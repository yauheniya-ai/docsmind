"""Ingest a PDF into a real Postgres and read it back: storage, full-text search, delete."""

from __future__ import annotations

import pytest

from docsmind.adapters.storage.postgres.repository import PostgresRepository
from docsmind.domain.errors import DocumentAlreadyExistsError
from docsmind.ingestion.pipeline import IngestionPipeline
from docsmind.retrieval.keyword import build_tsquery


async def test_ingest_stores_text_chunks_and_original(db_config, make_pdf):
    pdf = make_pdf("Backups must be encrypted at rest.", "Logs are kept for ninety days.")

    version = await IngestionPipeline(db_config).run(pdf, collection="policies", version_label="2025")

    repo = PostgresRepository(db_config)
    stored = await repo.get_version(version.id)
    assert stored["collection"] == "policies" and stored["label"] == "2025"
    assert "encrypted at rest" in await repo.get_full_text(version.id)
    assert await repo.count_chunks(version.id) >= 1
    assert await repo.get_normalized_text(version.id)
    assert any(v["id"] == version.id for v in await repo.list_versions())


async def test_keyword_and_exact_search_find_the_passage(db_config, make_pdf):
    pdf = make_pdf("Backups must be encrypted at rest.", "Clause 7.4.2 covers key rotation.")
    version = await IngestionPipeline(db_config).run(pdf, collection="policies")
    repo = PostgresRepository(db_config)

    ranked = await repo.keyword_search(build_tsquery("encrypting backups"), version_ids=[version.id])
    exact = await repo.grep_chunks("7.4.2", version_ids=[version.id])

    assert ranked and "encrypted" in ranked[0]["content"]  # stemming: encrypting -> encrypted
    assert exact and exact[0]["page_start"] == 1


async def test_same_file_twice_is_rejected(db_config, make_pdf):
    pdf = make_pdf("Only once.")
    pipeline = IngestionPipeline(db_config)
    await pipeline.run(pdf, collection="policies")

    with pytest.raises(DocumentAlreadyExistsError):
        await pipeline.run(pdf, collection="policies")


async def test_delete_removes_the_version_and_its_chunks(db_config, make_pdf):
    version = await IngestionPipeline(db_config).run(make_pdf("Temporary."), collection="policies")
    repo = PostgresRepository(db_config)

    assert await repo.delete_version(version.id) is True

    assert await repo.get_version(version.id) is None
    assert await repo.count_chunks(version.id) == 0
