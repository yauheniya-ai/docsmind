"""Shared fixtures for all test levels.

    tests/unit/         pure logic, fakes only, no Docker, no network
    tests/integration/  real Postgres + pgvector (a throwaway Docker container), no network, no LLM
    tests/e2e/          the real `docsmind` CLI and server as subprocesses, driven over HTTP

The Postgres container starts once per session, and only when a test asks for it, so
`pytest tests/unit` never needs Docker. Without Docker, integration and e2e tests are skipped.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent


def pytest_collection_modifyitems(items):
    for item in items:
        parts = Path(str(item.fspath)).parts
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        elif "e2e" in parts:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def postgres_url():
    """A migrated pgvector Postgres, as an asyncpg URL. Skips when Docker is unavailable."""
    try:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg")
        container.start()
    except Exception as exc:  # noqa: BLE001 - no Docker daemon, image pull failed, ...
        pytest.skip(f"Docker/Postgres is not available: {exc}")

    try:
        url = container.get_connection_url()
        env = {**os.environ, "DOCSMIND_DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
        )
        yield url
    finally:
        container.stop()


@pytest.fixture
def make_pdf(tmp_path):
    """`make_pdf(*lines)` -> path of a one-page PDF with a unique line, so every call is a new document
    (the content hash must be unique to be ingested)."""

    def build(*lines: str) -> Path:
        doc = pymupdf.open()
        page = doc.new_page()
        y = 72
        for line in (*lines, f"Document id {uuid.uuid4().hex}"):
            page.insert_text((50, y), line, fontsize=11)
            y += 20
        path = tmp_path / f"{uuid.uuid4().hex[:8]}.pdf"
        doc.save(str(path))
        doc.close()
        return path

    return build
