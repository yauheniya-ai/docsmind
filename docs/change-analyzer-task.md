# Task: make the Change Analyzer a real agent on a persistent table

Status: **implemented** (sections 13 steps 1 to 8). See "Implementation notes" at the end for where the code
differs from this plan. Not yet done: a run with the real models (the tests use scripted models), and rebuilding
the UI bundle in `src/docsmind/server/ui` (`cd frontend && npm run build`).

## 0. Goal and what was checked

Goal: the Change Analyzer holds one change table per document pair. The user keeps working on it
in chat ("review chapter 3 again", "is row 14 really normative?", "add what the changelog
says about X"), and the agent responds by improving and validating that table with tools. It does not
relaunch a fixed pipeline with a new set of findings.

The task text was checked against the code (`agent/subagents/change_analysis/*`, `agent/loop.py`,
`agent/tools/*`, `pricing.py`, `change_store.py`, `Agents.tsx`). Findings that change the scope:

| Task claim | What the code shows | Consequence |
|---|---|---|
| Staged pipeline in fixed order (1) | Confirmed. `ChangeAnalyzer._compare` runs prepare → decide → scan ∥ changelog → verify → gaps → report in code. The only tool loops are changelog search and per-row verification. | Replace the orchestration, keep the building blocks (section 3). |
| Table is the source of truth (5) | Already true: `change_rows` + `ChangeStore`, UI edits, Excel export. But **only code writes rows**; the agent has no table tools, and `verify_row` failures **hard-delete** rows. | The gap is table tools, not a table. |
| Persist per pair, keyed by content hash (10.1) | Tables persist in Postgres, outside the session. But (a) every run creates a **new** `change_analyses` row and nothing looks up an existing one, and (b) the analysis is tied to version ids with `ON DELETE CASCADE` (migration 0005), so **deleting a document version currently deletes its analyses**. That contradicts the intent: analyses stand on their own. | Key the analysis by the hash pair, never delete it with a document, re-link on re-ingest (section 8). Add lookup and reuse. |
| Cost knows only input and output (9) | Partly wrong. `Usage` already has `cached_input_tokens`, and both LLM adapters fill it (Anthropic: cache read; OpenAI: `cached_tokens`). What is missing: cache **write** tokens, a price per cache class, and `cost_breakdown` ignores the cached field. | Smaller than described (section 8). |
| Shared tool definitions with per-agent profiles (7) | Tools are already defined once (`build_registry`) and subset per agent by name. Scope already comes from the `Catalog` passed in. What blocks sharing: `web_search`, `fetch_url`, `page_vision`, `load_table` are flagged `subagent_only`, so the Mind cannot use them. | Remove the flag, add tiers (section 4). |
| fetch_url cannot open PDF/Word (3) | Confirmed: `fetch_bytes(allowed=TEXT_TYPES)` rejects everything but HTML/plain text. `pymupdf` and `python-docx` are already dependencies. `page_vision` works only on **ingested** documents (blob URI of a catalog handle), not on a downloaded file. | PDF/docx reader plus a vision fallback for downloaded files (section 6). |
| Tool limits per step (3b) | Confirmed: `VERIFY_TOOLS`, `CHANGELOG_*_TOOLS` in `specs.py`. | Removed with the stages. |
| Router and planner should be revised (8) | `ROUTER` returns `simple`/`complex`; `complex` → `PLANNER`, which lists `delegate`. `delegate` takes `documents` or `analysis_id`+`row_ids`, `task` is "optional notes". | New route and a request-based `delegate` (section 7). |

Two things in the task text conflict and are resolved here:

- Section 4 says the workflow is "only a hint", but section 10 requires reconciliation and a
  verification gate. These are **invariants enforced in code**, not hints (section 5). Only the
  *order and choice of steps* is left to the agent.
- Section 2 lists "Full scan" and "Diff scan" as two advanced tools. Today the mode is chosen by the 25%
  threshold and the model never needs to choose. One `scan_documents` tool with `mode: auto|diff|full`
  avoids a decision the agent has no basis for.

## 1. Architecture

One agent, `change_analyzer`, running the existing `run_tool_loop` (`agent/loop.py`) with its own system
prompt and the tool set below. No staged `_compare`.

```
Mind --delegate(documents, request)--> Change Analyzer (agent loop)
                                         |-- core tools      (search, grep, read_pages, get_outline, read_url, ...)
                                         |-- table tools     (get_table, add_row, update_row, archive_row, restore_row)
                                         |-- advanced tools  (check_versions, scan_documents, find_changelog,
                                         |                    verify_findings, write_report)
                                         v
                              persistent table (change_rows) + archive + runs + report
```

- The agent's own context stays small: tools return **compact results** (counts, short row handles,
  one-line descriptions), never the whole table or documents. Full rows come from `get_table` with
  filters and pages.
