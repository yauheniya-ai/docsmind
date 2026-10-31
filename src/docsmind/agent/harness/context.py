"""The context builder: the one place that assembles every prompt.

Deterministic code, not a model. Given the state of a turn it decides what
the model sees, in what order, within what budget:

    [ role prompt + rules ]  cache   <- identical every call
    [ document register    ]  cache   <- changes only when documents change
    [ memory / summary / plan ]       <- changes per turn (after the cache mark)
    [ recent history, then the new question ]

Putting stable content first is what makes provider prompt caching work, and
keeping this logic in one file is what makes the context auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from docsmind.agent import prompts
from docsmind.agent.catalog import Catalog
from docsmind.agent.harness.tokens import message_tokens
from docsmind.agent.sources import strip_markers
from docsmind.agent.subagents.specs import SubagentSpec
from docsmind.config import DocsmindConfig
from docsmind.domain.agent import (
    MemoryItem,
    Message,
    Plan,
    StoredMessage,
    SystemBlock,
    ToolSpec,
)


@dataclass
class TurnState:
    catalog: Catalog
    question: str
    history: list[StoredMessage] = field(default_factory=list)  # not yet summarized
    summary: str | None = None
    memory: list[MemoryItem] = field(default_factory=list)
    interpreted_question: str | None = None
    plan: Plan | None = None


@dataclass
class PromptBundle:
    system: list[SystemBlock]
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)


class ContextBuilder:
    def __init__(self, config: DocsmindConfig) -> None:
        self.config = config

    # -- pieces --------------------------------------------------------------

    @staticmethod
    def _register_block(catalog: Catalog) -> SystemBlock:
        return SystemBlock(text=f"The register (documents you may use):\n{catalog.render()}", cache=True)

    def _volatile_block(self, state: TurnState, *, with_plan: bool) -> SystemBlock | None:
        parts: list[str] = []

        notes = self._memory_lines(state)
        if notes:
            parts.append(
                "<memory>\nNotes kept from earlier conversations. They can be outdated or wrong; "
                "verify against the documents before relying on them.\n" + "\n".join(notes) + "\n</memory>"
            )
        if state.summary:
            parts.append(f"<conversation_summary>\n{state.summary}\n</conversation_summary>")
        if state.interpreted_question and state.interpreted_question.strip() != state.question.strip():
            parts.append(f"<interpreted_question>\n{state.interpreted_question}\n</interpreted_question>")
        if with_plan and state.plan and state.plan.steps:
            steps = "\n".join(f"{i}. {s}" for i, s in enumerate(state.plan.steps, start=1))
            parts.append(
                f"<plan>\nFollow this plan; adapt it if the evidence demands.\n{steps}\n</plan>"
            )
        if not parts:
            return None
        return SystemBlock(text="\n\n".join(parts), cache=False)

    @staticmethod
    def _memory_lines(state: TurnState) -> list[str]:
        lines: list[str] = []
        for item in state.memory:
            if item.kind == "fact":
                lines.append(f"- {item.content}")
            elif item.version_id is not None:
                doc = state.catalog.by_version(item.version_id)
                if doc:
                    lines.append(f"- [{doc.handle}] {item.content}")
        return lines

    def _history(self, state: TurnState, *, max_pairs: int | None = None) -> list[Message]:
        """Prior turns as messages, newest kept first, within the history
        budget. Citation markers are stripped: handles are per turn and would
        collide with this turn's."""
        stored = state.history
        if max_pairs is not None:
            stored = stored[-max_pairs * 2 :]
        messages = [Message(role=m.role, content=strip_markers(m.content)) for m in stored]

        kept: list[Message] = []
        used = 0
        for message in reversed(messages):
            cost = message_tokens(message)
            if used + cost > self.config.agent_history_budget_tokens and kept:
                break
            kept.append(message)
            used += cost
        kept.reverse()
        while kept and kept[0].role != "user":  # providers want a user turn first
            kept.pop(0)
        return kept

    # -- bundles per role ----------------------------------------------------------

    def orchestrator(self, state: TurnState) -> PromptBundle:
        system = [SystemBlock(text=prompts.ROUTER), self._register_block(state.catalog)]
        volatile = self._volatile_block(state, with_plan=False)
        if volatile:
            system.append(volatile)
        messages = [*self._history(state, max_pairs=2), Message(role="user", content=state.question)]
        return PromptBundle(system=system, messages=messages)

    def planner(self, state: TurnState) -> PromptBundle:
        system = [SystemBlock(text=prompts.PLANNER), self._register_block(state.catalog)]
        volatile = self._volatile_block(state, with_plan=False)
        if volatile:
            system.append(volatile)
        question = state.interpreted_question or state.question
        return PromptBundle(system=system, messages=[Message(role="user", content=question)])

    def executor(self, state: TurnState, tools: list[ToolSpec]) -> PromptBundle:
        # One cache breakpoint, at the end of the stable prefix (role + register).
        system = [SystemBlock(text=prompts.EXECUTOR), self._register_block(state.catalog)]
        volatile = self._volatile_block(state, with_plan=True)
        if volatile:
            system.append(volatile)
        messages = [*self._history(state), Message(role="user", content=state.question)]
        return PromptBundle(system=system, messages=messages, tools=tools)

    def subagent(
        self, spec: SubagentSpec, task: str, catalog: Catalog, tools: list[ToolSpec]
    ) -> PromptBundle:
        """Fresh context by design: no history, no memory, no plan — just the
        role, the register and the task."""
        system = [SystemBlock(text=spec.prompt), self._register_block(catalog)]
        return PromptBundle(
            system=system, messages=[Message(role="user", content=task)], tools=tools
        )
