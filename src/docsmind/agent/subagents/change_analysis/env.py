"""What every stage of one Change Analyzer run shares: the models, stores, tools, usage counters.

Two ways to call a model: `call` (one request, no tools) and `loop` (a tool loop in a fresh
context, as the executor does, with a tool subset and a step budget). Both add to the run's usage.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from docsmind.agent.catalog import Catalog
from docsmind.agent.harness.context import PromptBundle
from docsmind.agent.loop import run_tool_loop
from docsmind.agent.sources import SourceRegistry
from docsmind.agent.subagents.change_analysis.rows import extract_json
from docsmind.agent.tools.base import Emit, ToolContext, ToolRegistry
from docsmind.config import DocsmindConfig
from docsmind.domain.agent import AgentEvent, LLMResponse, Message, SystemBlock, Usage
from docsmind.domain.ports import LLM


def silent(_event: AgentEvent) -> None:
    """An `emit` for loops whose tool calls are not shown (one per row would flood the trace)."""


@dataclass
class LoopOutcome:
    text: str
    exhausted: bool
    vision_log: list[str] = field(default_factory=list)  # what page_vision returned in this loop
    tools_called: list[str] = field(default_factory=list)


@dataclass
class RunEnv:
    config: DocsmindConfig
    llm: LLM  # the main model: tool loops and the report
    fast_llm: LLM  # small calls: versions check, changelog matching
    scan_llm: LLM  # the long-context scan
    repo: Any
    changes: Any  # ChangeStore
    tools: ToolRegistry  # the Change Analyzer's tools (see specs.py)
    emit: Emit
    vision: Any = None
    embedder: Any = None
    usage: Usage = field(default_factory=Usage)
    usage_by_model: dict[str, Usage] = field(default_factory=dict)  # the same tokens, split by model (for the cost)
    usage_by_tool_model: dict[tuple[str, str], Usage] = field(default_factory=dict)  # ... by (tool, model)
    sources: SourceRegistry = field(default_factory=SourceRegistry)
    model_calls: int = 0
    tool_steps: int = 0
    model_call_limit: int = 0  # 0 = no limit; the request-wide cap on model calls (loops inside tools included)
    verify_retries: int = 0  # verification loops relaunched after a technical failure
    vision_reads: int = 0  # page_vision calls: their tokens are not reported by the vision adapter

    @property
    def budget_left(self) -> int | None:
        """Model calls left in this request (None: no limit)."""
        return max(0, self.model_call_limit - self.model_calls) if self.model_call_limit else None

    @property
    def budget_exhausted(self) -> bool:
        return self.budget_left == 0

    def _count(self, model: str, usage: Usage, tool: str = "other") -> None:
        self.usage.add(usage)
        self.usage_by_model.setdefault(model, Usage()).add(usage)
        self.usage_by_tool_model.setdefault((tool, model), Usage()).add(usage)

    async def call(
        self,
        llm: LLM,
        system: str,
        user: str,
        *,
        max_tokens: int,
        on_text: Callable[[str], Awaitable[None]] | None = None,
        tool: str = "other",
    ) -> LLMResponse:
        self.model_calls += 1
        response = await llm.complete(
            [Message(role="user", content=user)],
            system=[SystemBlock(text=system, cache=True)],
            max_tokens=max_tokens,
            on_text=on_text,
        )
        self._count(llm.model, response.usage, tool)
        return response

    async def json_call(
        self, llm: LLM, system: str, user: str, *, max_tokens: int = 1024, tool: str = "other"
    ) -> dict | None:
        response = await self.call(llm, system, user, max_tokens=max_tokens, tool=tool)
        return extract_json(response.text)

    async def loop(
        self,
        *,
        system: str,
        task: str,
        catalog: Catalog,
        tool_names: tuple[str, ...] | list[str],
        max_steps: int,
        max_tokens: int,
        emit: Emit | None = None,
        tag: dict | None = None,
        tool: str = "other",
    ) -> LoopOutcome:
        registry = self.tools.subset(tool_names).configured(self.config)
        bundle = PromptBundle(
            system=[
                SystemBlock(text=system),
                SystemBlock(text=f"The register (documents you may use):\n{catalog.render()}", cache=True),
            ],
            messages=[Message(role="user", content=task)],
            tools=registry.specs(),
        )
        loop_emit = emit or silent
        ctx = ToolContext(
            repo=self.repo,
            catalog=catalog,
            sources=self.sources,
            config=self.config,
            emit=loop_emit,
            delegate=None,  # no recursion
            embedder=self.embedder,
            vision=self.vision,
        )
        usage = Usage()
        try:
            result = await run_tool_loop(
                llm=self.llm,
                bundle=bundle,
                registry=registry,
                ctx=ctx,
                usage=usage,
                emit=loop_emit,
                max_steps=max_steps,
                max_tokens=max_tokens,
                context_budget_tokens=self.config.agent_context_budget_tokens,
                stream_text=False,
                event_tag=tag,
            )
        finally:
            self._count(self.llm.model, usage, tool)
            self.vision_reads += len(ctx.vision_log)
        self.tool_steps += result.steps
        self.model_calls += result.steps
        return LoopOutcome(result.text, result.exhausted, list(ctx.vision_log), list(result.tools_called))
