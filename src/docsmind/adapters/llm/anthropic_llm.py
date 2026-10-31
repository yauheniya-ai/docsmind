"""Anthropic messages adapter.

Prompt caching is explicit on Anthropic: the harness orders system blocks
stable-first and flags where the stable prefix ends (`SystemBlock.cache`);
those become `cache_control` breakpoints here. Tools are rendered before the
system prompt in Anthropic's cache order, so they're covered by the same
breakpoint.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from docsmind.domain.agent import (
    LLMResponse,
    Message,
    StopReason,
    SystemBlock,
    ToolCall,
    ToolChoice,
    ToolSpec,
    Usage,
)
from docsmind.domain.errors import LLMConfigurationError

_STOP: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
}


class AnthropicLLM:
    def __init__(self, *, api_key: str | None, model: str, timeout: float = 120.0) -> None:
        if not api_key:
            raise LLMConfigurationError(
                "Anthropic API key is not set. Add DOCSMIND_ANTHROPIC_API_KEY to your .env "
                "(or set DOCSMIND_LLM_PROVIDER=openai)."
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - import guard
            raise LLMConfigurationError(
                'The anthropic package is not installed. Run `pip install "docsmind[anthropic]"`.'
            ) from exc
        self.model = model
        self._client = AsyncAnthropic(api_key=api_key, max_retries=3, timeout=timeout)

    @staticmethod
    def _system(blocks: list[SystemBlock]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for b in blocks:
            if not b.text.strip():
                continue
            item: dict[str, Any] = {"type": "text", "text": b.text}
            if b.cache:
                item["cache_control"] = {"type": "ephemeral"}
            out.append(item)
        return out

    @staticmethod
    def _convert(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []

        def flush() -> None:
            if pending_results:
                out.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for m in messages:
            if m.role == "tool":
                # All results for one assistant turn go in a single user message.
                pending_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content or "(empty)",
                        "is_error": m.is_error,
                    }
                )
                continue
            flush()
            if m.role == "user":
                out.append({"role": "user", "content": m.content})
            else:
                blocks: list[dict[str, Any]] = []
                if m.content.strip():
                    blocks.append({"type": "text", "text": m.content})
                blocks.extend(
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in m.tool_calls
                )
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
        flush()
        return out

    async def complete(
        self,
        messages: list[Message],
        *,
        system: list[SystemBlock],
        tools: list[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        max_tokens: int = 1024,
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": self._system(system),
            "messages": self._convert(messages),
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
            kwargs["tool_choice"] = {"type": tool_choice}

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                if on_text and text:
                    await on_text(text)
            final = await stream.get_final_message()

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in final.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input) if isinstance(block.input, dict) else {},
                    )
                )
        u = final.usage
        usage = Usage(
            input_tokens=(u.input_tokens or 0)
            + (getattr(u, "cache_read_input_tokens", 0) or 0)
            + (getattr(u, "cache_creation_input_tokens", 0) or 0),
            output_tokens=u.output_tokens or 0,
            cached_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )
        stop: StopReason = "tool_use" if calls else _STOP.get(final.stop_reason or "", "other")
        return LLMResponse(text="".join(text_parts), tool_calls=calls, stop_reason=stop, usage=usage)