- An analysis **outlives its documents**. If a document was deleted, `get_table`, `get_row`, `write_report`,
  archive/restore, `create_file` and the UI keep working; tools that need the text (`scan_documents`,
  `verify_findings`, `find_changelog` against the documents, `search`/`read_pages` on that handle) return "document
  not in the register; ingest it again (same file, same hash) to continue". Re-ingesting the same file re-attaches it.
- The agent has **no memory of its own across chat turns**. Continuity is the table, the archive, the
  report and the run history. On every call it starts by loading the table for the pair.
- Step budgets, on two separate levels. (1) The agent's own loop: `change_agent_max_steps` (default 25 per
  request; one step is one model turn, parallel tool calls in a turn count once, so `verify_findings` on 30
  rows is one step), hard stop with a final turn without tools, as in the Mind's loop. (2) Loops inside tools keep
  their own limits as today: `change_verify_max_steps` (8 per row verification), `change_changelog_max_steps`
  (8), scan retries. Parallel tool calls are allowed (`agent_max_parallel_tools`).
- Streaming to the UI keeps the existing events (`analysis_start`, `change_row`, `change_row_update`,
  `change_row_removed`, `analysis_done`), now emitted by the tools. `analysis_status` phases are replaced
  by the normal `tool_start`/`tool_end` trace.

## 2. What is kept and what is replaced

| Module | Fate |
|---|---|
| `prep.py` (normalize, diff, split into parts) | Kept as the code inside `check_versions` and `scan_documents`. Normalized text is already stored in `document_versions.normalized_text`; use it instead of recomputing. |
| `rows.py` (`checked()`, JSON parsing) | Kept. Every write path goes through `ChangeRow.checked()`. |
| `scan.py` (parts, resume after cut-off) | Kept inside `scan_documents`; writes to **staging** (section 5) and saves progress per part so an interrupted scan continues. |
| `verify.py` (proof re-checked verbatim in code) | Kept as `verify_findings`; this is the gate. Rejected rows are archived, not deleted. |
| `changelog.py` | Kept inside `find_changelog` (finder loop + `match_entries`). |
| `report.py` | Kept inside `write_report` (LLM narrative + code-built sections). |
| `analyzer.py` `_compare`, `_decide`, `_verify_all`, `_changelog_gaps`, `StageSpec`s | **Replaced** by the agent loop; the gap check becomes a hint in the prompt plus `verify_findings` on changelog entries. |
| `verify_existing`, `ChangeAnalyzer.compare` | Stay as thin entry points (UI "Verify" button, `docsmind diff`) that call the same tool functions or run the agent with a default request. |

## 3. Tool tiers and the registry

Each tool is defined once (`Tool` subclass) and gets two new class attributes: `tier = "core" | "table" | "advanced"`
and an optional per-agent **profile** (`ToolProfile(scope, prompt_overlay, model)`) in the agent's spec. Nothing
else is shared state. `subagent_only` is removed.

**Core tools** (deterministic, offered to the Mind and the Change Analyzer):
`search`, `grep`, `read_pages`, `get_outline`, `list_documents`, `load_table`, `web_search`, `read_url`
(the renamed `fetch_url`, section 6), `page_vision`, `create_file` (below). Scope per agent: the `Catalog` (Mind: all documents;
Change Analyzer: the pair).

**`create_file`: write results as a downloadable file.** Today `export_excel` can only export a stored change
table. The Mind and the Change Analyzer should be able to put *any* result into a file for the user: a table of
search results, a comparison across documents, a summary as Markdown. That is a deterministic building block
without a model, so it is a core tool. The UI's "Export" button stays a UI function: it keeps calling the REST
routes (`/changes/{id}/export.xlsx`, `report.{docx,md}`), which share the writer code with the tool.

