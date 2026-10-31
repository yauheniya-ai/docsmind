# Changelog

All notable changes to this project will be documented in this file.

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
- `scripts/bundle_frontend.py` helper to copy `../frontend/dist` into the package
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
