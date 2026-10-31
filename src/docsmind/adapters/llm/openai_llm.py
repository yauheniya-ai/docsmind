"""OpenAI (and OpenAI-compatible) chat-completions adapter.

Prompt caching on OpenAI is automatic for a stable prompt prefix, so this
adapter just keeps the system blocks in the order the harness gave them
(stable first) and doesn't need explicit cache markers.
"""

from __future__ import annotations

import json
import logging
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

_FINISH: dict[str, StopReason] = {"stop": "end", "tool_calls": "tool_use", "length": "max_tokens"}
log = logging.getLogger(__name__)


def parse_tool_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    """Models occasionally emit malformed JSON for tool arguments. Don't crash
    the turn: report it so the tool layer can tell the model to retry."""
    if not raw.strip():
        return {}, None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"arguments were not valid JSON ({exc.msg})"
    if not isinstance(value, dict):
        return {}, "arguments must be a JSON object"
    return value, None


class OpenAILLM:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise LLMConfigurationError(
                "OpenAI API key is not set. Add DOCSMIND_OPENAI_API_KEY to your .env "
                "(or set DOCSMIND_LLM_PROVIDER=anthropic)."
            )
        try:
            from openai import AsyncOpenAI, BadRequestError
        except ImportError as exc:  # pragma: no cover - import guard
            raise LLMConfigurationError(
                'The openai package is not installed. Run `pip install "docsmind[openai]"`.'
            ) from exc
        self.model = model
        self._bad_request = BadRequestError
        self._reasoning_effort = reasoning_effort
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=3, timeout=timeout)

    @staticmethod
    def _convert(messages: list[Message], system: list[SystemBlock]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        system_text = "\n\n".join(b.text for b in system if b.text.strip())
        if system_text:
            out.append({"role": "system", "content": system_text})
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                item: dict[str, Any] = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    item["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ]
                out.append(item)
            else:
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
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
            "messages": self._convert(messages, system),
            "max_completion_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
            kwargs["tool_choice"] = tool_choice

        text_parts: list[str] = []
        partial: dict[int, dict[str, str]] = {}
        finish: str | None = None
        usage = Usage()

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except self._bad_request as exc:
            # Some reasoning models refuse function tools on chat-completions
            # unless reasoning is off. Remember that and retry once.
            if not (tools and "reasoning_effort" in str(exc) and self._reasoning_effort != "none"):
                raise
            log.warning("%s rejects tools with reasoning; retrying with reasoning_effort='none'", self.model)
            self._reasoning_effort = "none"
            kwargs["reasoning_effort"] = "none"
            stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.usage:
                details = getattr(chunk.usage, "prompt_tokens_details", None)
                usage = Usage(
                    input_tokens=chunk.usage.prompt_tokens or 0,
                    output_tokens=chunk.usage.completion_tokens or 0,
                    cached_input_tokens=(getattr(details, "cached_tokens", 0) or 0) if details else 0,
                )
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta and delta.content:
                text_parts.append(delta.content)
                if on_text:
                    await on_text(delta.content)
            for tc in (delta.tool_calls or []) if delta else []:
                slot = partial.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function.arguments:
                        slot["args"] += tc.function.arguments
            if choice.finish_reason:
                finish = choice.finish_reason

        calls: list[ToolCall] = []
        for index in sorted(partial):
            slot = partial[index]
            args, error = parse_tool_arguments(slot["args"])
            calls.append(
                ToolCall(
                    id=slot["id"] or f"call_{index}",
                    name=slot["name"],
                    arguments=args,
                    parse_error=error,
                )
            )
        stop: StopReason = "tool_use" if calls else _FINISH.get(finish or "", "other")
        return LLMResponse(text="".join(text_parts), tool_calls=calls, stop_reason=stop, usage=usage)
