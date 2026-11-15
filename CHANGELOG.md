# Changelog

All notable changes to this project will be documented in this file.

## [0.10.0] - 2026-11-15

### Added

- The Text tab can show the normalized text (one sentence per line, headers, footers and page
  numbers removed) that the change analysis diffs. It is saved at ingest in
  `document_versions.normalized_text` (migration 0006; run `docsmind db upgrade`) and served by
  `GET /api/documents/{version_id}/normalized-text`, which backfills older versions.
- The sidebar can be collapsed to give the main content more room.

### Changed

- The embeddings and image-caption warnings now appear in the Chunks and Images tabs instead of
  above all tabs.
- The local Docker Postgres listens on port 5434 (was 5433) to avoid clashing with other
  containers. `.env.example` no longer points at 5432.
- New icon on the "Table & report" button.

### Fixed

- PDF preview in the browser: the PDF.js `build/` folder was excluded by `.gitignore` and
  missing from clones.

## [0.9.0] - 2026-11-13

### Added

- Edit a document after ingest: the document page lets you change the title, the collection and
  the version label, which are normally set at ingest. New route
  `PATCH /api/documents/{version_id}` (any subset of `title`, `collection`, `label`; empty
  values are rejected with 422, an unknown version with 404) and
  `PostgresRepository.update_version_metadata`. Title and collection are kept on the document,
  the label on the version.
- The Agents diagram shows the Change Analyzer's pipeline as stages (versions check, scan,
  changelog search, row verification, report). Each stage opens a details panel with its
  description, its prompt, the model it runs on (main, fast or scan), its limit and the tools
  its loop may call. Backed by `StageSpec` and `SubagentSpec.stages` in
  `agent/subagents/specs.py`.
- The frontend source (`frontend/`) now lives in this repository, so a clone is enough to
  continue work on the UI on another machine (`cd frontend && npm install && npm run build`).

### Changed

- The planner is no longer limited to 2-6 steps: the "2 to 6 steps" rule and the "final step is
  always about synthesising the answer" rule are gone from its prompt, `MAX_STEPS` is gone from
  the code, and the plan call may use up to 2,000 output tokens (was 500) so a longer plan is
  not cut off.
- A change table keeps the titles of its two documents (`current_title`, `previous_title`) with
  the analysis, so the Excel "Info" sheet, the file names and the report keep the title the
  documents had when they were compared.


## [0.8.0] - 2026-11-12

### Added

- `DOCSMIND_ANTHROPIC_BASE_URL` next to the existing `DOCSMIND_OPENAI_BASE_URL`, so a key plus a
  gateway/proxy URL works for both providers (chat, vision, embeddings). An empty value means the
  default endpoint. `docsmind doctor` shows the base URL in use. `.env.example` lists keys and
  base URLs together.

- Change Analyzer rebuilt as a code-driven pipeline (`agent/subagents/change_analysis/`) that the
  Mind starts with `delegate`. Given the current and the previous version, it:
  1. normalizes both texts and diffs them in code (headers, footers and page numbers removed);
  2. checks that they are versions of one document and which is newer (a small model call);
  3. scans for changes with one long-context call per part, streaming rows into the table;
  4. verifies every compliance-relevant row (new, modified or removed, normative, with a
     requirement) in a small tool loop, and re-checks the quoted proof against the stored text
     in code: passages must be verbatim, pages must hold them, and a "new" or "removed" claim
     must be absent from the other version. A check that goes wrong (no usable answer, an error,
     a proof that is not in the text) is started again, up to `CHANGE_VERIFY_MAX_RETRIES` times.
     Rows without proof are dropped: they are not in the table, the report or the database;
  5. searches for a changelog in parallel (in the documents, and online for public ones) and
     checks the entries that no row explains;
  6. writes a report: narrative from the model, every countable section built by code. Page
     references in it ("A p.12") are clickable and open that page of that version.