| Argument | Meaning |
|---|---|
| `filename` | Base name only; the extension comes from `format`, the name is slugged and given a short random suffix. |
| `format` | `txt`, `md`, `csv` or `xlsx`. |
| `content` | The text, for `txt` and `md`. |
| `columns`, `rows` | For `csv` and `xlsx`: header cells and rows of strings, numbers, booleans or null. |
| `sheets` | For `xlsx` with several sheets: a list of `{name, columns, rows}`. |
| `source` | Instead of `rows`: `{change_table: analysis_id, only: actionable, natures: [...]}` writes the stored change table exactly as the UI export does (8 columns, Info sheet), so up to hundreds of rows never pass through the model's context. |

Behavior: files go to `blob_root/exports/` and the tool returns a markdown download link (`/blobs/exports/<name>`),
as `export_excel` does today, so nothing changes in the chat UI and the link stays in the saved conversation.
Limits: 5 MB per file, 50,000 model-supplied cells (`create_file_max_cells`). `xlsx` reuses the existing styling
of `rows_to_xlsx` (header row, widths); `csv` is UTF-8 with BOM so Excel opens it correctly. **Spreadsheet formula
injection:** a cell that starts with `=`, `+`, `-` or `@` is stored as text. Wrong shapes (`txt` with `rows`, `csv`
without `columns`) return an error that says what to pass.

**Table tools** (Change Analyzer only; rows are addressed as `R<ordinal>`, stable, never reused):

| Tool | Input | Effect / output |
|---|---|---|
| `get_table` | `status` (active default, archived, candidate), `nature`, `actionable_only`, `verified`, `clause_contains`, `offset`, `limit` | Compact rows (handle, clause A, nature, binding, one-line description, verified flag); counts per nature at the top. |
| `get_row` | `row` | Full row with proof. |
| `add_row` | row fields | `checked()`, stored `active`, `origin="agent"`, **unverified**. Used for what the user asks to add. |
| `update_row` | `row`, fields | Edits; clears `verified` and proof if clause, nature, requirement or binding changed. |
| `archive_row` | `row`, `reason` | Moves out of the main table. |
| `restore_row` | `row` | Back to active. |

**Advanced tools** (Change Analyzer; each can use its own model via config):

| Tool | Model | What it does |
|---|---|---|
| `check_versions` | fast | Today's `_decide` as a tool: verdict (versions / unrelated / changelog), which is newer, public or not, diff ratio. Creates or loads the analysis for the pair. Identical or unrelated documents return a plain-words verdict and no table. |
| `scan_documents(mode=auto, focus=None)` | scan (long context) | **The internal first pass, once per pair.** One long-context call per part over A plus B or A plus the diff; fast, broad and not deterministic. Writes candidates into **staging**; non-actionable ones join the table when the scan finishes. Refused for a pair with a finished scan; called again only after a failure or crash, it continues from the saved rows. |
| `find_changelog(query=None)` | main | Today's finder loop; stores entries on the analysis, returns a summary and which entries have no row. |
| `verify_findings(rows or candidates, parallel)` | main (tool loop per item) | Runs the proof check. Verified candidates are promoted to active; rejected candidates go to the archive with the reason; verified active rows get proof; unproven **active** rows stay active, unverified, with a note (user rows are never removed by verification). |
| `write_report(focus=None)` | main | Writes the report from the current active table; marks the report with the table revision it covers. |

The Mind additionally keeps `delegate`. `export_excel` is replaced by the core tool `create_file`.

## 4. System prompt of the Change Analyzer

The prompt carries: the role (comparing versions of one document), the table definitions (columns, nature and
binding rules, "A is current, B is previous"), the **typical order** as a hint (check versions → load the
table → changelog and scan → verify → report), how to write a report, and the rules below. The agent may
deviate: skip steps, repeat them, run them in parallel, answer a question about a row without touching
anything.

Rules the prompt states (and section 5 enforces in code where it can):

