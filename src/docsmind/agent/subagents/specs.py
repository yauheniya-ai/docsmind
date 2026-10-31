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
    max_steps: int | None = None  # defaults to config.agent_subagent_max_steps


RESEARCHER = SubagentSpec(
    name="researcher",
    role="Evidence gathering",
    description=(
        "Investigates one focused question across the given documents and reports the "
        "evidence with citations. Use for large reads or to run several investigations in parallel."
    ),
    prompt=prompts.RESEARCHER,
    tools=("search", "grep", "read_pages", "get_outline"),
)

CHANGE_ANALYZER = SubagentSpec(
    name="change_analyzer",
    role="Version comparison",
    description=(
        "Compares two versions of a document (or two related documents) and reports what was "
        "added, removed or reworded, section by section, with citations on both sides."
    ),
    prompt=prompts.CHANGE_ANALYZER,
    tools=("search", "grep", "read_pages", "get_outline"),
)

SUBAGENTS: dict[str, SubagentSpec] = {s.name: s for s in (RESEARCHER, CHANGE_ANALYZER)}