- After a comparison the Mind answers in one or two short sentences: the Change Analyzer finished,
  built a detailed table and a report that open next to the chat, plus one overall judgement
  composed in code. It no longer re-describes the changes or adds its own page sources; the
  Analyzer hands back a short status instead of the report (`DOCSMIND_CHANGE_REPORT_MAX_CHARS` is gone).
- Change tables are stored (migration `0005`: `change_analyses`, `change_rows`) with a verified
  flag, proof passages with pages and a human approved flag. Run `docsmind db upgrade`.
- Routes `/api/changes`: read an analysis, add, edit and delete rows, Excel export.
  The file is named `yyyymmdd-<title of document A>-change-table.xlsx`, the header rows use the
  accent color with white text, and a second sheet "Info" lists the two documents, date,
  duration, tokens, cost and the model used for each stage.
- The report downloads on its own as Word (`.docx`) or Markdown (`yyyymmdd-<title>-change-report.*`),
  with the title "Change analysis", the two documents and what was compared in front. The panel
  shows Excel on the Table tab (table and Info) and Word / Markdown on the Report tab; the report
  view starts with the same title and a normal-size "what is compared" paragraph instead of the
  small gray italic note. New dependency: `python-docx`.
- `POST /api/changes/{id}/verify` and `DocsmindAgent.verify_rows`: a person runs the Change Analyzer's
  verification directly on any row of the table (star in the row, "Verify" in the row details, or
  the footer button), not through the Mind. A row it cannot prove stays in the table, unverified,
  with the reason. The panel no longer says "Ask the Mind to verify", and a verified row shows
  "Verified" instead of a verify button.
- The approval box uses the accent color and asks "Approve this row? Yes / No" first.

### Fixed

- A proof's page is looked up in the stored text when the verifier gives none or a wrong one, so
  proofs carry "A p.4" and open the document at that page.
- Tools: `page_vision` (a vision model reads a page image), `load_table` (xlsx/csv from a URL,
  parsed in code) for the Change Analyzer; `export_excel` for the Mind.
- `docsmind diff` now prints the table and report; `--actionable` and `--xlsx` options.
- Config: the `change_*` settings (see `.env.example`). Dependency: `openpyxl`.

### Changed

- `compare()` / `acompare()` return a `ChangeResult` (message, analysis, rows). The two versions
  may be given in either order.

### Removed

- The `researcher` subagent (code, prompt, tests and UI). `change_analyzer` is now the only
  subagent.
- The generic subagent loop and `agent_subagent_max_steps`; the Change Analyzer has its own limits.


## [0.7.0] - 2026-11-12

### Added

- Change Analyzer can now look for a published changelog online when the documents themselves
  contain none, using two new subagent-only tools:
  - `web_search`: searches the web via Tavily, optionally limited to given domains. Only offered
    when `DOCSMIND_WEB_SEARCH_API_KEY` is set.
  - `fetch_url`: reads a public web page as text. Hardened for model-supplied URLs (http(s) on
    ports 80/443 only, no credentials, no private/loopback addresses on any redirect hop,
    bounded size and time).
- Config: `web_search_api_key`, `web_timeout_s`, `web_fetch_max_bytes` (documented in
  `.env.example`).
- Implementation brief for the Change Analyzer (`docs/`) and unit tests for the web tools.

### Changed

- Change Analyzer prompt and tool list updated to use the web tools as a fallback, preferring
  the publisher's own domain.
- Rebuilt the bundled frontend UI assets.

## [0.6.0] - 2026-11-10

### Added

- Workspace: clicking a source card or citation chip opens the cited PDF page in a side panel
  next to the chat.
- Images/Tables galleries: full-view lightbox (blurred backdrop, arrow-key navigation) with
  the full image caption beneath.
- Chunk preview shows the embedding vector on one truncated line, expandable to the full
  vector (`GET /api/documents/{version_id}/chunks/{chunk_id}/embedding`; chunk list now
  includes `embedding_model`, `embedding_dims`, `embedding_preview`).