1. Always load the table for the pair first. Never start from an empty table when one exists.
2. A repeat request for the same pair is answered from the existing table and report. Say when the analysis was done.
3. **The user never asks for a scan**: they ask for a comparison and want one table they can rely on. The scan is an internal first pass that builds the foundation of that table, run once per pair. Never scan again to "check again" or "redo": say that it was analyzed on <date>, then find out what the user wants (a chapter in depth, a row to verify, the changelog, a row to add) and improve the table with research and `verify_findings`. Scan again only after a failure or crash midway (it continues from the rows saved so far). Never mention scans to the user.
4. Ask for verification of new findings before telling the user about them.
5. The report always reflects the table. Code rewrites it when the request ends if rows changed; the agent never tells the user that a report is outdated and calls `write_report` only to focus it or when asked.

## 5. Invariants enforced in code

These do not depend on the agent following instructions:

1. **Scan never writes to the main table.** `scan_documents` writes `status="candidate"` rows. Only
   `verify_findings` can promote a candidate to `active`.
2. **A scan that failed is continued, not repeated.** `scan_documents` marks the scan unfinished before its first model call and saves progress after every part. After a failure or crash a new call skips the finished parts and gives the model the rows saved so far ("continue after the last row"); rows are never stored twice. A finished scan is refused.
3. **Verification gate.** Candidates are promoted only if `verify_findings` proves them (verbatim proof checks of `verify.py`). A rejected candidate is archived with the reason, so it is not verified again.
4. **A scan never removes an active row.** Only verification of that row or a user action archives it.
5. **No hard deletes of findings.** `delete_row` becomes archive (`reason="deleted by user"`); the UI delete and the agent both archive. Hard deletion only cascades from deleting the analysis.
6. **No repeated rows.** A new row (from the changelog check or `add_row`) is added only after it was compared with every row of the table, active, pending and archived (`admit.py`): code catches the same clause title and kind of change, a small model reads the full list of clauses for the rest. A refused row is not added; its proof, if the existing row lacks it, goes to the existing row.
7. **Every write goes through `ChangeRow.checked()`** and the store's lock (ordinals are assigned on insert).
8. **The report is never behind the table.** Any report-relevant row change (not `confirmations` or `approved`) after a report was written sets `report_stale=true` internally. Code clears it by rewriting the report: at the end of every agent request, when the Report tab is opened after edits in the panel (`POST /changes/{id}/report/refresh`), and before a Word or Markdown download. The flag is never shown to the user as "outdated"; a rewrite keeps the effort and cost figures of the analysis.

## 6. Readers: PDF, Word, scanned pages (section 3 of the original task)

`fetch_url` is renamed `read_url` (the old name stays as an alias for one release) and becomes a core tool.

