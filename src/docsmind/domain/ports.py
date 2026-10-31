"""Interfaces the core depends on. Concrete implementations live under
`adapters/` and are imported lazily by the adapter registry, never here.

Keeping these as `Protocol`s (rather than ABCs) means adapters don't need to
inherit from anything docsmind-specific — a thin wrapper around an existing
client class is enough.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from docsmind.domain.agent import (
    Conversation,
    LLMResponse,
    MemoryItem,
    Message,
    StoredMessage,
    SystemBlock,
    ToolChoice,
    ToolSpec,
)
from docsmind.domain.documents import Chunk, Document, DocumentVersion, ParsedDocument


class LLM(Protocol):
    """Chat/tool-calling completion provider (Anthropic, OpenAI, ...).

    Implementations stream internally: `on_text` is awaited with each text
    delta as it arrives, and the complete response is returned at the end.
    """

    model: str

    async def complete(
        self,
        messages: list[Message],
        *,
        system: list[SystemBlock],
        tools: list[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        max_tokens: int = 1024,
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
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


class ConversationStore(Protocol):
    """Server-side chat history, so the Mind has multi-turn context."""

    async def create(self, title: str) -> Conversation:
        ...

    async def get(self, conversation_id: UUID) -> Conversation | None:
        ...

    async def list(self, limit: int = 50) -> list[dict]:
        ...

    async def messages(self, conversation_id: UUID) -> list[StoredMessage]:
        ...

    async def append(self, conversation_id: UUID, messages: list[StoredMessage]) -> None:
        ...

    async def set_summary(self, conversation_id: UUID, summary: str, upto: int) -> None:
        ...

    async def delete(self, conversation_id: UUID) -> bool:
        ...


class MemoryStore(Protocol):
    """Long-lived notes the Mind keeps between conversations: user facts and
    per-document notes. Written by the harness, never directly by the model."""

    async def add(self, items: list[MemoryItem]) -> int:
        """Insert new items, skipping duplicates. Returns how many were new."""
        ...

    async def recall(self, *, version_ids: list[UUID] | None, limit: int) -> list[MemoryItem]:
        """Most recent facts plus notes for the given versions (None = any)."""
        ...