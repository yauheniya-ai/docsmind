"""Vision providers for image captioning: Anthropic and OpenAI."""

from __future__ import annotations

from docsmind.config import DocsmindConfig
from docsmind.domain.errors import EnrichmentConfigurationError
from docsmind.enrichment.pricing import resolve_vision_model


def create_captioner(config: DocsmindConfig):
    provider = config.vision_provider
    model = resolve_vision_model(provider, config.vision_model)
    if provider == "anthropic":
        from docsmind.adapters.vision.captioners import AnthropicCaptioner

        return AnthropicCaptioner(
            api_key=config.anthropic_api_key, model=model, max_output_tokens=config.vision_max_output_tokens
        )
    if provider == "openai":
        from docsmind.adapters.vision.captioners import OpenAICaptioner

        return OpenAICaptioner(
            api_key=config.openai_api_key,
            model=model,
            base_url=config.openai_base_url,
            max_output_tokens=config.vision_max_output_tokens,
            reasoning_effort=config.openai_reasoning_effort,
        )
    raise EnrichmentConfigurationError(f"Unknown vision_provider {provider!r}. Supported: anthropic, openai.")
