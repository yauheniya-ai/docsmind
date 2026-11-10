"""Vision captioners: turn an image into a short, searchable description.

Satisfies `domain.ports.VisionCaptioner`. Captions exist so figures, charts
and diagrams can be found by text search and read by the agent; they are
written to be retrieval-friendly (type of figure, visible text, key takeaway).
"""

from __future__ import annotations

import base64

from docsmind.domain.errors import EnrichmentConfigurationError

CAPTION_PROMPT = (
    "This image was extracted from a document page. Write a caption that helps someone find it by "
    "searching text. Say what kind of figure it is (chart, diagram, table, photo, logo, screenshot...), "
    "transcribe any legible text, labels and numbers, and state the main point it conveys. "
    "Plain prose, at most 120 words, no preamble. Logos, seals and banners count as content: describe them "
    "and transcribe their text. Only if the image has no content at all (a blank area, a plain line, "
    "border or background fill) reply exactly: decorative"
)


def _clean(caption: str) -> str:
    return " ".join(caption.split())


class OpenAICaptioner:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str | None = None,
        max_output_tokens: int = 300,
        reasoning_effort: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise EnrichmentConfigurationError(
                "OpenAI API key is not set. Add DOCSMIND_OPENAI_API_KEY to your .env to caption images."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - import guard
            raise EnrichmentConfigurationError(
                'The openai package is not installed. Run `pip install "docsmind[openai]"`.'
            ) from exc
        self.model_name = model
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=4, timeout=timeout)

    async def caption(self, image_bytes: bytes, *, mime_type: str) -> str:
        data = base64.b64encode(image_bytes).decode("ascii")
        kwargs: dict = {
            "model": self.model_name,
            "max_completion_tokens": self._max_output_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CAPTION_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{data}"}},
                    ],
                }
            ],
        }
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        response = await self._client.chat.completions.create(**kwargs)
        return _clean(response.choices[0].message.content or "")


class AnthropicCaptioner:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        max_output_tokens: int = 300,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise EnrichmentConfigurationError(
                "Anthropic API key is not set. Add DOCSMIND_ANTHROPIC_API_KEY to your .env, or set "
                "DOCSMIND_VISION_PROVIDER=openai."
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - import guard
            raise EnrichmentConfigurationError(
                'The anthropic package is not installed. Run `pip install "docsmind[anthropic]"`.'
            ) from exc
        self.model_name = model
        self._max_output_tokens = max_output_tokens
        self._client = AsyncAnthropic(api_key=api_key, max_retries=4, timeout=timeout)

    async def caption(self, image_bytes: bytes, *, mime_type: str) -> str:
        data = base64.b64encode(image_bytes).decode("ascii")
        response = await self._client.messages.create(
            model=self.model_name,
            max_tokens=self._max_output_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": data}},
                        {"type": "text", "text": CAPTION_PROMPT},
                    ],
                }
            ],
        )
        return _clean("".join(b.text for b in response.content if getattr(b, "type", "") == "text"))
