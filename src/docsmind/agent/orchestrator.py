"""Router: one call to the small model that decides how a turn is handled: `simple` -> straight to the
executor, `complex` -> planner first, `delegate` -> straight to the executor, which hands the request to
the Change Analyzer (no plan needed). It only routes: the user's message is never rewritten.

Deterministic fallback: any failure or unparseable reply routes `simple`, because the executor can handle
a hard question with its tools, whereas a broken router must never block an answer.
"""

from __future__ import annotations

import logging

from docsmind.agent.harness.context import ContextBuilder, TurnState
from docsmind.agent.util import parse_json_object
from docsmind.domain.agent import RouteDecision, Usage
from docsmind.domain.ports import LLM

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, llm: LLM, builder: ContextBuilder) -> None:
        self.llm = llm
        self.builder = builder

    async def route(self, state: TurnState, usage: Usage) -> RouteDecision:
        bundle = self.builder.orchestrator(state)
        try:
            response = await self.llm.complete(bundle.messages, system=bundle.system, max_tokens=300)
        except Exception:
            log.exception("Router call failed; defaulting to the simple route")
            return RouteDecision(route="simple")
        usage.add(response.usage)

        data = parse_json_object(response.text) or {}
        word = str(data.get("route", "")).lower()
        route = word if word in ("complex", "delegate") else "simple"
        return RouteDecision(route=route)
