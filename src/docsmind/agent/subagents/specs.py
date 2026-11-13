"""Subagent definitions. A subagent is a role (prompt + tool subset) that runs
the same tool loop as the executor, but in a fresh context and returning only
a short report — so big reading jobs don't bloat the main conversation."""

from __future__ import annotations

from dataclasses import dataclass

from docsmind.agent import prompts


@dataclass(frozen=True)
class StageSpec:
    """One step of a code-driven subagent: a model call (or a small tool loop) with its own prompt."""

    name: str
    role: str
    description: str
    prompt: str
    user_prompt: str
    model: str  # "main" | "fast" | "scan": which configured model it runs on
    budget: str  # what the UI shows as its limit
    tools: tuple[str, ...] = ()  # the tools its loop may call (empty: a single model call)


@dataclass(frozen=True)
class SubagentSpec:
    name: str
    role: str
    description: str  # shown to the Mind (tool description) and in the UI
    prompt: str
    tools: tuple[str, ...]
    stages: tuple[StageSpec, ...] = ()


# The tools of the Change Analyzer's two tool loops. Only these stages call tools; the web tools are
# offered by the changelog search only for public documents.
CHANGELOG_LOCAL_TOOLS = ("get_outline", "read_pages", "grep", "search")
CHANGELOG_WEB_TOOLS = ("web_search", "fetch_url", "load_table")
VERIFY_TOOLS = ("search", "grep", "read_pages", "page_vision")

CHANGE_ANALYZER_STAGES = (
    StageSpec(
        name="versions_check",
        role="Versions check",
        description=(
            "Decides from the titles, outlines, first pages and the diff ratio whether the two documents are "
            "versions of one document, which one is newer, and whether the documents are public."
        ),
        prompt=prompts.DECIDER,
        user_prompt="Titles, outlines, first pages and the diff ratio of A and B, prepared by code.",
        model="fast",
        budget="One model call, no tools",
    ),
    StageSpec(
        name="scan",
        role="Scan",
        description=(
            "One long-context model reads the current version together with a diff against the previous "
            "one (or both versions in full when they differ a lot) and writes the change table, one row "
            "per change. Long documents are split into parts. Rows without proof are dropped later."
        ),
        prompt=prompts.SCANNER,
        user_prompt="Version A in full, then the diff of A against B (or B in full), one sentence per line.",
        model="scan",
        budget="One model call per part, no tools",
    ),
    StageSpec(
        name="changelog",
        role="Changelog search",
        description=(
            "Looks for a revision history or release notes, in the documents and (for public ones) online, "
            "then matches its entries to the rows of the table."
        ),
        prompt=prompts.CHANGELOG_FINDER,
        user_prompt="The two documents and their handles.",
        model="main",
        budget="A tool loop, in parallel with the scan",
        tools=CHANGELOG_LOCAL_TOOLS + CHANGELOG_WEB_TOOLS,
    ),
    StageSpec(
        name="verify",
        role="Row verification",
        description=(
            "Checks every compliance-relevant row against the text and finds verbatim proof with its page; "
            "code re-checks the proof. A row without proof is dropped."
        ),
        prompt=prompts.VERIFIER,
        user_prompt="One row of the table, with the two documents' handles.",
        model="main",
        budget="One tool loop per row, in parallel",
        tools=VERIFY_TOOLS,
    ),
    StageSpec(
        name="report",
        role="Report",
        description=(
            "Writes the narrative of the report from the verified table; code adds the editorial and "
            "relocated rows, the changelog section, the methodology, the cost and the disclaimer."
        ),
        prompt=prompts.REPORT_WRITER,
        user_prompt="The counts, the actionable rows and the changelog summary as JSON.",
        model="main",
        budget="One model call, no tools",
    ),
)


CHANGE_ANALYZER = SubagentSpec(
    name="change_analyzer",
    role="Version comparison",
    description=(
        "Compares two versions of a document and builds a change table (one row per change: clause, "
        "nature, compliance requirement, binding, scope), with every compliance-relevant row checked "
        "against the text and a written report. Pass documents=[current, previous]. It also finds "
        "and uses a published changelog (in the documents and, for public documents, online). "
        "Pass analysis_id and row_ids instead to verify rows of an existing table."
    ),
    prompt=prompts.change_analyzer_overview(),
    # Tools its tool loops (changelog search, row verification) may use; the web tools are only
    # offered for public documents.
    tools=("search", "grep", "read_pages", "get_outline", "page_vision", "load_table", "web_search", "fetch_url"),
    stages=CHANGE_ANALYZER_STAGES,
)

SUBAGENTS: dict[str, SubagentSpec] = {s.name: s for s in (CHANGE_ANALYZER,)}
