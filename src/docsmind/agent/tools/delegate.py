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
        default="",
        max_length=2000,
        description=(
            "Optional notes for the subagent, which has NO access to this conversation: what the user "
            "cares about (a topic, a kind of change), or why rows are being verified."
        ),
    )
    documents: list[str] | None = Field(
        default=None,
        description=(
            "Comparison: exactly two handles, [current version, previous version] (the newer one first). "
            "Not needed when analysis_id is given."
        ),
    )
    analysis_id: str | None = Field(
        default=None, description="Id of an existing change analysis, to verify rows of its table (with row_ids)."
    )
    row_ids: list[str] | None = Field(
        default=None, max_length=50, description="With analysis_id: the ids of the rows to verify again."
    )


class DelegateTool(Tool):
    name = "delegate"
    long_running = True
    summary = "Hands a comparison of two document versions to the Change Analyzer, which works in its own fresh context."
    description = (
        "Delegate to a subagent, which works in its own fresh context and returns a short status. "
        "It can take minutes.\nSubagents:\n" + _ROSTER
    )
    how_it_works = (
        "The Mind passes the two document handles (current first, previous second); the subagent never sees the conversation.",
        "Code drives the analysis: prepare and diff the texts, scan for changes, check them against the text, write the report.",
        "The change table and the written report are stored and shown next to the chat; only a short status comes back to the Mind.",
    )
    Args = DelegateArgs

    async def run(self, args: DelegateArgs, ctx: ToolContext) -> ToolOutput:
        if ctx.delegate is None:
            raise ToolInputError("Delegation is not available here.")
        if args.subagent not in SUBAGENTS:
            raise ToolInputError(f"Unknown subagent '{args.subagent}'. Choose from: {', '.join(SUBAGENTS)}.")
        ctx.catalog.resolve_many(args.documents)  # validate handles before spending a model call
        if args.analysis_id is None:
            if not args.documents or len(args.documents) != 2 or args.documents[0] == args.documents[1]:
                raise ToolInputError(
                    "Pass documents=[current, previous]: two different document handles, the newer version first."
                )
        elif not args.row_ids:
            raise ToolInputError("Pass row_ids with analysis_id: the rows to verify.")
        extra = {"analysis_id": args.analysis_id, "row_ids": args.row_ids}
        report = await ctx.delegate(args.subagent, args.task, args.documents, extra)
        return ToolOutput(report, summary=f"{args.subagent} reported")
