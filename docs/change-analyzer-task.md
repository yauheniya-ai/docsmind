# Task: make Change Analyzer a real agent instead of a fixed pipeline

Keep the existing UI and results unchanged.

## 1. Single agent with one toolbox

Replace the staged pipeline (Versions check → Scan → Changelog search → Row verification → Report) with one Change Analyzer agent. It gets a specialist prompt on comparing versioned documents and decides the order of work itself. It may run steps in parallel, repeat them, or skip them.

## 2. Two tool tiers: core and advanced

Tools are organized in two separate tiers, in the code and in the UI.

**Core tools** are simple, deterministic building blocks that are standard for every agent (Mind and Change Analyzer alike):

- Search, Grep, Read Pages, Get Outline, Load Table
- Web Search
- Read URL / Fetch URL, which must open HTML, plain text, **PDF and Word (.docx)**
- Page Vision

**Advanced tools** are higher-level steps, often backed by their own model. These are the former pipeline stages, exposed as callable tools:

- Versions check
- Full scan (A + B)
- Diff scan (A + diff)
- Changelog search (inside the documents and online)
- Verify finding
- Write report

Each advanced tool can use its own model, for example a long-context model for full scan, a small one for versions check, and a strong reasoner for verification.

## 3. Fix: the changelog lookup cannot open PDF or Word files

Currently the agent searching for a changelog fails on PDF links. The error is "application/pdf, which cannot be read here. Only HTML or plain text pages work." Many changelogs, release notes and standards bulletins are published as PDF or Word documents.

- Make the URL/file reader a core tool that supports PDF and .docx (text extraction, with Page Vision as a fallback for scanned or layout-heavy pages).
- Make it available to every agent, not only to one stage. Changelog search, Verify finding and Mind must all use the same reader.
- On a read failure, the agent should try another route (a different source, Page Vision) instead of silently dropping the source.

## 3b. No step-specific tool limits

Don't limit tools to specific steps. Any advanced tool or the agent itself may call any core tool. Verification in particular must be able to use all of them.

## 4. The workflow is only a hint in the prompt

Typical order:

1. Check that the documents are real versions.
2. Look for a changelog (internal and online).
3. Scan.
4. Verify each compliance-relevant finding in a loop.
5. Write the report.

The agent may deviate when the situation requires it.

## 5. The results table is the source of truth

All results are held in one table, which the agent updates as it works. There is no separate ledger concept.

- Each row is one finding, with at least: scope (chapter or page range), description, source (which tool or document produced it), and status (unverified, verified, rejected).
- Scans and changelog search add rows. Verification updates the status of existing rows.
- The report is derived from the table and is never written ad hoc. Regenerating it after any change always gives a consistent result.

## 6. Support client requests as partial re-runs

A request like "review chapter 3 again" should trigger a scan limited to that scope, then verification, then a report rewrite. Only the table rows in that scope are replaced. All other rows are kept.

## 7. One definition per tool, with per-agent profiles

Tools are defined once and shared between agents. Scope, prompt overlay or model can be adapted per agent. For example, Search covers all documents for Mind but only the document pair for Change Analyzer.

## 8. Delegation stays as it is

Mind hands versioned-document comparisons to Change Analyzer and gets the report back. Mind may also call the lightweight versions check directly.

## 9. Track true cost including cached tokens

Current cost tracking only knows input and output tokens, and prices all input at the full rate (for example "936,861 input and 21,050 output tokens at $0.1 / $0.5 per 1M"). That overstates the cost whenever prompt caching applies. Change Analyzer makes many calls that repeat the same large context (documents, tool definitions), so caching matters here. This is a small change, not a complicated one.

**What to record per LLM call** (all providers return these in the usage block):

- Uncached input tokens
- Cached input tokens read (cache hits)
- Cache write tokens, for providers that charge for writing the cache
- Output tokens, including reasoning tokens if they are billed as output

