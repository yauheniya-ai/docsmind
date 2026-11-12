# Change Analyzer: Implementation Brief

Compares two versions of a document and lists all changes, substantive and editorial. Substantive changes carry the most responsibility, so they are verified in depth.

## 1. What the Change Analyzer does

Mind delegates every request to compare two versions of a document to the Change Analyzer. Only the Change Analyzer holds the table definitions (section 5), so it does all four of these itself:

1. **Check the documents.** Are these really two versions of one document? Unrelated or identical documents get a plain-words reply and no table.
2. **Decide on the changelog.** Is it worth looking for online? Yes for public documents, no for internal ones.
3. **Scan.** Call the scan tool, an LLM call that builds the structured change table from the full documents.
4. **Verify.** Verify the compliance-relevant rows against the documents. This is the Change Analyzer's main job.
5. **Split.** Split documents that don't fit within the context window.

Everything else about documents stays in Mind.

## 2. Deterministic preparation (code, no agent)

- Take the text of A and B that was extracted at upload.
- Normalize it: remove headers, footers and page numbers, then split into sentences, one per line.
- Run a line diff of A against B. Diff ratio = changed sentences / total sentences in A.
- If the normalized texts are identical (diff ratio 0), stop here: the documents are the same, so no model call is made and no table is built.
- Choose the mode by threshold. The default is 25%, and the developer can change it later.
  - Below the threshold: the model gets full A plus the bounded diff (4 lines either side of each change).
  - At or above the threshold: the model gets full A plus full B.
- The diff ratio is also available as a signal for the versions check.

## 3. Decisions, scan and verification

### Decide

**Are these really versions of one document?** The Change Analyzer decides this, from titles, outlines, first pages and the diff ratio. Users sometimes upload two unrelated documents, or the same document twice under different names or version labels. Those get no table: it replies in plain words and says what it sees. A document plus its own changelog is different: the changelog describes the diff to the previous version, so a table is built from it.

**Is a changelog worth searching for online?** Yes for public documents such as laws, regulations, standards and public specifications, where the publisher and version are identifiable and a changelog is likely published. No for internal documents such as internal technical specifications. The Change Analyzer decides this. When the answer is no, the changelog search gets no web tools. The check for a changelog inside the documents is always done.

### Build the change table

The scan is a tool owned by the Change Analyzer: one long-context model call with no tools. It receives the table definitions (section 5) plus the inputs chosen in section 2, and it outputs JSONL, one change per line. Rules for the model:

- Unchanged clauses are not recorded.
- Extraction noise is not recorded: a punctuation mark added, or a paragraph moved from one page to another. A real rewording is recorded as editorial.
- Each row gets exactly one nature (new, modified, editorial, removed, relocated), assigned correctly by the model from the start. Substantive overrides structural: a clause that is both modified and relocated is tagged modified. Relocated means only the position changed and the requirement is identical.
- Rows are ordered by position in A.

### Verify compliance-relevant changes

- **Which rows:** actionable rows only: a compliance delta exists AND its binding is normative. Editorial changes and noise are not verified.
- **What each row needs:** new needs proof in A plus a verified absence in B. Modified needs proof in A and B. Removed needs proof in B plus a verified absence in A. The Proof is extracted as verbatim passages with page references and shown in a tooltip. The Verified star is set once verification is done.
- **Tools:** search (hybrid if the documents are embedded, keyword otherwise), grep and read\_pages. Page vision when these text tools fail.
- **Loop until the proof is found,** escalating through: the row's clause context in A and B, then targeted grep and search, then page vision. No proof after all three means the change does not exist in the text, so the row is removed.
- **Changelog:** it is scanned together with the table. Each compliance-relevant entry (a requirement added or removed) should match a row. If an entry has no row, the verifier searches A and B for it. If the change is real, it adds the row with proof. If not, it lists the entry as 'claimed in changelog, no change found'. The changelog is reference only and never replaces our rows or our schema. High-level entries are used only in the report.
- Rows can be verified in parallel.

## 4. Structure and flow

The Change Analyzer is the main agent. It holds the table definitions, makes the decisions and owns the tools below, and its own context stays small. The scan is a tool it calls: one long-context model call with no tools. Searching for the changelog and verifying rows use document tools. So that many rows do not fill the Change Analyzer's context, verification can run as separate small parallel calls. The scan needs a long-context model. The other steps work in small contexts.

| Step | Context | Tools | Returns |
| --- | --- | --- | --- |
| Scan (tool, LLM call) | Large: A plus the diff or B, plus the output | None | JSONL rows |
| Changelog search | Small | get\_outline, read\_pages, grep, search. web\_search, fetch\_url and load\_table only for public documents | Changelog entries, or not found |
| Row verification | Small: one row per call | search, grep, read\_pages, page vision | Verified and Proof, rows added or removed |

