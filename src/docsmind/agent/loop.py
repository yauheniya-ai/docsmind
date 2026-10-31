"""The tool-use loop shared by the executor and every subagent.

    model call -> tool calls? -> run them (in parallel) -> feed results back -> repeat

Guard rails live here, in code: a hard step budget (the last step has tools
disabled so the model must answer), parallel execution with a concurrency cap,
and context pruning so a long investigation doesn't overflow the window.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from docsmind.agent.harness.context import PromptBundle
from docsmind.agent.harness.tokens import message_tokens
from docsmind.agent.tools.base import Emit, ToolContext, ToolRegistry
from docsmind.domain.agent import AgentEvent, LLMResponse, Message, ToolCall, Usage
from docsmind.domain.ports import LLM

log = logging.getLogger(__name__)

ELIDED = "[Earlier tool result removed to save context. Run the tool again if you need it.]"
PROTECTED_ROUNDS = 2  # most recent tool rounds are never pruned


@dataclass
class LoopResult:
    text: str
    steps: int
    exhausted: bool  # True if the step budget forced the final answer


def prune_messages(messages: list[Message], budget_tokens: int) -> list[Message]:
    """Elide the oldest tool results until the transcript fits the budget.

    Only tool *results* are dropped (they're the bulk); the assistant's calls
    stay, so the model still sees what it already tried. Source handles keep
    working because the registry lives outside the transcript."""
    total = sum(message_tokens(m) for m in messages)
    if total <= budget_tokens:
        return messages

    rounds = [i for i, m in enumerate(messages) if m.role == "assistant" and m.tool_calls]
    if len(rounds) <= PROTECTED_ROUNDS:
        return messages
    protect_from = rounds[-PROTECTED_ROUNDS]

    pruned = list(messages)
    for i in range(protect_from):
        if total <= budget_tokens:
            break
        m = pruned[i]
        if m.role == "tool" and m.content != ELIDED:
            total -= message_tokens(m)
            pruned[i] = m.model_copy(update={"content": ELIDED})
            total += message_tokens(pruned[i])
    return pruned


async def run_tool_loop(
    *,
    llm: LLM,
    bundle: PromptBundle,
    registry: ToolRegistry,
    ctx: ToolContext,
    usage: Usage,
    emit: Emit,
    max_steps: int,
    max_tokens: int,
    context_budget_tokens: int,
    stream_text: bool,
    event_tag: dict | None = None,
) -> LoopResult:
    """Run until the model answers without tool calls (or the budget ends).

    `event_tag` is merged into tool events so the UI can nest a subagent's
    activity under the delegate call that started it."""
    tag = event_tag or {}
    messages = list(bundle.messages)
    tools = bundle.tools
    semaphore = asyncio.Semaphore(ctx.config.agent_max_parallel_tools)

    async def on_text(text: str) -> None:
        emit(AgentEvent(type="token", data={"text": text}))

    async def run_one(call: ToolCall) -> Message:
        emit(AgentEvent(type="tool_start", data={"id": call.id, "name": call.name, "args": call.arguments, **tag}))
        async with semaphore:
            output = await registry.execute(call, ctx)
        emit(
            AgentEvent(
                type="tool_end",
                data={"id": call.id, "name": call.name, "ok": not output.is_error, "summary": output.summary, **tag},
            )
        )
        return Message(role="tool", tool_call_id=call.id, content=output.content, is_error=output.is_error)

    for step in range(1, max_steps + 1):
        final_step = step == max_steps
        messages = prune_messages(messages, context_budget_tokens)
        response: LLMResponse = await llm.complete(
            messages,
            system=bundle.system,
            tools=tools or None,
            tool_choice="none" if final_step else "auto",
            max_tokens=max_tokens,
            on_text=on_text if stream_text else None,
        )
        usage.add(response.usage)

        calls = [] if final_step else response.tool_calls
        if not calls:
            return LoopResult(text=response.text.strip(), steps=step, exhausted=final_step and bool(tools))

        if stream_text and response.text.strip():
            # Text before tool calls is narration, not the answer: tell the UI
            # to move it out of the answer area.
            emit(AgentEvent(type="note", data={"text": response.text.strip()}))

        messages.append(Message(role="assistant", content=response.text, tool_calls=calls))
        results = await asyncio.gather(*(run_one(c) for c in calls))
        messages.extend(results)

    # Unreachable: the final step always returns above.
    return LoopResult(text="", steps=max_steps, exhausted=True)  # pragma: no cover


EventCallback = Callable[[AgentEvent], None]
