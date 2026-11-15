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
        min_length=3,
        max_length=2000,
        description=(
            "The user's request, in their own words and complete: the subagent has NO access to this "
            "conversation. E.g. 'Compare these versions', 'Review chapter 3 in depth', 'Verify row R14', "
            "'Check the changelog against the table'."
        ),
    )
    documents: list[str] | None = Field(
        default=None,
        description=(
            "Exactly two handles, [current version, previous version] (the newer one first). Not needed "
            "when analysis_id is given. For a pair that was compared before, the existing table is used."
        ),
    )
    analysis_id: str | None = Field(
        default=None, description="Id of an existing change analysis, to work on its table (instead of documents)."
    )


class DelegateTool(Tool):
    name = "delegate"
    tier = "mind"
    long_running = True
    summary = "Hands a comparison of two document versions to the Change Analyzer, which works in its own fresh context."
    description = (
        "Delegate to a subagent, which works in its own fresh context and returns a short status. "
        "It can take minutes.\nSubagents:\n" + _ROSTER
    )
    how_it_works = (
        "The Mind passes the user's request and the two document handles (current first, previous second) or an analysis id; the subagent never sees the conversation.",
        "The Change Analyzer is an agent: it loads the change table of the pair (or starts one) and decides its own steps: check versions, scan, changelog, verification, report.",
        "The table and the report are stored and shown next to the chat; only a short status and the agent's answer come back to the Mind.",
    )
    Args = DelegateArgs

    async def run(self, args: DelegateArgs, ctx: ToolContext) -> ToolOutput:
        if ctx.delegate is None:
            raise ToolInputError("Delegation is not available here.")
        if args.subagent not in SUBAGENTS:
            raise ToolInputError(f"Unknown subagent '{args.subagent}'. Choose from: {', '.join(SUBAGENTS)}.")
        ctx.catalog.resolve_many(args.documents)  # validate handles before spending a model call
        if args.analysis_id is None and (
            not args.documents or len(args.documents) != 2 or args.documents[0] == args.documents[1]
        ):
            raise ToolInputError(
                "Pass documents=[current, previous]: two different document handles, the newer version first."
            )
        extra = {"analysis_id": args.analysis_id}
        report = await ctx.delegate(args.subagent, args.task, args.documents, extra)
        return ToolOutput(report, summary=f"{args.subagent} reported")
