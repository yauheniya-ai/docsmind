# docsmind

[![PyPI version](https://img.shields.io/pypi/v/docsmind.svg)](https://pypi.org/project/docsmind/)
[![Python versions](https://img.shields.io/pypi/pyversions/docsmind.svg)](https://pypi.org/project/docsmind/)
[![Downloads](https://static.pepy.tech/badge/docsmind)](https://pepy.tech/project/docsmind)
[![Downloads / month](https://static.pepy.tech/badge/docsmind/month)](https://pepy.tech/project/docsmind)
[![License: MIT](https://img.shields.io/pypi/l/docsmind.svg)](https://github.com/yauheniya-ai/docsmind/blob/main/LICENSE)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#status)

Chat with your documents. Track what changed between versions, and why it matters.

`docsmind` ingests PDFs (and soon DOCX/text) into a version-aware Postgres/pgvector
store, and gives you an agent, **the Mind**, to analyze them. Built-in subagents
will perform specialized tasks.

> **Alpha.** Ingestion and inspection work today. Embeddings, chat and change analysis
> are designed and scaffolded but not implemented yet; see [What works today](#what-works-today).

## What works today

| | |
|---|---|
| ✅ PDF ingestion | Full text, stats, outline (from the PDF's own table of contents), page-based chunks, images |
| ✅ Postgres + pgvector storage | Local Docker, or any Postgres with pgvector (Neon, Supabase) |
| ✅ Inspection CLI | `versions`, `fulltext`, `outline`, `chunks` read back exactly what was saved |
| ✅ `docsmind doctor` | Checks database, blob storage and API keys, and tells you what to fix |
| ✅ Table extraction | Extract and store tables skipping repeating ones (e.g. headers or footers) |
| ✅ server + UI | FastAPI backend (`docsmind serve`) + React UI (`frontend/`): add documents, browse indexed content, image and table galleries |
| ✅ Chat agent (the Mind) | Routes each question, plans complex ones, searches/reads your documents with tools, delegates to subagents, and answers with verified `[S#]` citations. Streams to the UI over SSE; conversations are saved |
| 🚧 Image captioning | Images are extracted and stored; vision captioning is next |
| 🚧 Embeddings + hybrid search | Chunks are stored without embeddings for now |
| 🚧 Change analysis (diffra) | `docsmind diff` exists as a command, without an implementation yet |
| 🚧 DOCX/text parsers, S3/Supabase blob storage | Planned |

## Install

```bash
pip install docsmind
# or, with optional extras:
pip install "docsmind[all]"
```

Extras: `docsmind[openai]`, `docsmind[anthropic]`, `docsmind[server]` (FastAPI, serves the bundled UI),
`docsmind[mlflow]` (tracing/eval export). Requires Python 3.10+.

## Quickstart

### 1. Get a Postgres with pgvector

Pick one:

**A. Local Docker (recommended for trying it out)**
```bash
docsmind db up         # docker compose up -d, pgvector/pgvector image on port 5433
docsmind db upgrade    # applies migrations, creates the vector extension
```

**B. Neon or Supabase** (no Docker needed; both support pgvector)
```bash
# in .env:
# DOCSMIND_DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>/<db>
docsmind db upgrade
```
Extracted images are saved to local disk (`DOCSMIND_BLOB_ROOT`) for now. S3 and
Supabase Storage backends are planned.

Then check that everything is wired correctly:

```bash
docsmind doctor
```

Configuration is environment-driven (`DOCSMIND_*`, or a `.env` file). Copy
`.env.example` to `.env` to see every option. Chat needs an LLM key
(`DOCSMIND_LLM_PROVIDER` plus `DOCSMIND_ANTHROPIC_API_KEY` or `DOCSMIND_OPENAI_API_KEY`);
ingestion and browsing don't.

### 2. Ingest a PDF

```bash
docsmind ingest report.pdf --collection policies
docsmind ingest report_v2.pdf --collection policies --version 2025   # version label is optional
```

or from Python:

```python
from docsmind import Docsmind

dm = Docsmind()
version = dm.ingest("report.pdf", collection="policies")   # version defaults to today's date
print(version.id)
```

### 3. Look at what was saved

```bash
docsmind versions                       # every ingested version, with its id, word count and chunk count
docsmind fulltext <version-id>          # saved full text (--full for everything)
docsmind outline <version-id>           # outline from the PDF's table of contents
docsmind chunks <version-id> --page 12  # the chunk(s) saved for one page
```

You can also inspect the tables directly with `docsmind db psql`.

### 4. Browse it in the UI

```bash
pip install "docsmind[server]"
docsmind serve              # FastAPI backend on http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000`.

The React UI is bundled into the Python package and served directly by `docsmind serve`
from `docsmind/src/docsmind/server/ui`.

Clicking **+ Add Document** in the UI runs the same `docsmind ingest` pipeline on the
uploaded PDF. Selecting a document shows its saved full text/outline (**Content**), and
galleries of everything extracted from it (**Images**, **Tables**).

For contributors refreshing bundled UI assets before a release:

```bash
cd ../frontend
npm install
npm run build
cd ../docsmind
python scripts/bundle_frontend.py
```

### 5. Ask the Mind

In the UI, open **Workspace** and ask a question (needs `docsmind[openai]` or `docsmind[anthropic]`
plus an API key). From the terminal or Python:

```bash
docsmind chat "What are our data retention obligations?"   # one-shot
docsmind chat                                              # REPL, keeps the conversation
```

```python
result = dm.chat_result("What are our data retention obligations?")
print(result.answer, result.citations)
change_set = dm.compare(v1.id, v2.id)   # planned (diffra)
```

## How the Mind works

```
Question -> Orchestrator --simple--> Executor <-> Tools / Subagents -> Answer
                 |                       ^
                 +--complex--> Planner --+        Harness feeds every role:
                                                  context builder, prompt cache,
                                                  summarizer, memory
```

- **Orchestrator**: one cheap call that routes the question (`simple` or `complex`) and rewrites
  it as a standalone question using the conversation so far.
- **Planner** (complex only): a short list of steps the executor follows.
- **Executor**: a bounded tool loop (`search`, `grep`, `read_pages`, `get_outline`,
  `list_documents`, `delegate`). The last step forces an answer; tool errors go back to the
  model, not the user; old tool results are elided when the context grows.
- **Subagents** (`researcher`, `change_analyzer`): fresh context, a subset of the tools, run in
  parallel, cannot delegate further.
- **Harness**: deterministic code, not another agent. It orders the prompt stable-first so the
  prefix caches, summarizes long histories, and saves durable notes to memory after each answer.
- **Citations**: tools register passages as `S1, S2, ...`; the model cites `[S#]`; invented
  handles are dropped and the rest renumbered by first appearance. Document text is treated as
  untrusted data, never as instructions.
- **Search** is Postgres full-text (ranked) plus exact-text grep until embeddings land.

Tuning (all `DOCSMIND_*`): `AGENT_FAST_MODEL` (router/summarizer/memory model, defaults to
`LLM_MODEL`), `AGENT_MAX_STEPS`, `AGENT_SUBAGENT_MAX_STEPS`, `AGENT_MEMORY_ENABLED`,
`OPENAI_REASONING_EFFORT`, `OPENAI_BASE_URL`. See `.env.example`.

HTTP: `POST /api/chat` streams Server-Sent Events (`route`, `plan`, `tool_start`, `tool_end`,
`subagent_start`, `subagent_end`, `token`, `note`, `answer`, `error`, `done`);
`GET /api/conversations[/{id}]`, `DELETE /api/conversations/{id}`, `GET /api/agent`.

## How ingestion works

Ingestion is deliberately simple and predictable. There's no layout guessing, because
heuristics that work on one PDF break on the next.

- **Full text** is extracted for the whole document and saved as-is, with character,
  word and estimated token counts.
- **Outline** comes only from the PDF's own table of contents/bookmarks. If the PDF
  has none, the outline is empty. Structure is never guessed from fonts or numbering.
- **Chunks** are made per page for vector search: most pages (~3,000 characters) become
  one chunk, and unusually dense pages are split into ~3,500-character pieces with a
  100-character overlap. Chunks exist to find relevant passages by meaning, not to
  reconstruct structure.
- **Images** are extracted with their page and position, skipping tiny icons, and saved
  under a readable path: `images/<collection>/<title>/<version>-<id>/`.
  Visually repeated images (e.g. logo on every page) are de-duplicated by perceptual hash.
- **Tables** are detected with PyMuPDF and rendered as PNG snippets, saved under
  `tables/<collection>/<title>/<version>-<id>/`.

## CLI reference

| Command | What it does |
|---|---|
| `docsmind ingest <file> -c <collection> [-v <label>]` | Parse a PDF and save it |
| `docsmind versions` | List ingested versions with ids and stats |
| `docsmind fulltext <id> [--full]` | Print the saved full text |
| `docsmind outline <id>` | Print the saved outline |
| `docsmind chunks <id> [--page N] [--full]` | Print saved chunks |
| `docsmind delete <id> [-y]` | Delete one version and its derived chunks/images/tables |
| `docsmind db up` / `upgrade` / `psql` | Start local Postgres, apply migrations, open psql |
| `docsmind doctor` | Check your setup |
| `docsmind serve [--host] [--port] [--reload]` | Run the FastAPI backend for the `frontend/` UI |
| `docsmind chat [question] [-c <collection>]` | Ask the Mind once, or start a REPL that keeps the conversation |
| `docsmind diff` | Planned |

## Architecture

```
src/docsmind/
├── __init__.py            # exports Docsmind + key models only
├── client.py              # Docsmind facade (sync + async)
├── config.py              # pydantic-settings, env-driven
│
├── domain/                # pure data + interfaces, no I/O
│   ├── documents.py       # Document, DocumentVersion, Section, Chunk, Image, ParsedDocument
│   ├── changes.py         # Change, ChangeSet, ImpactAssessment
│   ├── citations.py       # Citation
│   ├── tables.py          # AgentTable, TableRevision
│   └── ports.py           # LLM, Embedder, VisionCaptioner, VectorStore, BlobStore, Parser
│
├── ingestion/
│   ├── pipeline.py        # parse -> save (embed step planned)
│   ├── parsers/           # pdf.py (PyMuPDF); docx, text planned
│   ├── chunking/          # page.py: page-based chunks with overlap
│   └── enrichment/        # caption.py (planned)
│
├── adapters/              # every concrete implementation, imported lazily
│   ├── storage/postgres/  # tables, engine, repository
│   ├── blobs/             # filesystem.py; s3, supabase planned
│   ├── llm/ embeddings/ vision/ tracing/   # planned
│
├── cli/                   # Typer app: ingest, versions, fulltext, outline, chunks, db, doctor
├── retrieval/             # keyword (full-text) search helpers; vector search planned
├── agent/                 # the Mind: orchestrator, planner, tool loop, tools, subagents, harness
└── server/                # FastAPI app + routes backing frontend/ (documents, chat SSE, conversations)

alembic/   tests/   examples/   docs/
```

The UI source lives one level up as a sibling project: `frontend/` (React + TypeScript +
TailwindCSS, see the repo-root `CLAUDE.md`). Release builds are bundled into
`src/docsmind/server/ui` and served by `server/app.py` at `/`. The UI talks to `server/`
over `/api/*`, and loads extracted images/tables straight from disk via `/blobs/*`.

## Artwork attribution

- Workspace 3D graph artwork inspiration: Angela Galliat,
  "Neural nervous system of a brain" (CodePen):
  https://codepen.io/agalliat/pen/vYGXJxQ

### Design notes

- **Ports and adapters, once.** `domain/ports.py` defines every external dependency as a
  `Protocol`. The core never imports `openai`, `anthropic` or `mlflow` directly. Only
  adapters do, and lazily, so a bare `pip install docsmind` doesn't pull in SDKs you're
  not using.
- **Text and images share one retrieval path.** An image becomes a chunk
  (`chunk_type = 'image'`) whose content will be its caption, embedded with the same
  embedder and stored in the same `chunks` table. One index, one citation type.
- **Version-aware schema.** Every chunk, image and outline entry belongs to a
  `DocumentVersion`, so results can be filtered and cited "as of" a specific revision.
  Outline paths (e.g. `03.13.11`) give version comparison something stable to align on
  where a document has a table of contents.
- **Tables are shared state, not a UI feature.** `AgentTable` is a domain object that
  both the agent and the user write to (append-only revisions). This is distinct from
  the `tables` storage table (extracted PDF table *regions*, rendered as images
  alongside `images` — browsable read-only in the UI's **Tables** gallery).

## Development

```bash
git clone https://github.com/yauheniya-ai/docsmind
cd docsmind
uv sync --all-groups --all-extras   # or: pip install -e ".[dev,all]"
docsmind db up && docsmind db upgrade
uv run pytest
```

`requires-python = ">=3.10"` is a floor, not a target. `uv sync` will use 3.12 (see
`.python-version`), and CI (`.github/workflows/test.yml`) runs the tests across
3.10, 3.11 and 3.12 so the floor is actually verified.

To try the parser on a PDF without a database:

```bash
python examples/parse_pdf_demo.py path/to/file.pdf
```

## Status

`0.x`: the public API (`Docsmind`, the domain models) may still change. Semantic
versioning applies from `1.0`. See the [changelog](CHANGELOG.md) for what's in each release.

## License

MIT
