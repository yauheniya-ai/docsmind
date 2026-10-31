"""Planner: for complex questions, writes a short step list that the executor
then follows. Planning is advisory — it focuses the executor, it doesn't
constrain it — so a failed plan simply means "no plan"."""

from __future__ import annotations

import logging

from docsmind.agent.harness.context import ContextBuilder, TurnState
from docsmind.agent.util import parse_json_object
from docsmind.domain.agent import Plan, Usage
from docsmind.domain.ports import LLM

log = logging.getLogger(__name__)

MAX_STEPS = 6


class Planner:
    def __init__(self, llm: LLM, builder: ContextBuilder) -> None:
        self.llm = llm
        self.builder = builder

    async def plan(self, state: TurnState, usage: Usage) -> Plan | None:
        bundle = self.builder.planner(state)
        try:
            response = await self.llm.complete(bundle.messages, system=bundle.system, max_tokens=500)
        except Exception:
            log.exception("Planner call failed; continuing without a plan")
            return None
        usage.add(response.usage)

        data = parse_json_object(response.text) or {}
        raw = data.get("steps")
        if not isinstance(raw, list):
            return None
        steps = [" ".join(s.split()) for s in raw if isinstance(s, str) and s.strip()][:MAX_STEPS]
        return Plan(steps=steps) if steps else None
