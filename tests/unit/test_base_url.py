"""A provider key plus a base URL (company gateway) reaches every client."""

from __future__ import annotations

import pytest

from docsmind.adapters.llm import create_llm
from docsmind.adapters.vision import create_captioner
from docsmind.config import DocsmindConfig


def _config(**overrides) -> DocsmindConfig:
    return DocsmindConfig(_env_file=None, **overrides)


def test_blank_base_url_means_default() -> None:
    config = _config(openai_base_url="  ", anthropic_base_url="")
    assert config.openai_base_url is None
    assert config.anthropic_base_url is None


def test_base_url_is_trimmed() -> None:
    config = _config(openai_base_url=" https://gw.example.com/v1/ ")
    assert config.openai_base_url == "https://gw.example.com/v1"


def test_openai_llm_uses_base_url() -> None:
    pytest.importorskip("openai")
    config = _config(
        llm_provider="openai", llm_model="gpt-6-luna", openai_api_key="k", openai_base_url="https://gw.example.com/v1"
    )
    llm = create_llm(config)
    assert str(llm._client.base_url).startswith("https://gw.example.com/v1")


def test_anthropic_llm_uses_base_url() -> None:
    pytest.importorskip("anthropic")
    config = _config(
        llm_provider="anthropic", anthropic_api_key="k", anthropic_base_url="https://gw.example.com/anthropic"
    )
    llm = create_llm(config)
    assert str(llm._client.base_url).startswith("https://gw.example.com/anthropic")


def test_anthropic_captioner_uses_base_url() -> None:
    pytest.importorskip("anthropic")
    config = _config(
        vision_provider="anthropic", anthropic_api_key="k", anthropic_base_url="https://gw.example.com/anthropic"
    )
    captioner = create_captioner(config)
    assert str(captioner._client.base_url).startswith("https://gw.example.com/anthropic")
