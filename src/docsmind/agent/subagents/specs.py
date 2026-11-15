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


# Core tools shared by every agent, and the Change Analyzer's own table and step tools. The web tools
# are offered to a loop only for public documents (a query about an internal document would leak it).
LOCAL_TOOLS = ("search", "grep", "read_pages", "get_outline", "load_table", "page_vision")
WEB_TOOLS = ("web_search", "read_url")
FILE_TOOLS = ("create_file",)
TABLE_TOOLS = ("get_table", "get_row", "add_row", "update_row", "archive_row", "restore_row")
STEP_TOOLS = ("check_versions", "scan_documents", "find_changelog", "verify_findings", "write_report")

# What the inner loops (changelog search, row verification) may call: any core tool, no step limits.
CHANGELOG_LOCAL_TOOLS = LOCAL_TOOLS
CHANGELOG_WEB_TOOLS = WEB_TOOLS
VERIFY_TOOLS = LOCAL_TOOLS


CHANGE_ANALYZER = SubagentSpec(
    name="change_analyzer",
    role="Version comparison",
    description=(
        "Compares two versions of a document and keeps ONE change table per pair (one row per change: clause, "
        "nature, compliance requirement, binding, scope), every compliance-relevant row checked against the "
        "text, plus a written report. It also works on an existing table: verify a row, review a chapter in "
        "depth, check the changelog, add a row. Pass the user's request verbatim and either "
        "documents=[current, previous] or the analysis_id."
    ),
    prompt=prompts.CHANGE_ANALYZER_AGENT,  # the agent's own system prompt; the tools' model prompts belong to the tools
    tools=LOCAL_TOOLS + WEB_TOOLS + FILE_TOOLS + TABLE_TOOLS + STEP_TOOLS,
)

SUBAGENTS: dict[str, SubagentSpec] = {s.name: s for s in (CHANGE_ANALYZER,)}
