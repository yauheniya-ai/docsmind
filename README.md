# docsmind

[![PyPI version](https://img.shields.io/pypi/v/docsmind.svg)](https://pypi.org/project/docsmind/)
[![Python versions](https://img.shields.io/pypi/pyversions/docsmind.svg)](https://pypi.org/project/docsmind/)
[![Downloads](https://static.pepy.tech/badge/docsmind)](https://pepy.tech/project/docsmind)
[![Downloads / month](https://static.pepy.tech/badge/docsmind/month)](https://pepy.tech/project/docsmind)
[![Tests](https://github.com/yauheniya-ai/docsmind/actions/workflows/test.yml/badge.svg)](https://github.com/yauheniya-ai/docsmind/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/yauheniya-ai/<GIST_ID>/raw/docsmind-coverage.json)](https://github.com/yauheniya-ai/docsmind/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/pypi/l/docsmind.svg)](https://github.com/yauheniya-ai/docsmind/blob/main/LICENSE)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#status)

Chat with your documents. Track what changed between versions, and why it matters.

`docsmind` ingests PDFs (and soon DOCX/text) into a version-aware Postgres/pgvector
store, and gives you an agent, **the Mind**, to chat with them. A built-in subagent,
**diffra**, will do change analysis between document versions: what changed, where, and
its compliance implications, with citations back to exact pages.

> **Alpha.** Ingestion and inspection work today. Embeddings, chat and change analysis
> are designed and scaffolded but not implemented yet; see [What works today](#what-works-today).

## What works today

| | |
|---|---|
| ✅ PDF ingestion | Full text, stats, outline (from the PDF's own table of contents), page-based chunks, images |
| ✅ Postgres + pgvector storage | Local Docker, or any Postgres with pgvector (Neon, Supabase) |
| ✅ Inspection CLI | `versions`, `fulltext`, `outline`, `chunks` read back exactly what was saved |
| ✅ `docsmind doctor` | Checks database, blob storage and API keys, and tells you what to fix |
| 🚧 Embeddings + hybrid search | Chunks are stored without embeddings for now |
| 🚧 Image captioning | Images are extracted and stored; vision captioning is next |
| 🚧 Chat agent (the Mind) | `docsmind chat` exists as a command, without an agent behind it yet |
| 🚧 Change analysis (diffra) | `docsmind diff` exists as a command, without an implementation yet |
| 🚧 DOCX/text parsers, S3/Supabase blob storage, server + UI | Planned |

## Install

```bash
pip install docsmind
# or, with optional extras:
pip install "docsmind[all]"
```

Extras: `docsmind[openai]`, `docsmind[anthropic]`, `docsmind[server]` (FastAPI, planned UI),
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
`.env.example` to `.env` to see every option. Model API keys aren't needed yet;
they'll be required once embeddings and chat land.

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

### 4. Coming next

```python
print(dm.chat("What are our data retention obligations?"))   # planned
change_set = dm.compare(v1.id, v2.id)                        # planned (diffra)
```

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

## CLI reference

| Command | What it does |
|---|---|
| `docsmind ingest <file> -c <collection> [-v <label>]` | Parse a PDF and save it |
| `docsmind versions` | List ingested versions with ids and stats |
| `docsmind fulltext <id> [--full]` | Print the saved full text |
| `docsmind outline <id>` | Print the saved outline |
| `docsmind chunks <id> [--page N] [--full]` | Print saved chunks |
| `docsmind db up` / `upgrade` / `psql` | Start local Postgres, apply migrations, open psql |
| `docsmind doctor` | Check your setup |
| `docsmind chat`, `docsmind diff` | Planned |

## Architecture

```
src/docsmind/
├── __init__.py            # exports Docsmind + key models only
├── client.py              # Docsmind facade (sync + async)
├── config.py              # pydantic-settings, env-driven
│
├── domain/                # pure data + interfaces, no I/O
│   ├── documents.py       # Document, DocumentVersion, Section, Chunk, Image, ParsedDocument
│   ├── changes.py         # Change, ChangeSet, ImpactAssessment (diffra)
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
├── retrieval/             # hybrid vector + keyword search (planned)
├── agent/                 # the Mind + diffra subagent (planned)
└── server/                # FastAPI + UI (planned)

alembic/   tests/   examples/   docs/   ui/
```

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
  both the agent and the user write to (append-only revisions).

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
