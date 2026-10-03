"""Pure data models for documents, versions, sections and chunks.

No I/O here. These are shared by ingestion, retrieval, the agent, and the
storage adapters — treat changes to field names as breaking changes.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ChunkType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    TABLE = "table"


class Document(BaseModel):
    """A logical document that may have multiple versions over time."""

    id: UUID = Field(default_factory=uuid4)
    collection: str
    title: str
    tenant_id: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class DocumentVersion(BaseModel):
    """One ingested version of a document (e.g. the 2025 revision of a policy)."""

    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    label: str  # e.g. "2025", "v2", "rev-C"
    effective_date: date | None = None
    content_hash: str
    supersedes_id: UUID | None = None
    blob_uri: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Section(BaseModel):
    """One entry in a document's outline — populated only from the PDF's own
    table of contents/bookmarks (`doc.get_toc()`). If a document has no TOC,
    `ParsedDocument.sections` is simply empty; there is deliberately no
    fallback attempt to reconstruct structure from font size/boldness. That
    kind of heuristic detection doesn't generalize across the huge variety of
    real-world PDFs and creates an ongoing maintenance burden chasing edge
    cases, for a payoff (a synthetic section tree) that chunking no longer
    even depends on — see `Chunk` below.
    """

    id: UUID = Field(default_factory=uuid4)
    version_id: UUID
    parent_id: UUID | None = None
    path: str  # e.g. "4.2" when the TOC title has explicit numbering, else synthetic "s1.s2"
    heading: str | None = None
    number: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    ordinal: int = 0


class Image(BaseModel):
    """An image extracted from a document version."""

    id: UUID = Field(default_factory=uuid4)
    version_id: UUID
    page_number: int
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    uri: str
    width: int
    height: int
    format: str
    phash: str | None = None
    caption: str | None = None
    caption_model: str | None = None
    captioned_at: datetime | None = None


class Table(BaseModel):
    """A table extracted from a document version.

    Tables are stored as rendered images in blob storage, analogous to
    extracted images. No structured table contents are stored here.
    """

    id: UUID = Field(default_factory=uuid4)
    version_id: UUID
    page_number: int
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    uri: str
    width: int
    height: int
    format: str
    phash: str | None = None


class Chunk(BaseModel):
    """A retrievable unit for vector search: prose for text chunks, caption
    for image chunks. Chunked purely by page (see `ingestion/chunking/page.py`)
    — `section_id` exists for a future use but is not populated by the
    default pipeline. Chunks are for RAG-style "find the relevant passage",
    not for reconstructing document structure; the outline (`Section`, above)
    is a separate, optional concern.
    """

    id: UUID = Field(default_factory=uuid4)
    version_id: UUID
    section_id: UUID | None = None
    chunk_type: ChunkType = ChunkType.TEXT
    content: str
    image_id: UUID | None = None
    embedding_model: str | None = None
    page_start: int | None = None
    page_end: int | None = None


class DocumentStats(BaseModel):
    """Computed once per parse, over the whole document's extracted text."""

    char_count: int
    word_count: int
    token_count_estimate: int  # ~0.75 words/token rule of thumb; see chunking/page.py


class ParsedDocument(BaseModel):
    """Everything a `Parser` produces from one raw file, before storage.

    `full_text` is the entire document's extracted text, saved as-is — this
    is the ground truth a person or an LLM can read directly, independent of
    how it later gets chunked for vector search. Sections are returned
    alongside chunks/images/tables (rather than being implicit in
    `Chunk.section_id`) because the pipeline needs to insert them first.
    """

    full_text: str = ""
    stats: DocumentStats = Field(
        default_factory=lambda: DocumentStats(
            char_count=0,
            word_count=0,
            token_count_estimate=0,
        )
    )
    sections: list[Section] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)

