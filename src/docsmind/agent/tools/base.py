"""Tool plumbing: typed arguments, uniform error handling, output limits.

Each tool declares its arguments as a pydantic model. That one model is both
the JSON Schema shown to the LLM and the validator for what comes back, so
the two can't drift. Failures are returned to the model as error results
(so it can correct itself) rather than raised into the agent loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from docsmind.agent.catalog import Catalog
from docsmind.agent.sources import SourceRegistry
from docsmind.agent.util import truncate
from docsmind.config import DocsmindConfig
from docsmind.domain.agent import AgentEvent, ToolCall, ToolSpec
from docsmind.domain.errors import ToolInputError

log = logging.getLogger(__name__)

Emit = Callable[[AgentEvent], None]
DelegateFn = Callable[[str, str, "list[str] | None"], Awaitable[str]]


@dataclass
class ToolOutput:
    content: str  # what the model sees
    is_error: bool = False
    summary: str = ""  # one short line for the UI trace


@dataclass
class ToolContext:
    repo: Any  # PostgresRepository (or a test double with the same read methods)
    catalog: Catalog
    sources: SourceRegistry
    config: DocsmindConfig
    emit: Emit
    delegate: DelegateFn | None = None  # None for subagents: no recursion


class Tool:
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def spec(cls) -> ToolSpec:
        return ToolSpec(
            name=cls.name,
            description=cls.description,
            parameters=_clean_schema(cls.Args.model_json_schema()),
        )


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop pydantic's cosmetic `title` keys; they only cost tokens."""

    def walk(node: Any, *, in_properties: bool = False) -> Any:
        if isinstance(node, dict):
            return {
                k: walk(v, in_properties=(k == "properties"))
                for k, v in node.items()
                if in_properties or k != "title"
            }
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    cleaned = walk(schema)
    cleaned.setdefault("type", "object")
    return cleaned


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def names(self) -> list[str]:
        return list(self._tools)

    def subset(self, names: tuple[str, ...] | list[str]) -> ToolRegistry:
        return ToolRegistry([t for n, t in self._tools.items() if n in names])

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolOutput:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolOutput(
                f"Unknown tool '{call.name}'. Available: {', '.join(self._tools)}.", True, "unknown tool"
            )
        if call.parse_error:
            return ToolOutput(f"Could not read the arguments: {call.parse_error}. Retry.", True, "bad arguments")
        try:
            args = tool.Args.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolOutput(f"Invalid arguments: {_validation_message(exc)}", True, "invalid arguments")

        try:
            output = await asyncio.wait_for(tool.run(args, ctx), timeout=ctx.config.agent_tool_timeout_s)
        except ToolInputError as exc:
            return ToolOutput(str(exc), True, str(exc))
        except asyncio.TimeoutError:
            return ToolOutput("The tool timed out. Try a narrower request.", True, "timed out")
        except Exception:
            log.exception("Tool %s failed", call.name)
            return ToolOutput("The tool failed unexpectedly. Try a different approach.", True, "failed")

        output.content = truncate(output.content, ctx.config.agent_tool_result_max_chars)
        return output


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        where = ".".join(str(p) for p in err["loc"]) or "arguments"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)
