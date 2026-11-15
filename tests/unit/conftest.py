"""Fixtures for the agent tests (fakes live in fakes.py)."""

from __future__ import annotations

import pytest
from fakes import FakeRepo, ScriptedLLM

from docsmind.adapters.storage.memory import (
    InMemoryChangeStore,
    InMemoryConversationStore,
    InMemoryMemoryStore,
)
from docsmind.agent.agent import DocsmindAgent
from docsmind.config import DocsmindConfig


@pytest.fixture
def config() -> DocsmindConfig:
    return DocsmindConfig(
        _env_file=None, agent_max_steps=4, agent_history_budget_tokens=400
    )


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def changes() -> InMemoryChangeStore:
    return InMemoryChangeStore()


@pytest.fixture
def agent(config, llm, changes):
    def build(repo):
        return DocsmindAgent(
            config,
            llm=llm,
            fast_llm=llm,
            scan_llm=llm,
            repository=repo,
            conversations=InMemoryConversationStore(),
            memory=InMemoryMemoryStore(),
            changes=changes,
        )

    return build
