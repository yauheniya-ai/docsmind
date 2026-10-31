"""In-memory `ConversationStore` / `MemoryStore`. Used by tests and by
one-off scripts that don't want to touch Postgres; same contract as the
Postgres versions."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from docsmind.domain.agent import Conversation, MemoryItem, StoredMessage


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._conversations: dict[UUID, Conversation] = {}
        self._messages: dict[UUID, list[StoredMessage]] = {}

    async def create(self, title: str) -> Conversation:
        conversation = Conversation(title=title[:120])
        self._conversations[conversation.id] = conversation
        self._messages[conversation.id] = []
        return conversation

    async def get(self, conversation_id: UUID) -> Conversation | None:
        return self._conversations.get(conversation_id)

    async def list(self, limit: int = 50) -> list[dict]:
        rows = []
        for c in sorted(self._conversations.values(), key=lambda c: c.updated_at, reverse=True):
            msgs = self._messages[c.id]
            last = next((m for m in reversed(msgs) if m.role == "assistant"), None)
            rows.append(
                {
                    "id": c.id,
                    "title": c.title,
                    "created_at": c.created_at,
                    "updated_at": c.updated_at,
                    "message_count": len(msgs),
                    "last_answer": last.content if last else None,
                    "last_citations": last.citations if last else [],
                }
            )
        return rows[:limit]

    async def messages(self, conversation_id: UUID) -> list[StoredMessage]:
        return list(self._messages.get(conversation_id, []))

    async def append(self, conversation_id: UUID, messages: list[StoredMessage]) -> None:
        stored = self._messages[conversation_id]
        for m in messages:
            stored.append(m.model_copy(update={"ordinal": len(stored)}))
        self._conversations[conversation_id].updated_at = datetime.now(timezone.utc)

    async def set_summary(self, conversation_id: UUID, summary: str, upto: int) -> None:
        c = self._conversations[conversation_id]
        c.summary = summary
        c.summarized_upto = upto

    async def delete(self, conversation_id: UUID) -> bool:
        self._messages.pop(conversation_id, None)
        return self._conversations.pop(conversation_id, None) is not None


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}

    async def add(self, items: list[MemoryItem]) -> int:
        added = 0
        for item in items:
            if item.key not in self._items:
                self._items[item.key] = item
                added += 1
        return added

    async def recall(self, *, version_ids: list[UUID] | None, limit: int) -> list[MemoryItem]:
        items = [
            i
            for i in self._items.values()
            if version_ids is None or i.kind == "fact" or i.version_id in version_ids
        ]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items[:limit]
