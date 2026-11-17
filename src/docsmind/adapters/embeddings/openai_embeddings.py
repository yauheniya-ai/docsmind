"""OpenAI (and OpenAI-compatible) embeddings adapter. Satisfies `domain.ports.Embedder`."""

from __future__ import annotations

from docsmind.domain.errors import EnrichmentConfigurationError

# The API rejects inputs over 8191 tokens; page chunks are far smaller, but a
# dense page of tiny text can get there. Truncate rather than fail the batch.
MAX_INPUT_TOKENS = 8000
_MAX_REQUEST_TOKENS = 250_000  # API limit is 300k tokens per request


class OpenAIEmbedder:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        dimensions: int,
        base_url: str | None = None,
        batch_size: int = 64,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise EnrichmentConfigurationError(
                "OpenAI API key is not set. Add DOCSMIND_OPENAI_API_KEY to your .env to create embeddings."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - import guard
            raise EnrichmentConfigurationError(
                'The openai package is not installed. Run `pip install docsmind`.'
            ) from exc
        self.model_name = model
        self.dimensions = dimensions
        self._batch_size = max(1, batch_size)
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=4, timeout=timeout)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in self._batches([_truncate(t) for t in texts]):
            kwargs: dict = {"model": self.model_name, "input": batch}
            # Only the v3 models accept (and need) a `dimensions` argument.
            if "text-embedding-3" in self.model_name:
                kwargs["dimensions"] = self.dimensions
            response = await self._client.embeddings.create(**kwargs)
            ordered = sorted(response.data, key=lambda d: d.index)
            vectors.extend([list(d.embedding) for d in ordered])
        if len(vectors) != len(texts):
            raise RuntimeError(f"Embeddings API returned {len(vectors)} vectors for {len(texts)} inputs.")
        return vectors

    def _batches(self, texts: list[str]):
        batch: list[str] = []
        tokens = 0
        for text in texts:
            estimate = max(1, len(text) // 3)  # conservative: ~3 chars/token
            if batch and (len(batch) >= self._batch_size or tokens + estimate > _MAX_REQUEST_TOKENS):
                yield batch
                batch, tokens = [], 0
            batch.append(text)
            tokens += estimate
        if batch:
            yield batch


def _truncate(text: str) -> str:
    # The API also rejects empty strings; a single space embeds harmlessly.
    text = text if text.strip() else " "
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text, disallowed_special=())
        if len(tokens) > MAX_INPUT_TOKENS:
            return encoding.decode(tokens[:MAX_INPUT_TOKENS])
        return text
    except Exception:  # noqa: BLE001 - fall back to a character cap
        return text[: MAX_INPUT_TOKENS * 3]
