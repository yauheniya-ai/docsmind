"""Writes/reads the domain objects (`domain/documents.py`) to/from Postgres,
via the Core `Table`s in `tables.py`.

This is intentionally broader than the `VectorStore` port in `domain/ports.py`
— `VectorStore` only covers the hybrid-search-relevant subset (`upsert_chunks`,
`hybrid_search`). Writing a `Document`/`DocumentVersion`/`Section`/`Image`
graph on ingest is a separate concern from searching chunks, so it lives here
as its own class rather than being forced into that Protocol. `PostgresRepository`
implements `VectorStore` (see `upsert_chunks`/`hybrid_search` below) as one
part of what it does, not the whole of it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import insert, select

from docsmind.adapters.storage.postgres.engine import get_sessionmaker
from docsmind.adapters.storage.postgres.tables import (
    chunks as chunks_table,
)
from docsmind.adapters.storage.postgres.tables import (
    document_versions as document_versions_table,
)
from docsmind.adapters.storage.postgres.tables import (
    documents as documents_table,
)
from docsmind.adapters.storage.postgres.tables import (
    images as images_table,
)
from docsmind.adapters.storage.postgres.tables import (
    sections as sections_table,
)
from docsmind.config import DocsmindConfig
from docsmind.domain.documents import (
    Chunk,
    Document,
    DocumentStats,
    DocumentVersion,
    ParsedDocument,
)


class PostgresRepository:
    def __init__(self, config: DocsmindConfig) -> None:
        self.config = config
        self._sessionmaker = get_sessionmaker(config)

    async def create_document(self, document: Document) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                insert(documents_table).values(
                    id=document.id,
                    collection=document.collection,
                    title=document.title,
                    tenant_id=document.tenant_id,
                    created_at=document.created_at,
                )
            )
            await session.commit()

    async def create_version(
        self, version: DocumentVersion, *, full_text: str = "", stats: DocumentStats | None = None
    ) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                insert(document_versions_table).values(
                    id=version.id,
                    document_id=version.document_id,
                    label=version.label,
                    effective_date=version.effective_date,
                    content_hash=version.content_hash,
                    supersedes_id=version.supersedes_id,
                    blob_uri=version.blob_uri,
                    created_at=version.created_at,
                    full_text=full_text or None,
                    char_count=stats.char_count if stats else None,
                    word_count=stats.word_count if stats else None,
                    token_count_estimate=stats.token_count_estimate if stats else None,
                )
            )
            await session.commit()

    async def save_parsed(self, parsed: ParsedDocument) -> None:
        """Bulk-insert everything a `Parser` produced for one version:
        sections first (chunks/images reference them via foreign key), then
        images, then chunks. All in one transaction — a parse that produced
        1000 chunks shouldn't leave the DB with 400 of them if something
        fails partway through.
        """
        async with self._sessionmaker() as session:
            if parsed.sections:
                await session.execute(
                    insert(sections_table),
                    [
                        {
                            "id": s.id,
                            "version_id": s.version_id,
                            "parent_id": s.parent_id,
                            "path": s.path,
                            "heading": s.heading,
                            "number": s.number,
                            "page_start": s.page_start,
                            "page_end": s.page_end,
                            "ordinal": s.ordinal,
                        }
                        for s in parsed.sections
                    ],
                )
            if parsed.images:
                await session.execute(
                    insert(images_table),
                    [
                        {
                            "id": img.id,
                            "version_id": img.version_id,
                            "page_number": img.page_number,
                            "bbox_x0": img.bbox[0],
                            "bbox_y0": img.bbox[1],
                            "bbox_x1": img.bbox[2],
                            "bbox_y1": img.bbox[3],
                            "uri": img.uri,
                            "width": img.width,
                            "height": img.height,
                            "format": img.format,
                            "phash": img.phash,
                            "caption": img.caption,
                            "caption_model": img.caption_model,
                            "captioned_at": img.captioned_at,
                        }
                        for img in parsed.images
                    ],
                )
            if parsed.chunks:
                await session.execute(
                    insert(chunks_table),
                    [self._chunk_row(c) for c in parsed.chunks],
                )
            await session.commit()

    @staticmethod
    def _chunk_row(chunk: Chunk, embedding: list[float] | None = None) -> dict:
        return {
            "id": chunk.id,
            "version_id": chunk.version_id,
            "section_id": chunk.section_id,
            "chunk_type": chunk.chunk_type.value,
            "content": chunk.content,
            "image_id": chunk.image_id,
            "embedding": embedding,
            "embedding_model": chunk.embedding_model,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
        }

    # -- VectorStore port ----------------------------------------------------
    # Everything below satisfies `domain.ports.VectorStore`. Note `upsert_chunks`
    # is for *re-embedding* existing chunk rows (e.g. once captions land, or
    # you switch embedding models) — `save_parsed` above is what inserts the
    # rows in the first place, without embeddings, straight from a parse.

    async def upsert_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        async with self._sessionmaker() as session:
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                await session.execute(
                    chunks_table.update()
                    .where(chunks_table.c.id == chunk.id)
                    .values(embedding=embedding, embedding_model=chunk.embedding_model)
                )
            await session.commit()

    async def hybrid_search(
        self,
        query: str,
        query_embedding: list[float],
        *,
        version_ids: list[UUID] | None = None,
        chunk_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[Chunk]:
        # TODO: reciprocal rank fusion of a vector KNN query and a
        # plainto_tsquery match against `chunks.tsv`, per the design from
        # earlier in this conversation. Not implemented yet — this is the
        # next piece after captioning/embedding actually populate `embedding`.
        raise NotImplementedError(
            "hybrid_search isn't wired up yet — chunks currently have no "
            "embeddings to search over. See adapters/embeddings/ (not yet "
            "built) and enrichment/caption.py (not yet built)."
        )

    # -- read helpers, mainly for sanity-checking what actually landed -------

    async def count_chunks(self, version_id: UUID) -> int:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(chunks_table.c.id).where(chunks_table.c.version_id == version_id)
            )
            return len(result.fetchall())

    async def list_versions(self) -> list[dict]:
        """One row per ingested `DocumentVersion`, joined back to its
        `Document` — this is how you find a `version_id` to pass to
        `get_sections`/`get_chunks`/`get_full_text` without already knowing it."""
        async with self._sessionmaker() as session:
            stmt = (
                select(
                    documents_table.c.title,
                    documents_table.c.collection,
                    document_versions_table.c.id,
                    document_versions_table.c.label,
                    document_versions_table.c.created_at,
                    document_versions_table.c.char_count,
                    document_versions_table.c.word_count,
                )
                .select_from(
                    document_versions_table.join(
                        documents_table, documents_table.c.id == document_versions_table.c.document_id
                    )
                )
                .order_by(document_versions_table.c.created_at)
            )
            result = await session.execute(stmt)
            return [dict(row) for row in result.mappings().all()]

    async def get_full_text(self, version_id: UUID) -> str | None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(document_versions_table.c.full_text).where(
                    document_versions_table.c.id == version_id
                )
            )
            row = result.first()
            return row[0] if row else None

    async def get_sections(self, version_id: UUID) -> list[dict]:
        """The saved section tree for one version, in document order — the
        same shape `examples/parse_pdf_demo.py` prints, but read back from
        Postgres instead of a fresh parse."""
        async with self._sessionmaker() as session:
            stmt = (
                select(sections_table)
                .where(sections_table.c.version_id == version_id)
                .order_by(sections_table.c.ordinal)
            )
            result = await session.execute(stmt)
            return [dict(row) for row in result.mappings().all()]

    async def get_chunks(self, version_id: UUID, *, page: int | None = None) -> list[dict]:
        """Chunks for one version, in page order. Pass `page` to see only
        one page's chunk(s) — most pages are exactly one chunk; a dense page
        may be several."""
        async with self._sessionmaker() as session:
            stmt = (
                select(
                    chunks_table.c.id,
                    chunks_table.c.chunk_type,
                    chunks_table.c.content,
                    chunks_table.c.image_id,
                    chunks_table.c.page_start,
                    chunks_table.c.page_end,
                )
                .where(chunks_table.c.version_id == version_id)
                .order_by(chunks_table.c.page_start)
            )
            if page is not None:
                stmt = stmt.where(chunks_table.c.page_start == page)
            result = await session.execute(stmt)
            return [dict(row) for row in result.mappings().all()]