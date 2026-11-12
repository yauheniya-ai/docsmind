"""In-memory `ConversationStore` / `MemoryStore` / `ChangeStore`. Used by tests and by
one-off scripts that don't want to touch Postgres; same contract as the
Postgres versions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from docsmind.domain.agent import Conversation, MemoryItem, StoredMessage
from docsmind.domain.changes import ChangeAnalysis, ChangeRow


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


class InMemoryChangeStore:
    """`ChangeStore` in memory (tests, scripts). Same contract as `PostgresChangeStore`."""

    def __init__(self) -> None:
        self._analyses: dict[UUID, ChangeAnalysis] = {}
        self._rows: dict[UUID, ChangeRow] = {}

    async def create_analysis(self, analysis: ChangeAnalysis) -> None:
        self._analyses[analysis.id] = analysis.model_copy(deep=True)

    async def update_analysis(self, analysis_id: UUID, **fields: Any) -> None:
        current = self._analyses[analysis_id]
        self._analyses[analysis_id] = current.model_copy(update={**fields, "updated_at": datetime.now(timezone.utc)})

    async def get_analysis(self, analysis_id: UUID) -> ChangeAnalysis | None:
        found = self._analyses.get(analysis_id)
        return found.model_copy(deep=True) if found else None

    async def list_analyses(self, limit: int = 50) -> list[dict]:
        newest = sorted(self._analyses.values(), key=lambda a: a.created_at, reverse=True)
        return [
            {
                "id": a.id,
                "status": a.status,
                "mode": a.mode,
                "diff_ratio": a.diff_ratio,
                "created_at": a.created_at,
                "updated_at": a.updated_at,
                "current_version_id": a.current_version_id,
                "previous_version_id": a.previous_version_id,
                "row_count": sum(r.analysis_id == a.id for r in self._rows.values()),
                "verified_count": sum(r.analysis_id == a.id and r.verified for r in self._rows.values()),
            }
            for a in newest[:limit]
        ]

    async def delete_analysis(self, analysis_id: UUID) -> bool:
        for row_id in [r.id for r in self._rows.values() if r.analysis_id == analysis_id]:
            del self._rows[row_id]
        return self._analyses.pop(analysis_id, None) is not None

    async def add_row(self, row: ChangeRow) -> ChangeRow:
        ordinal = max((r.ordinal for r in self._rows.values() if r.analysis_id == row.analysis_id), default=-1) + 1
        stored = row.model_copy(update={"ordinal": ordinal}, deep=True)
        self._rows[stored.id] = stored
        return stored.model_copy(deep=True)

    async def update_row(self, row_id: UUID, **fields: Any) -> ChangeRow | None:
        current = self._rows.get(row_id)
        if current is None:
            return None
        self._rows[row_id] = current.model_copy(update={**fields, "updated_at": datetime.now(timezone.utc)})
        return self._rows[row_id].model_copy(deep=True)

    async def get_row(self, row_id: UUID) -> ChangeRow | None:
        found = self._rows.get(row_id)
        return found.model_copy(deep=True) if found else None

    async def delete_row(self, row_id: UUID) -> bool:
        return self._rows.pop(row_id, None) is not None

    async def list_rows(self, analysis_id: UUID) -> list[ChangeRow]:
        rows = [r for r in self._rows.values() if r.analysis_id == analysis_id]
        return [r.model_copy(deep=True) for r in sorted(rows, key=lambda r: r.ordinal)]
