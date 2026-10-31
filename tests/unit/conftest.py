"""Fixtures for the agent tests (fakes live in fakes.py)."""

from __future__ import annotations

import pytest
from fakes import FakeRepo, ScriptedLLM

from docsmind.config import DocsmindConfig


@pytest.fixture
def config() -> DocsmindConfig:
    return DocsmindConfig(
        _env_file=None, agent_max_steps=4, agent_subagent_max_steps=3, agent_history_budget_tokens=400
    )


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()
