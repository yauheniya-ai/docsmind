"""Embedding providers. Today: OpenAI (and OpenAI-compatible gateways)."""

from __future__ import annotations

from docsmind.config import DocsmindConfig
from docsmind.domain.errors import EnrichmentConfigurationError


def create_embedder(config: DocsmindConfig):
    if config.embedding_provider == "openai":
        from docsmind.adapters.embeddings.openai_embeddings import OpenAIEmbedder

        return OpenAIEmbedder(
            api_key=config.openai_api_key,
            model=config.embedding_model,
            dimensions=config.embedding_dimensions,
            base_url=config.openai_base_url,
            batch_size=config.embedding_batch_size,
        )
    raise EnrichmentConfigurationError(
        f"Unknown embedding_provider {config.embedding_provider!r}. Supported: openai."
    )