1. Mind delegates the request with the two document ids.
2. Code runs the preparation (section 2).
3. The Change Analyzer checks that the documents are versions (unrelated or identical documents get a plain-words answer and no table) and decides whether to search for a changelog online.
4. It starts the changelog search (local and online if public) and the scan at the same time.
5. It waits for both. If the scan fails (network error, broken JSONL), the Change Analyzer relaunches it automatically. The new call gets the same inputs (A plus the diff, or A and B) plus the rows produced so far, and continues after the last complete row. The user follows the stream of what is being done. If the changelog search fails or finds nothing, it continues without a changelog.
6. It runs a verification loop for each actionable row and checks the changelog gaps.
7. It writes the final report (section 6).

## 5. Table definitions

| Column | Definition |
| --- | --- |
| Current clause (A) | The clause in the current version (A), written as the number plus the title as they appear in the text, e.g. '4.1. Security of ...', never just '4.1.', so verifiers can search for it. For text without a number or title, such as a table, use the nearest heading and a short description of where the text sits. For multilingual documents add a language identifier, e.g. '4.2. Sicherheit (DE)' |
| Previous clause (B) | The same for the previous version (B). Empty for a new clause |
| Change description | In English. Original-language keywords appear in quotes only where terminology changed |
| Nature | Exactly one of new, modified, editorial, removed, relocated |
| Category | terminology, safety, testing, risk management, etc. |
| Compliance requirement | New: the full requirement from A. Modified: only the compliance delta. Removed: the requirement that no longer applies |
| Binding | normative, recommended, optional, informative, unchanged. For a removed row, the binding it had in B |
| Applicability scope | Who the requirement applies to |
| Verified (UI only) | Star set once verification is done. Not exported |
| Proof (UI only) | Verbatim passages from A and B with page references. Shown in a tooltip over the verified star. Not exported |

**Checks in code after the model:**

- Editorial or relocated rows cannot be normative: set binding to unchanged and compliance requirement to 'No change'.
- Only new, modified or removed rows can carry a normative, recommended or optional binding.
- Actionable = a compliance delta exists AND its binding is normative.

## 6. Inputs, outputs and long documents

**Token budget.** Output tokens count against the context limit. With 250K limit, A plus B or A plus diff should be capped at 180K tokens total, leaving roughly 70K for the table. Longer texts are split into several calls. Change Analyzer splits Doc A at ca. 90K tokens, checks which section it is and splits the Doc B or diff around the same chapter.

The split thresholds in tokens are provided in .env.example and can be changed via .env.

**Output**

- The model streams JSONL. The UI parses each line into a row as it arrives.
- The rows are stored as the single source of truth for display, editing, verification and export.
- The UI can filter to the compliance-relevant rows only and for the nature of change. The export to xlsx follows the filter. Editorial changes stay visible for teams that want them.
- Excel export is code, not an LLM. export\_excel writes the stored rows (the 8 columns, without Verified and Proof). The Change Analyzer does not need it, but Mind does, so a user can ask Mind for an Excel file.
- The report is written by the Change Analyzer at the end, based on the final table, the counts of new, modified and removed actionable clauses, and the changelog summary if one was found. It describes the findings: which changes are substantive, what the compliance impact is, and other noticeable findings. Editorial changes are listed by clause name, and relocated clauses and non-normative removed clauses are mentioned by clause name. In the end, the report briefly explains the methodology, meaning what was done to arrive at the findings, and ends with a disclaimer that the analysis is AI-based and must be verified by humans.

## 7. Tools

| Tool | Type | Used by | Notes |
| --- | --- | --- | --- |
| scan\_documents | new, LLM call | Change Analyzer | Long-context call with no tools. Builds the change table from the full documents and streams JSONL |
| get\_outline, read\_pages, grep, search | existing | Change Analyzer | Document access. search is hybrid when the documents are embedded and keyword-only when not |
| page\_vision | new | Change Analyzer | Renders the pages of one row as images, for multi-column, table or image confusion |
| load\_table | new, no LLM | Change Analyzer | Parses xlsx and csv, returns schema, row count and filterable pages, so spreadsheets stay out of the context |
| web\_search, fetch\_url | gated | Change Analyzer | Only when the Change Analyzer decides the documents are public |
| export\_excel | new, no LLM | Mind, UI export button | Writes the stored change table to xlsx |

## 8. Editing by analysts

- Analysts can add and delete rows freely.
- They can ask Mind to verify any other row, including editorial ones. Mind delegates it to the Change Analyzer, which verifies the row and fills Verified and Proof.
- Humans can add a check for approved (e.g., another column after the verified star).

## 9. Acceptance tests (development only)

- **Precision:** every starred row has verbatim proof from the documents.
- **Noise:** no rows for extraction noise, e.g., a punctuation-only change or a paragraph moved between pages.
- **Recall:** compliance-relevant changes claimed in the changelog are found in the table.
- **Cost:** log tokens and rounds per run.