- Agents: clicking a node opens a details panel next to the graph, like the source panel in the
  Workspace. For an agent (the Mind, subagents, router and planner) it shows the description,
  model, step budget, tools, the full system prompt (formatted or raw, with copy), the context
  blocks the harness adds and a description of the user prompt. For a tool it shows the
  description the model reads, which agents can use it and its arguments (type, required,
  default, constraints) with the JSON Schema. Tool chips and "Available to" chips navigate
  between panels; Escape or clicking the empty graph closes it.

### Changed

- `GET /api/agent`: every agent and stage now includes `system_prompt`, `context` (blocks with
  `label`, `text`, `cached`) and `user_prompt`; every tool now includes `instructions` (the
  description sent to the model) and `parameters` (its JSON Schema), in addition to `name` and
  `description`.

### Fixed

- Image captions are now shown in the gallery and refresh when a captioning job finishes.
- Captioning prompt no longer marks logos and banners as "decorative".

## [0.5.0] - 2026-11-02

### Changed

- Agents view is now an interactive graph: the Mind in the centre, its tools around it, and the
  subagents with their own tools beyond. Hover (or tap) a node for its description. Names are
  shown as "Change Analyzer", not `change_analyzer`; the router and planner appear as dashed
  stages of the Mind (one model call, no tools), not as subagents.
- `GET /api/agent`: `tools` are now `{name, description}` objects (plain-language summaries),
  and a new `stages` list describes the router and planner.

### Added

- On-demand enrichment for both pending roadmap items:
  - Embeddings: estimate by token count, confirmation step, resumable run (`docsmind embed`,
    `POST /api/enrichment/embeddings/{estimate,run}`).
  - Image captioning: estimate by image count/resolution + output-token range, confirmation step,
    resumable run (`docsmind caption`, `POST /api/enrichment/captions/{estimate,run}`).
- Enrichment status + background jobs API:
  `GET /api/enrichment/status`, `GET /api/enrichment/jobs/{job_id}`,
  `POST /api/enrichment/jobs/{job_id}/cancel`.
- Frontend Document details now shows "not embedded yet" / "not captioned yet" alerts with
  estimate-and-proceed dialogs and job progress/cancel controls.
- Hybrid retrieval path: keyword search fused with vector similarity when embeddings are present,
  with automatic fallback to keyword-only when embeddings or API access are unavailable.

## [0.4.0] - 2026-10-31

### Added

- The Mind: a chat agent (orchestrator, planner, tool-using executor, parallel subagents)
  with a deterministic harness (context builder with prompt-cache breakpoint, summarizer,
  memory writer). Answers carry verified `[S#]` citations.
- Provider-neutral LLM layer with OpenAI and Anthropic streaming adapters.
- Tools: `search`, `grep`, `read_pages`, `get_outline`, `list_documents`, `delegate`;
  subagents: `researcher`, `change_analyzer`.
- Migration `0004`: `conversations`, `messages`, `agent_memory` tables (run `docsmind db upgrade`).
- HTTP: `POST /api/chat` (SSE), `GET/DELETE /api/conversations`, `GET /api/agent`.
- UI: streaming chat in Workspace with live agent activity, markdown answers, citation chips
  and source cards that open the PDF at the cited page; Activity lists saved conversations;
  Agents reads the real registry. New frontend deps: `react-markdown`, `remark-gfm`.
- `Docsmind.chat_result()`; `chat`/`achat` accept `conversation_id`; `docsmind chat` REPL keeps
  the conversation.
- Config: `agent_*` settings, `openai_base_url`, `openai_reasoning_effort`.

## [0.3.0] - 2026-10-03

### Added

- Bundled frontend UI in the Python package: release UI assets are copied into
  `src/docsmind/server/ui` and served by `docsmind serve` at `/`.
- `scripts/bundle_frontend.py` helper to copy `frontend/dist` into the package
  before publishing.
- `docsmind delete <version-id> [-y]` command to remove one ingested version and
  its derived sections/chunks/images/tables.
- HTTP `DELETE /api/documents/{version_id}` endpoint.

### Changed

- `docsmind serve` now serves the bundled SPA (when present), in addition to
  `/api/*` and `/blobs/*`.
