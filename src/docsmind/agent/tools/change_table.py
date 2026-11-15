"""The Change Analyzer's own tools: the change table (`table` tier) and the steps of a comparison
(`advanced` tier). They are thin: the work is in `ChangeAnalyzer.op_*` (see the analyzer), which
the agent loop puts into `ToolContext.cache["change_analyzer"]`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from docsmind.agent import prompts
from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput
from docsmind.domain.changes import Binding, Nature
from docsmind.domain.errors import ToolInputError

if TYPE_CHECKING:
    from docsmind.agent.subagents.change_analysis.analyzer import ChangeAnalyzer


def analyzer_of(ctx: ToolContext) -> ChangeAnalyzer:
    analyzer = ctx.cache.get("change_analyzer")
    if analyzer is None:
        raise ToolInputError("This tool is only available to the Change Analyzer.")
    return analyzer


class _Empty(BaseModel):
    pass


# -- advanced tools: the steps of a comparison --------------------------------------------------------------


class CheckVersionsTool(Tool):
    name = "check_versions"
    tier = "advanced"
    model_prompt = prompts.DECIDER
    summary = "Checks that two documents are versions of one document, and creates or loads their change table."
    description = (
        "Check that the two documents given to you are really two versions of one document (or a document and "
        "its changelog), which one is newer, and whether they are public. Creates the change table for the pair, "
        "or loads the one that exists (then no model is called). Unrelated or identical documents get a plain "
        "answer and no table. Call this first for a new pair."
    )
    how_it_works = (
        "Normalizes both texts (headers, footers, page numbers removed) and diffs them in code.",
        "Identical texts end here, with no model call.",
        "A small model reads titles, outlines and first pages and decides: versions, unrelated or changelog; which is newer; public or internal.",
        "Looks the pair up by the files' content hashes: an existing table is loaded instead of a new one made.",
    )
    Args = _Empty

    async def run(self, args: _Empty, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(await analyzer_of(ctx).op_check_versions(), summary="versions checked")


class ScanArgs(BaseModel):
    mode: Literal["auto", "diff", "full"] = Field(
        default="auto",
        description="auto: by the diff ratio (below the threshold the model sees A plus the diff, else A and B in full).",
    )
    focus: str = Field(default="", max_length=600, description="What the user cares about, to focus the scan.")


class ScanDocumentsTool(Tool):
    name = "scan_documents"
    tier = "advanced"
    model_prompt = prompts.SCANNER
    long_running = True
    summary = "The internal first pass that builds the foundation of the table; continues from where it stopped if it failed."
    description = (
        "Scan document A together with B (or with the diff against B) and list candidate changes as rows. It is the "
        "internal FIRST PASS that builds the foundation of the table, not something the user asks for: one "
        "long-context model call per part, not deterministic, so it runs once per pair and is refused afterwards. "
        "Call it again ONLY when a scan failed or crashed midway: it then continues from the rows saved so far and "
        "the parts already done. Candidates are not part of the table yet: actionable ones wait for "
        "verify_findings; editorial, relocated and non-normative ones are added when the scan finishes."
    )
    how_it_works = (
        "Refuses when the pair already has a finished scan.",
        "Chooses the input: A plus a bounded diff below the threshold, A and B in full above it; long documents are split at section boundaries.",
        "One long-context model call per part writes rows as JSONL; each row is parsed and saved as a candidate as it arrives.",
        "Progress is saved after every part. After a failure or crash a new call skips the finished parts and tells the model which rows exist, so it continues after the last one.",
    )
    Args = ScanArgs

    async def run(self, args: ScanArgs, ctx: ToolContext) -> ToolOutput:
        text = await analyzer_of(ctx).op_scan(mode=args.mode, focus=args.focus)
        return ToolOutput(text, summary=text.split("\n", 1)[0][:90])


class FindChangelogArgs(BaseModel):
    query: str | None = Field(default=None, max_length=200, description="A web search hint, e.g. 'NIST SP 800-171 Rev. 3 changelog'.")


class FindChangelogTool(Tool):
    name = "find_changelog"
    tier = "advanced"
    model_prompt = prompts.CHANGELOG_FINDER + "\n\n=== matching entries to rows (second, smaller call) ===\n" + prompts.CHANGELOG_MATCHER
    long_running = True
    summary = "Looks for a revision history or release notes, in the documents and (for public ones) online."
    description = (
        "Look for a changelog of the change from B to A: inside the documents, and for public documents also "
        "online (HTML, PDF and Word are readable). Stores its entries and reports which compliance-relevant "
        "entries have no row in the table. Internal documents are never searched online."
    )
    how_it_works = (
        "A small tool loop searches the outline, search and grep of both documents for a revision history.",
        "For public documents it also searches the web and reads the publisher's page, PDF or Word file.",
        "A small model matches each compliance-relevant entry to a row; entries without a row are listed.",
    )
    Args = FindChangelogArgs

    async def run(self, args: FindChangelogArgs, ctx: ToolContext) -> ToolOutput:
        text = await analyzer_of(ctx).op_find_changelog(args.query)
        return ToolOutput(text, summary=text.split("\n", 1)[0][:90])


class VerifyArgs(BaseModel):
    rows: list[str] | None = Field(default=None, max_length=60, description='Row handles to verify, e.g. ["R3", "R14"].')
    which: Literal["pending", "unverified"] | None = Field(
        default=None,
        description='"pending": all actionable candidates from a scan. "unverified": active actionable rows without proof.',
    )
    changelog_gaps: bool = Field(
        default=False, description="Also check compliance-relevant changelog entries that no row describes (needs find_changelog first)."
    )


class VerifyFindingsTool(Tool):
    name = "verify_findings"
    tier = "advanced"
    model_prompt = prompts.VERIFIER
    long_running = True
    summary = "Researches rows against the documents and records verbatim proof; the only way a candidate enters the table."
    description = (
        "Verify rows against the documents: for each one a small research loop (search, grep, read pages, page "
        "vision) must find verbatim proof; code re-checks every passage. A proven candidate joins the table; a "
        "candidate without proof is archived with the reason; an active row without proof stays, unverified. "
        "Use it on pending candidates, on any row the user doubts, and on rows you added. Rows run in parallel."
    )
    how_it_works = (
        "Picks the rows by handle or by 'pending' / 'unverified'.",
        "One tool loop per row searches and reads both documents; new needs proof in A and a verified absence in B, modified needs proof in both, removed the reverse.",
        "Code checks that every proof passage occurs verbatim on the page it names (or in what page vision read).",
        "Proven candidates become active; unproven candidates are archived with the reason; unproven active rows keep a note.",
    )
    Args = VerifyArgs

    async def run(self, args: VerifyArgs, ctx: ToolContext) -> ToolOutput:
        text = await analyzer_of(ctx).op_verify(args.rows, args.which, args.changelog_gaps)
        return ToolOutput(text, summary=text.split("\n", 1)[0][:90])


class WriteReportArgs(BaseModel):
    focus: str = Field(default="", max_length=600, description="Optional emphasis for the report.")


class WriteReportTool(Tool):
    name = "write_report"
    tier = "advanced"
    model_prompt = prompts.REPORT_WRITER
    long_running = True
    summary = "Writes the report from the current table: findings, compliance impact, methodology, cost."
    description = (
        "Write (or rewrite) the report from the active rows of the table. A model writes the findings and the "
        "compliance impact; code adds editorial and relocated rows, the changelog section, the methodology, the "
        "cost and the disclaimer. Code already rewrites the report when the table changed: call it only to focus the report or when the user asks for one."
    )
    how_it_works = (
        "Reads the active rows and the stored changelog.",
        "One model call writes the narrative from the verified rows; code adds the other sections.",
        "Stores the report with the table revision it covers; code rewrites it whenever the table changes later, so a report is never behind the table.",
    )
    Args = WriteReportArgs

    async def run(self, args: WriteReportArgs, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(await analyzer_of(ctx).op_write_report(args.focus), summary="report written")


# -- table tools ---------------------------------------------------------------------------------------------


class GetTableArgs(BaseModel):
    status: Literal["active", "candidate", "archived"] = "active"
    nature: Nature | None = None
    actionable_only: bool = False
    verified: bool | None = None
    clause_contains: str | None = Field(default=None, max_length=200, description="Text in the clause or the description.")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=60, ge=1, le=100)


class GetTableTool(Tool):
    name = "get_table"
    tier = "table"
    summary = "Lists rows of the change table (compact), with counts, filtered by status, nature, verification or text."
    description = (
        "List rows of the change table, one line each: handle (R<number>), nature, binding, verified (✓, ·, ? "
        "for pending), clause and a short description, with counts at the top. status: active (the table, "
        "default), candidate (waiting for verification) or archived. Filter by nature, actionable_only, "
        "verified or clause_contains; page with offset. Use get_row for the full row with its proof."
    )
    how_it_works = (
        "Reads the rows of the table from the store.",
        "Filters them and returns one compact line per row, so a long table never fills the context.",
    )
    Args = GetTableArgs

    async def run(self, args: GetTableArgs, ctx: ToolContext) -> ToolOutput:
        text = await analyzer_of(ctx).op_get_table(
            status=args.status,
            nature=args.nature.value if args.nature else None,
            actionable_only=args.actionable_only,
            verified=args.verified,
            clause_contains=args.clause_contains,
            offset=args.offset,
            limit=args.limit,
        )
        return ToolOutput(text, summary=text.split("\n", 1)[0][:90])


class RowArgs(BaseModel):
    row: str = Field(description='Row handle, e.g. "R12".')


class GetRowTool(Tool):
    name = "get_row"
    tier = "table"
    summary = "Shows one row in full, with its verbatim proof and why it is not verified."
    description = "Show one row in full: clauses, nature, binding, requirement, scope, verification note and proof passages."
    how_it_works = ("Reads one row by its handle.",)
    Args = RowArgs

    async def run(self, args: RowArgs, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(await analyzer_of(ctx).op_get_row(args.row), summary=args.row)


class RowFields(BaseModel):
    current_clause: str = Field(default="", max_length=2_000, description="Number plus title as in the text, e.g. '4.1. Backups'.")
    previous_clause: str = Field(default="", max_length=2_000)
    change_description: str = Field(default="", max_length=4_000)
    nature: Nature = Nature.MODIFIED
    category: str = Field(default="", max_length=200)
    compliance_requirement: str = Field(default="No change", max_length=2_000)
    binding: Binding = Binding.UNCHANGED
    applicability_scope: str = Field(default="", max_length=500)


class AddRowArgs(RowFields):
    allow_duplicate: bool = Field(default=False, description="Add although similar rows exist (a different finding).")


class AddRowTool(Tool):
    name = "add_row"
    tier = "table"
    summary = "Adds a finding to the table (from the user or from your own research), unverified."
    description = (
        "Add a row to the table: a finding of the user or of your own research. It starts unverified: call "
        "verify_findings on it. Code warns about similar rows that exist already (active or archived) and "
        "adds nothing then, unless allow_duplicate=true. The rules apply: editorial and relocated rows carry "
        "no requirement; only new, modified or removed rows can be normative."
    )
    how_it_works = (
        "Applies the row rules in code and compares the row with existing ones.",
        "Stores it as an active, unverified row (origin: agent).",
    )
    Args = AddRowArgs

    async def run(self, args: AddRowArgs, ctx: ToolContext) -> ToolOutput:
        fields = args.model_dump(exclude={"allow_duplicate"})
        return ToolOutput(await analyzer_of(ctx).op_add_row(fields, allow_duplicate=args.allow_duplicate), summary="row added")


class UpdateRowArgs(BaseModel):
    row: str = Field(description='Row handle, e.g. "R12".')
    current_clause: str | None = Field(default=None, max_length=2_000)
    previous_clause: str | None = Field(default=None, max_length=2_000)
    change_description: str | None = Field(default=None, max_length=4_000)
    nature: Nature | None = None
    category: str | None = Field(default=None, max_length=200)
    compliance_requirement: str | None = Field(default=None, max_length=2_000)
    binding: Binding | None = None
    applicability_scope: str | None = Field(default=None, max_length=500)


class UpdateRowTool(Tool):
    name = "update_row"
    tier = "table"
    summary = "Edits fields of a row; editing its claim clears its verification."
    description = (
        "Change fields of an active row. Changing the clause, nature, requirement or binding of a verified "
        "row clears its verification and proof (they no longer apply): verify it again."
    )
    how_it_works = ("Applies the row rules in code and stores the change.", "Clears verification when the claim changed.")
    Args = UpdateRowArgs

    async def run(self, args: UpdateRowArgs, ctx: ToolContext) -> ToolOutput:
        fields = args.model_dump(exclude={"row"}, exclude_none=True)
        if not fields:
            raise ToolInputError("Pass at least one field to change.")
        return ToolOutput(await analyzer_of(ctx).op_update_row(args.row, fields), summary=args.row)


class ArchiveRowArgs(BaseModel):
    row: str = Field(description='Row handle, e.g. "R12".')
    reason: str = Field(min_length=3, max_length=500, description="Why the row leaves the table.")


class ArchiveRowTool(Tool):
    name = "archive_row"
    tier = "table"
    summary = "Moves a row out of the table into the archive, with the reason. It can be restored."
    description = "Move a row out of the table into the archive (never destroyed), with the reason. Use it when a row is wrong or the user asks to delete it."
    how_it_works = ("Marks the row archived with the reason and the date; the table, export and report no longer show it.",)
    Args = ArchiveRowArgs

    async def run(self, args: ArchiveRowArgs, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(await analyzer_of(ctx).op_archive_row(args.row, args.reason), summary=args.row)


class RestoreRowTool(Tool):
    name = "restore_row"
    tier = "table"
    summary = "Brings an archived row back into the table."
    description = "Bring an archived row back into the table."
    how_it_works = ("Marks the row active again and clears the archive reason.",)
    Args = RowArgs

    async def run(self, args: RowArgs, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(await analyzer_of(ctx).op_restore_row(args.row), summary=args.row)


__all__ = [
    "AddRowTool",
    "ArchiveRowTool",
    "CheckVersionsTool",
    "FindChangelogTool",
    "GetRowTool",
    "GetTableTool",
    "RestoreRowTool",
    "ScanDocumentsTool",
    "UpdateRowTool",
    "VerifyFindingsTool",
    "WriteReportTool",
]