**Normalize provider differences first.** Some providers report total input that already includes cached tokens, others report cached tokens separately. Convert every call into the four fields above, so that `uncached_input = total_input - cached_read - cache_write` where the provider's input total includes them.

**Formula:**

```
cost = uncached_input   * price_input
     + cached_read      * price_cached_read
     + cache_write      * price_cache_write
     + output           * price_output
```

**Price table per model** (configurable, not hardcoded), with these fields: input, cached read, cache write, output, all per 1M tokens. If a provider has no cache write fee, set it to the normal input price or zero as that provider bills. If a model has no cached price configured, fall back to the input price and mark the cost as an upper bound.

**Where to show it:**

- Per call, per tool (Full scan, Verify finding and so on), per agent, and per run, so expensive steps are visible.
- Show the cache hit rate (`cached_read / total_input`) next to the cost, because it explains why a run was cheap or expensive.
- Show the line as: `model: X uncached + Y cached read + Z cache write + W output = $total`, plus what the cost would have been without caching.

**Check against the invoice.** Compare the tracked totals with the provider's billing regularly. If they differ, the usual causes are a wrong cache price or a provider that bills cache writes separately.

## 10. Consistency across runs and sessions

The same two documents produced different results in two sessions (29 changes / 15 compliance-relevant vs. 37 / 19). A single LLM scan misses different things each time, and descriptions of the same change are worded differently every run, so deterministic matching or a programmatic diff does not work. Consistency comes from persistence, reconciliation and a verification gate.

### 10.1 Persist the results table per document pair

- Key the table by the content hashes of document A and document B, and store it outside the session.
- A new session first looks up an existing table for the pair and loads it. It never starts from an empty table when one exists.
- The table also stores the archive (see 10.4), the run history, and the cost per run.

### 10.2 Repeat requests return existing results

- A request to compare the same pair returns the existing table and report by default. The answer is the same and costs almost nothing.
- A new scan runs only when the user asks to review again (all or a scope, see section 6) or when the documents have changed (hash mismatch).

### 10.3 A rescan reconciles with the table and never recreates it

1. **Staging.** A rescan writes its candidate findings to a staging area. Nothing in the main table changes until a candidate has been classified.
2. **LLM matcher.** Each candidate is compared with all existing rows, active and archived. Wording varies between runs, so matching is semantic. Location (chapter, page) is used only to narrow which rows the matcher compares against. Outcomes:
   - **Matches an active row:** discard the candidate and optionally increase that row's confirmation count.
   - **Matches an archived row (rejected or deleted):** discard the candidate. This keeps old noise out of the results.
   - **No match:** send it to verification.
3. **Verification is the gate.** A new candidate enters the main table only if it is verified. If verification rejects it, it goes to the archive with the reason, so it is not verified again in later runs. Only unmatched candidates are verified, which also limits cost.
4. **Active rows are never removed because a scan did not find them again.** A missed finding in one run is not evidence against it. Only verification or a manual user action moves a row out of the main table.
5. **Match against the archive only on high confidence.** If the matcher is unsure whether a candidate equals an archived row, send the candidate to verification. This prevents a genuine new finding from being suppressed.

### 10.4 Archive instead of deleting

- Rejected and deleted rows are removed from the main view but kept in an archive.
- The UI has a "Show deleted" toggle that lists archived rows with the reason, the run that archived them and the date.
- A user can restore an archived row. Rows deleted manually by the user go to the same archive.

### 10.5 Reduce variance in the scan itself

- Put a granularity rule in the scan prompt: what counts as one finding, and when changes are merged or split. Part of the gap between runs comes from one run merging changes that another run splits.
- Compliance relevance is decided by the verification step against stated criteria, not by the scan, so the classification does not vary between scans.

### 10.6 Stop rule and stability indicator

- Optional: when the user asks for a thorough review, rescan until a run adds no new verified findings (or two in a row).
- Show the number of runs and the time of the last reconciliation in the report, so the user can see how stable the result is.
