"""Subagent definitions. A subagent is a role (prompt + tool subset) that runs
the same tool loop as the executor, but in a fresh context and returning only
a short report — so big reading jobs don't bloat the main conversation."""

from __future__ import annotations

from dataclasses import dataclass

from docsmind.agent import prompts


@dataclass(frozen=True)
class SubagentSpec:
    name: str
    role: str
    description: str  # shown to the Mind (tool description) and in the UI
    prompt: str
    tools: tuple[str, ...]


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
)

SUBAGENTS: dict[str, SubagentSpec] = {s.name: s for s in (CHANGE_ANALYZER,)}
