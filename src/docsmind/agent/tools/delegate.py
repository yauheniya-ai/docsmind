"""The `delegate` tool: how the Mind hands work to a subagent.

The tool itself is a thin shim — the actual subagent run is wired in by the
agent (`ToolContext.delegate`) because it needs the LLM and the tool loop.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from docsmind.agent.subagents.specs import SUBAGENTS
from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput
from docsmind.domain.errors import ToolInputError

_ROSTER = "\n".join(f"- {s.name}: {s.description}" for s in SUBAGENTS.values())


class DelegateArgs(BaseModel):
    subagent: str = Field(description=f"Which subagent: one of {', '.join(SUBAGENTS)}.")
    task: str = Field(
        min_length=10,
        description=(
            "A complete, self-contained task. The subagent has NO access to this conversation: "
            "name the documents (handles), the exact question, and what to report."
        ),
    )
    documents: list[str] | None = Field(
        default=None, description="Document handles the subagent may use. Omit for all documents."
    )


class DelegateTool(Tool):
    name = "delegate"
    summary = "Hands a self-contained task to a subagent that works in its own fresh context."
    description = (
        "Delegate a self-contained task to a subagent that works in its own fresh context and returns a "
        "short report with [S#] citations. Use for large or independent work; several delegate calls in "
        "one step run in parallel.\nSubagents:\n" + _ROSTER
    )
    Args = DelegateArgs

    async def run(self, args: DelegateArgs, ctx: ToolContext) -> ToolOutput:
        if ctx.delegate is None:
            raise ToolInputError("Delegation is not available here.")
        if args.subagent not in SUBAGENTS:
            raise ToolInputError(f"Unknown subagent '{args.subagent}'. Choose from: {', '.join(SUBAGENTS)}.")
        ctx.catalog.resolve_many(args.documents)  # validate handles before spending a model call
        report = await ctx.delegate(args.subagent, args.task, args.documents)
        return ToolOutput(report, summary=f"{args.subagent} reported")
