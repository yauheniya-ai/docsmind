"""Interfaces the core depends on. Concrete implementations live under
`adapters/` and are imported lazily by the adapter registry, never here.

Keeping these as `Protocol`s (rather than ABCs) means adapters don't need to
inherit from anything docsmind-specific — a thin wrapper around an existing
client class is enough.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from docsmind.domain.documents import Chunk, Document, DocumentVersion, ParsedDocument


class LLM(Protocol):
    """Chat/tool-calling completion provider (Anthropic, OpenAI, ...)."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        ...


class VisionCaptioner(Protocol):
    """Describes an image well enough to be searchable and cited."""

    async def caption(self, image_bytes: bytes, *, mime_type: str) -> str:
        ...


class Embedder(Protocol):
    """Turns text (including image captions) into vectors."""

    model_name: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class VectorStore(Protocol):
    """Hybrid (vector + keyword) storage and retrieval for chunks."""

    async def upsert_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        ...

    async def hybrid_search(
        self,
        query: str,
        query_embedding: list[float],
        *,
        version_ids: list[UUID] | None = None,
        chunk_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[Chunk]:
        ...


class BlobStore(Protocol):
    """Stores original files and extracted images."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        """Returns a URI that `get` can resolve later."""
        ...

    async def get(self, uri: str) -> bytes:
        ...


class Parser(Protocol):
    """Extracts structure (sections), text chunks, and images from a raw file."""

    async def parse(self, path: str, *, document: Document, version: DocumentVersion) -> ParsedDocument:
        ...