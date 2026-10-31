"""Memory: what the harness remembers between conversations.

Two kinds of notes: `fact` (durable things the user said about themselves)
and `doc_note` (verified navigation facts about a document). The model never
writes memory directly — after an answer, the harness asks a fast model to
extract a few candidates, validates them, and stores them. Reads happen in the
context builder, rendered as clearly-labelled, possibly-stale notes.
"""

from __future__ import annotations

import logging
from uuid import UUID

from docsmind.agent import prompts
from docsmind.agent.catalog import Catalog, CatalogDoc
from docsmind.agent.util import parse_json_object
from docsmind.domain.agent import MemoryItem, Message, SystemBlock
from docsmind.domain.citations import Citation
from docsmind.domain.errors import ToolInputError
from docsmind.domain.ports import LLM, MemoryStore

log = logging.getLogger(__name__)

MAX_NOTE_CHARS = 240
MAX_ITEMS_PER_KIND = 3
RECALL_LIMIT = 20


class MemoryWriter:
    def __init__(self, llm: LLM, store: MemoryStore) -> None:
        self.llm = llm
        self.store = store

    async def remember(
        self,
        *,
        conversation_id: UUID | None,
        question: str,
        answer: str,
        sources: list[Citation],
        catalog: Catalog,
    ) -> int:
        """Best-effort and silent: a failure here must never affect the user."""
        try:
            items = await self._extract(conversation_id, question, answer, sources, catalog)
            return await self.store.add(items)
        except Exception:
            log.exception("Memory update failed")
            return 0

    async def _extract(
        self,
        conversation_id: UUID | None,
        question: str,
        answer: str,
        sources: list[Citation],
        catalog: Catalog,
    ) -> list[MemoryItem]:
        used = "\n".join(
            f"- {doc.handle}: {doc.title}"
            for doc in catalog.docs
            if any(s.version_id == doc.version_id for s in sources)
        )
        prompt = (
            f"Documents used:\n{used or '(none)'}\n\nUSER: {question}\n\nASSISTANT: {answer}"
        )
        response = await self.llm.complete(
            [Message(role="user", content=prompt)],
            system=[SystemBlock(text=prompts.MEMORY_WRITER)],
            max_tokens=400,
        )
        data = parse_json_object(response.text) or {}

        items: list[MemoryItem] = []
        for fact in _strings(data.get("facts"))[:MAX_ITEMS_PER_KIND]:
            items.append(
                MemoryItem(kind="fact", content=fact, source_conversation_id=conversation_id)
            )
        raw_notes = data.get("doc_notes")
        for entry in (raw_notes if isinstance(raw_notes, list) else [])[:MAX_ITEMS_PER_KIND]:
            if not isinstance(entry, dict):
                continue
            note = _clean(entry.get("note"))
            handle = entry.get("document")
            if not note or not isinstance(handle, str):
                continue
            doc = _resolve(catalog, handle)
            if doc is None:  # unknown handle: drop the note
                continue
            items.append(
                MemoryItem(
                    kind="doc_note",
                    content=note,
                    version_id=doc.version_id,
                    source_conversation_id=conversation_id,
                )
            )
        return items


def _resolve(catalog: Catalog, handle: str) -> CatalogDoc | None:
    try:
        return catalog.resolve(handle)
    except ToolInputError:
        return None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:MAX_NOTE_CHARS] if len(text) >= 4 else None


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [c for c in (_clean(v) for v in value) if c]
