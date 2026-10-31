"""LLM adapter registry. SDKs are imported lazily inside each adapter, so a
bare `pip install docsmind` never needs openai/anthropic installed."""

from __future__ import annotations

from docsmind.config import DocsmindConfig
from docsmind.domain.errors import LLMConfigurationError
from docsmind.domain.ports import LLM


def create_llm(config: DocsmindConfig, *, model: str | None = None) -> LLM:
    """Build the configured LLM. `model` overrides `config.llm_model` (used
    for the fast model that routes, summarizes and writes memory)."""
    provider = config.llm_provider.lower()
    model = model or config.llm_model
    if provider == "openai":
        from docsmind.adapters.llm.openai_llm import OpenAILLM

        return OpenAILLM(
            api_key=config.openai_api_key,
            model=model,
            base_url=config.openai_base_url,
            reasoning_effort=config.openai_reasoning_effort,
        )
    if provider == "anthropic":
        from docsmind.adapters.llm.anthropic_llm import AnthropicLLM

        return AnthropicLLM(api_key=config.anthropic_api_key, model=model)
    raise LLMConfigurationError(
        f"Unknown DOCSMIND_LLM_PROVIDER '{config.llm_provider}'. Use 'anthropic' or 'openai'."
    )