- Ingestion now checks `content_hash` before parsing and raises a friendly
  "already ingested" error instead of surfacing a raw Postgres unique-constraint
  failure.

### Fixed

- Image extraction de-duplicates repeated embedded images (e.g. page logo reused
  across many pages) using perceptual hash, preventing inflated image counts.
- API duplicate-ingest responses now return HTTP 409 with structured details.



## [0.2.0] - 2026-09-27

First functional release: documents can now be ingested from PDF into
Postgres/pgvector and inspected from the command line. Chat, embeddings and
change analysis are scaffolded but not implemented yet (see "Not yet
implemented" below).

### Added

- **PDF ingestion pipeline** (`docsmind ingest <file> --collection <name>`),
  built on PyMuPDF:
  - Full document text is extracted and saved per version, together with
    stats (character, word and estimated token counts).
  - Outline is taken from the PDF's own table of contents/bookmarks when one
    exists. Documents without a TOC get an empty outline; structure is
    deliberately not guessed from fonts or numbering.
  - Text is chunked by page for vector search: one chunk per page, with
    unusually dense pages split into ~3,500-character pieces with a
    100-character overlap.
  - Images are extracted with their page and bounding box, skipping tiny
    icons, and stored with a perceptual hash for future de-duplication. They
    are saved under a readable path
    (`images/<collection>/<title>/<version>-<id>/`) instead of a bare UUID.
- **Postgres storage** with pgvector: Alembic migrations for documents,
  versions, sections, images, chunks (with a generated `tsvector` column for
  keyword search) and editable agent tables with revision history. The vector
  index uses `halfvec` so 3072-dimension embeddings
  (`text-embedding-3-large`) fit within pgvector's HNSW limit.
- **Inspection commands**: `docsmind versions`, `docsmind fulltext`,
  `docsmind outline` and `docsmind chunks` read back what was saved, so
  results can be checked without writing SQL.
- **Database commands**: `docsmind db up` (local Docker Postgres with
  pgvector), `docsmind db upgrade` (apply migrations) and `docsmind db psql`.
- `docsmind doctor` checks the database URL, blob storage and API keys, and
  detects Neon/Supabase connection strings.
- `Docsmind` / `AsyncDocsmind` Python facade for ingesting documents.
- Domain models for documents, versions, sections, chunks, images, citations,
  change sets and editable tables, with `Protocol` ports for LLMs, embedders,
  vision captioners, vector stores, blob stores and parsers.
- Environment-driven configuration (`DOCSMIND_*` variables, `.env` support)
  and a documented `.env.example`.
- Filesystem blob storage for extracted images.
- GitHub Actions: a test workflow across Python 3.10, 3.11 and 3.12 with a
  wheel install check, and a publish workflow using PyPI trusted publishing.
- Unit tests for the PDF parser and page chunker, a runnable parser demo
  (`examples/parse_pdf_demo.py`) and a synthetic test-PDF generator.
- `.gitignore`, `docker-compose.yml` and `.python-version`.

### Changed

- `--version` on `docsmind ingest` is now optional and defaults to today's
  date.
- The local Docker Postgres listens on port 5433 instead of 5432 to avoid
  clashing with a locally installed Postgres.
- Package metadata: author email, explicit Python 3.10-3.12 classifiers and
  the issue tracker URL.

### Removed

- Heading detection from font size, boldness and numbering patterns, and
  section-based chunking. Neither generalised across real-world PDFs, and
  chunking no longer depends on document structure.

### Not yet implemented

- Embeddings and hybrid vector/keyword search. Chunks are stored without
  embeddings, and `hybrid_search` raises `NotImplementedError`.
- Image captioning with a vision model.
- The chat agent behind `docsmind chat`.
- DOCX and plain-text parsers, S3/Supabase blob storage, and the server/UI.
- Multi-column reading order: PDFs with genuine multi-column layouts may have
  their text interleaved in the saved full text.

## [0.1.0] - 2026-09-12

### Added

- Initial release to reserve the package name on PyPI.
