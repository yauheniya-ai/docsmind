"""Integration fixtures: a config pointing at the throwaway Postgres."""

from __future__ import annotations

import pytest

from docsmind.adapters.storage.postgres import engine as engine_module
from docsmind.config import DocsmindConfig


@pytest.fixture
def db_config(postgres_url, tmp_path) -> DocsmindConfig:
    return DocsmindConfig(_env_file=None, database_url=postgres_url, blob_root=str(tmp_path / "blobs"))


@pytest.fixture(autouse=True)
def _fresh_engines():
    """Every test has its own event loop; a cached async engine from an earlier loop would break."""
    yield
    engine_module._engines.clear()