- Download with `fetch_bytes(allowed=None)`, same SSRF checks, size and time limits.
- Content type decides the extractor: HTML (today's), plain text, **PDF** (`pymupdf`, text per page, keeps page numbers), **.docx** (`python-docx`: paragraphs and tables, headings kept). Unknown types: clear error naming the type.
- Long results come in parts with `offset`, as now; PDF results are labelled `[page N]`.
- **Vision fallback:** if a PDF yields less than `read_url_min_chars_per_page` (default 100) on a page, the result says "page N has no readable text; call read_url with `vision_pages=[N]` and a `question`". With `vision_pages`, the tool renders those pages of the already downloaded file (bytes cached in `ToolContext.cache` per URL) and sends them to the vision model. `page_vision` itself stays for ingested documents.
- **Failure handling is behavioral:** the tool error text names alternatives (another URL, `web_search`, `vision_pages`); the prompt says "never drop a source silently: if a source cannot be read, say so in the report".
- `find_changelog`, `verify_findings` and the Mind all use this one reader; `VERIFY_TOOLS`/`CHANGELOG_*_TOOLS` lists are deleted. `load_table` stays for xlsx/csv.

## 7. Delegation: Mind routing and the new `delegate`

- `ROUTER` gets a third route `delegate` ("the user asks to compare versions, or to work on an existing
  comparison"). That route skips the planner and goes to the executor with `delegate` available and a hint line.
  `PLANNER` loses its Change Analyzer wording.
- `delegate(subagent, request, documents=None, analysis_id=None)`: `request` is **required**, in the user's
  words ("pass the user's request verbatim; the subagent cannot see the conversation"). `documents` are the
  two handles when known; `analysis_id` when the user refers to an existing table. `row_ids` is dropped;
  rows are addressed inside the agent as `R<n>`.
- The Mind must know which comparisons exist. The register shown to the Mind gets one line per stored analysis ("analysis 3f2a: D1 (2025) vs D3 (2024), 37 rows, report current"), built by `catalog.render()`.
- The Change Analyzer's final answer to the Mind stays short (status, counts, what changed in the table), as today; the table and report are shown in the panel.

## 8. Data model (migration 0007)

`change_analyses` (analyses stand on their own; no document deletion ever removes one):

- Add `current_hash`, `previous_hash` (`content_hash` of the versions), backfilled from `document_versions`
  for existing rows, then `NOT NULL`. **Identity = the ordered hash pair**: unique on
  `(current_hash, previous_hash)`.
- `current_version_id` / `previous_version_id` become **nullable** and their foreign keys change from
  `ON DELETE CASCADE` to `ON DELETE SET NULL`. They are a convenience link to the live document, not the identity.
- Add a snapshot of what the UI and the report need when the document is gone: `current_doc` and
  `previous_doc` (`jsonb`: title, collection, label, page count), filled at creation and refreshed when the
  documents are renamed.
- **Re-link on ingest:** `IngestionPipeline.ingest` ends with `UPDATE change_analyses SET current_version_id = :new
  WHERE current_hash = :hash AND current_version_id IS NULL` (same for previous). A re-ingested file therefore
  gets its table, archive, report and history back with no user action. Lookup for a requested pair is by hash
  pair (in either order, resolved by the versions check), so a deleted-and-re-ingested document matches too.
- Existing duplicates (several analyses per pair): keep the newest per pair as the pair's analysis and move
  the older ones into `change_runs` as history instead of deleting them.
- Add `report_stale boolean`, `report_revision int`, `table_revision int`.

`change_rows`: add `status text` (`active` default, `candidate`, `archived`), `archive_reason text`,
`archived_at timestamptz`, `archived_by_run uuid`, `confirmations int default 0`, `origin` gains `agent`.
Index on `(analysis_id, status)`.

`change_runs` (new): `id`, `analysis_id`, `started_at`, `request text`, `kind` (`compare` | `rescan` (a continued scan) | `verify` | `edit` | `question`), `scope text`, `usage jsonb`
(per tool and model, section 9), `steps int`, `outcome text`. This is the run history and the cost per run; today's `change_analyses.meta` keeps only the last run.

UI and export read `status='active'` rows only; "Show deleted" lists `archived` rows with reason, run and date and a Restore action (`POST /changes/{id}/rows/{row}/restore`). Candidates are never shown as findings (a "pending" count can be).

## 9. Follow-up requests: depth instead of rescans (a scan is repeated only to finish an interrupted one)

The scan finds candidates quickly and misses different things each time. Later work is the job of the
verifier and of in-depth research. What the agent does for typical requests:

| User says | Agent does (no new scan) |
|---|---|
| "Review chapter 3 again" | Answers that the analysis exists (date, rows), asks what to focus on if unclear. Then: `get_table` filtered to chapter 3 → `verify_findings` on its unverified rows → research the chapter with `get_outline`, `read_pages`, `search`/`grep` for changes the table lacks → `add_row` for each, then `verify_findings` → `write_report`. |
| "Is row 14 really normative?" / "verify R14" | `get_row`, `verify_findings(R14)`; answer with the proof or the reason it failed. |
| "Add: clause 5.2 now requires X" | `add_row` (user's wording, `origin=user`), `verify_findings` on it if asked. |
| "Check the changelog against the table" | `find_changelog`, then `verify_findings` on each compliance-relevant entry no row explains; verified ones become rows, the others are listed as "claimed in changelog, no change found". |
| A scan failed or crashed midway | `scan_documents` again: it continues from the rows saved so far. |

Rows the agent adds from its own research go through the same gate as scan candidates (verification is the
only way into the table). `add_row` and the changelog check pass the admission gate of invariant 6: a finding that is already in the table is never added twice.

## 10. Cost tracking

- `Usage` gets `cache_write_tokens`. Adapters normalize to four fields: `input_tokens` stays the uncached count; `cached_input_tokens` and `cache_write_tokens` are separate; `output_tokens` includes reasoning. (Anthropic reports input, cache read and cache creation separately; OpenAI's `prompt_tokens` includes cached. Today the Anthropic adapter adds them together into `input_tokens`; that line changes.)
- `MODEL_PRICES` entries become `ModelPrice(input, cached_read, cache_write, output)` per 1M; overridable from config as the embedding price already is. Missing cached price falls back to input price and marks the cost `upper_bound=True`; missing cache write price falls back to input price.
- `cost = uncached*input + cached_read*cached_read + cache_write*cache_write + output*output`; also returned: cost without caching and cache hit rate `cached_read / (uncached + cached_read + cache_write)`.
- `RunEnv._count` aggregates by `(tool, model)`; the Mind's usage keeps adding the subagent's. Report, run history and the Agents page show per call/tool/agent/run and the line `model: X uncached + Y cached read + Z cache write + W output = $total (without caching $…)`.
- Prompt caching only helps if the stable prefix is stable: tool definitions and the system prompt come first with a cache breakpoint (already done in `RunEnv.loop`); the register and document context come after. Keep the agent's tool list ordered and constant.
- Reconcile with the provider invoice monthly; the usual causes of a mismatch are a wrong cache price or separate billing of cache writes. This is a manual check, not code.

## 11. UI

- **Agents page** (`Agents.tsx` and the `/agents` payload built in `agent.py`): replace the stage diagram with: the Mind and the Change Analyzer, each showing its prompt and its tools grouped by tier (core, table, advanced), with the model of each advanced tool and the profile differences (scope, prompt overlay). The order of work is drawn as a dashed "typical order" hint, not as a pipeline. The invariants of section 5 are listed under the agent as "enforced in code".
- **Change panel**: "Show deleted" toggle and Restore; the report is updated automatically ("Updating the report to match the table…" while it runs); `agent` origin badge; run history list (request, kind, scope, cost) from `change_runs`.
- **Chat trace**: unchanged mechanism (`tool_start`/`tool_end`), new tool names.

## 12. Decisions needed before implementation

Recommended defaults are first.

1. ~~Pair identity~~ **Decided:** exactly one analysis per pair, reused by default; a swapped upload finds the same table through the versions check.
2. ~~Re-ingest of the same file~~ **Decided:** analyses are never deleted with a document; identity is the hash pair; re-ingest re-links by hash (section 8). Deleting an analysis is a separate, explicit action.
3. ~~Matcher cost~~ **Decided:** there is no matcher: the scan runs once per pair and is only continued after a failure.
4. ~~Step limit~~ **Decided:** two levels (section 1): 25 steps for the agent's loop per request (a normal request needs under 10), and the existing per-tool limits (8 steps per row verification, 8 for the changelog search) unchanged.
5. ~~Compatibility~~ **Decided:** `compare()`, `verify_rows()` and the REST routes stay unchanged for the UI and CLI.

6. ~~Cost limit per request~~ **Decided:** a cap of 1000 model calls per request (first set to 300, raised after a 313-call run on a 100-page standard) (`change_request_max_model_calls`), counting loops inside tools. Calls cannot be estimated up front (a 30-page questionnaire alone takes 100+), so 1000 is a rough boundary to be tuned empirically. When hit, the agent stops, saves the table and tells the user what is left to verify.

All decisions are made; implementation follows section 13.

## 13. Implementation order

Each step is independently shippable and keeps the tests green.

1. **Cost tracking** (section 10): `Usage`, adapters, `ModelPrice`, `cost_breakdown`, report line. Small, no dependencies.
2. **Readers and files** (sections 3 and 6): `read_url` with PDF, docx, vision pages; `create_file` replacing `export_excel`; drop `subagent_only`; Mind gets the core tools. Tests: PDF/docx fixtures served from a local stub.
3. **Data model and store** (section 8): migration 0007 (hash identity, `SET NULL` foreign keys, document snapshot, row status, runs), re-link on ingest, `ChangeStore` methods for status, archive, restore, runs; UI and export filter on `active`; soft delete; UI and routes cope with analyses whose documents are missing (proof shown without page links).
4. **Table tools and invariants** (sections 3 and 5): `get_table`, `add_row`, `update_row`, `archive_row`, `restore_row`; staging; `verify_findings` promotes or archives.
5. **Advanced tools as tools** (section 3): wrap `check_versions`, `scan_documents` (resumable), `find_changelog`, `write_report`; pair lookup and reuse.
6. **The agent loop** (sections 1 and 4): prompt, spec with profile, entry points `compare`/`verify_rows`/delegate; delete `_compare` and the stage specs.
7. **Mind routing** (section 7): router, planner text, `delegate` signature, register lines.
8. **UI** (section 11): Agents page, archive toggle, automatic report update, run history.

## 14. Acceptance tests

Scripted (fake LLM, `tests/unit/fakes.py`; `test_change_analyzer.py` has 35 tests to port, most of them test the building blocks that stay):

- Same pair requested twice: the second call makes **no** scan or verify call and returns the stored table (call counter).
- `scan_documents` on a pair with a finished scan: refuses with the date and row count, no model call.
- A scan that failed midway (and one marked unfinished by a crash) is continued: finished parts are skipped, the model gets the rows so far, nothing is stored twice, the scan is marked finished.
- "Review chapter 3 again" with an existing table: no scan call; the agent loads the table and verifies or researches (call counter).
- A row that repeats an existing one (same clause, any wording or numbering) is refused by the gate, from `add_row` and from the changelog check alike; its proof goes to the existing row if that lacks it.
- Candidate rejected by verification: archived with reason, never shown, never shown.
- Delete a document version: its analyses, rows, archive and report remain; `get_table` still works; `scan_documents` answers "document not in the register".
- Re-ingest the same file: the analysis is re-linked to the new version id by hash and a scan or verify works again.
- `update_row` on a verified row's requirement clears the verified flag; a report is rewritten by code after row changes (agent request end, panel edits, downloads) without the agent calling `write_report`.
- `read_url` on a PDF and a .docx returns text with page labels; a PDF page without text points to `vision_pages`.
- `create_file`: txt, md, csv and xlsx files are written and linked; an xlsx made from `rows` and one made from `source` open with the right headers; a cell `=1+1` is stored as text; an oversize request is refused with a clear message.
- Cost: four-field usage from both adapters; known cache hit gives lower cost than the no-cache figure.

Live (manual, development): precision (every starred row has verbatim proof), noise (no punctuation-only rows), recall (compliance-relevant changelog entries appear in the table), **consistency** (three runs on the same pair: the active table after run 2 and 3 contains all rows of run 1, and added rows are all verified), cost per run logged with cache hit rate.


## 15. Implementation notes

Where the code differs from, or adds to, the plan above:

- **Handles** are `R1, R2, ...` (ordinal + 1), not `R<ordinal>`.
- **A check that could not run** (technical failure, every relaunch failed) leaves a candidate **pending**, not archived: only a finished check without proof archives it. The UI shows a pending count and a clock mark.
- **Editorial, relocated and non-normative candidates** are added to the table by the scan itself (they are never verified in depth); only actionable candidates wait for `verify_findings`.
- **No repeated rows** (`admit.py`): see invariant 6. The gate runs under the analyzer's lock, so two rows added together cannot both slip past each other. New rows are still appended at the end of the table: placing them at the right position would need deterministic sorting and was left out.
- **Model-call limit:** the agent's own turns are counted live; a tool that starts past the limit refuses with a message telling the agent to stop and report what is open. Loops already running finish, so the limit can be exceeded by one batch.
- **Web tools in inner loops** (changelog search, verification) are offered only for public documents.
- **Run history** is written once at the end of a request (`change_runs`), with per-model and per-tool cost.
- **Migrations:** 0006 normalized text, 0007 identity by hash / archive / runs, 0008 one live analysis per pair (older duplicates are marked `superseded`, kept).
- **Agents page:** the Change Analyzer's tools are drawn as three groups (core, table, steps) around it; the order of the steps is a hint in its prompt, not drawn as a pipeline.
- **Old stored analyses** keep working: reports and the Excel Info sheet read cost data in both the old and the new shape.
- **Scan simplified after review:** no `scope`, no `rescan` flag, no LLM matcher and no scope resolver. A scan is repeated only to finish an interrupted one; `confirmations` on rows stays in the data model but nothing sets it any more.
