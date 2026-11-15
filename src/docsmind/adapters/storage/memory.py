"""In-memory `ConversationStore` / `MemoryStore` / `ChangeStore`. Used by tests and by
one-off scripts that don't want to touch Postgres; same contract as the
Postgres versions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from docsmind.domain.agent import Conversation, MemoryItem, StoredMessage
from docsmind.domain.changes import NON_REPORT_FIELDS, ChangeAnalysis, ChangeRow, ChangeRun


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
        self._runs: dict[UUID, ChangeRun] = {}

    async def create_analysis(self, analysis: ChangeAnalysis) -> None:
        self._analyses[analysis.id] = analysis.model_copy(deep=True)

    async def update_analysis(self, analysis_id: UUID, **fields: Any) -> None:
        current = self._analyses[analysis_id]
        if fields.get("report") is not None:
            fields = {**fields, "report_revision": current.table_revision, "report_stale": False}
        self._analyses[analysis_id] = current.model_copy(update={**fields, "updated_at": datetime.now(timezone.utc)})

    def _touch(self, analysis_id: UUID | None) -> None:
        current = self._analyses.get(analysis_id) if analysis_id else None
        if current is not None:
            self._analyses[current.id] = current.model_copy(
                update={"table_revision": current.table_revision + 1, "report_stale": current.report is not None}
            )

    async def get_analysis(self, analysis_id: UUID) -> ChangeAnalysis | None:
        found = self._analyses.get(analysis_id)
        return found.model_copy(deep=True) if found else None

    async def find_analysis(self, current_hash: str, previous_hash: str) -> ChangeAnalysis | None:
        found = [
            a
            for a in self._analyses.values()
            if a.current_hash == current_hash and a.previous_hash == previous_hash and a.status != "superseded"
        ]
        return max(found, key=lambda a: a.created_at).model_copy(deep=True) if found else None

    async def relink_versions(self, content_hash: str, version_id: UUID) -> int:
        linked = 0
        if not content_hash:
            return 0
        for a in list(self._analyses.values()):
            update: dict[str, Any] = {}
            if a.current_hash == content_hash and a.current_version_id is None:
                update["current_version_id"] = version_id
            if a.previous_hash == content_hash and a.previous_version_id is None:
                update["previous_version_id"] = version_id
            if update:
                self._analyses[a.id] = a.model_copy(update=update)
                linked += len(update)
        return linked

    async def list_analyses(self, limit: int = 50) -> list[dict]:
        newest = sorted(
            (a for a in self._analyses.values() if a.status != "superseded"), key=lambda a: a.created_at, reverse=True
        )
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
                "current_title": a.current_doc.get("title"),
                "current_label": a.current_doc.get("label"),
                "previous_title": a.previous_doc.get("title"),
                "previous_label": a.previous_doc.get("label"),
                "report_stale": a.report_stale,
                "row_count": sum(r.analysis_id == a.id and r.status == "active" for r in self._rows.values()),
                "verified_count": sum(
                    r.analysis_id == a.id and r.status == "active" and r.verified for r in self._rows.values()
                ),
            }
            for a in newest[:limit]
        ]

    async def delete_analysis(self, analysis_id: UUID) -> bool:
        for row_id in [r.id for r in self._rows.values() if r.analysis_id == analysis_id]:
            del self._rows[row_id]
        for run_id in [r.id for r in self._runs.values() if r.analysis_id == analysis_id]:
            del self._runs[run_id]
        return self._analyses.pop(analysis_id, None) is not None

    async def add_row(self, row: ChangeRow) -> ChangeRow:
        ordinal = max((r.ordinal for r in self._rows.values() if r.analysis_id == row.analysis_id), default=-1) + 1
        stored = row.model_copy(update={"ordinal": ordinal}, deep=True)
        self._rows[stored.id] = stored
        if stored.status == "active":
            self._touch(stored.analysis_id)
        return stored.model_copy(deep=True)

    async def update_row(self, row_id: UUID, **fields: Any) -> ChangeRow | None:
        current = self._rows.get(row_id)
        if current is None:
            return None
        self._rows[row_id] = current.model_copy(update={**fields, "updated_at": datetime.now(timezone.utc)})
        if (current.status == "active" or "status" in fields) and not set(fields) <= NON_REPORT_FIELDS:
            self._touch(current.analysis_id)
        return self._rows[row_id].model_copy(deep=True)

    async def get_row(self, row_id: UUID) -> ChangeRow | None:
        found = self._rows.get(row_id)
        return found.model_copy(deep=True) if found else None

    async def delete_row(self, row_id: UUID) -> bool:
        gone = self._rows.pop(row_id, None)
        if gone is not None and gone.status == "active":
            self._touch(gone.analysis_id)
        return gone is not None

    async def archive_row(self, row_id: UUID, reason: str, run_id: UUID | None = None) -> ChangeRow | None:
        return await self.update_row(
            row_id, status="archived", archive_reason=reason, archived_at=datetime.now(timezone.utc), archived_by_run=run_id
        )

    async def restore_row(self, row_id: UUID) -> ChangeRow | None:
        return await self.update_row(
            row_id, status="active", archive_reason=None, archived_at=None, archived_by_run=None
        )

    async def list_rows(self, analysis_id: UUID, status: str | None = "active") -> list[ChangeRow]:
        rows = [r for r in self._rows.values() if r.analysis_id == analysis_id and (status is None or r.status == status)]
        return [r.model_copy(deep=True) for r in sorted(rows, key=lambda r: r.ordinal)]

    async def add_run(self, run: ChangeRun) -> None:
        self._runs[run.id] = run.model_copy(deep=True)

    async def update_run(self, run_id: UUID, **fields: Any) -> None:
        self._runs[run_id] = self._runs[run_id].model_copy(update=fields)

    async def list_runs(self, analysis_id: UUID) -> list[ChangeRun]:
        runs = [r for r in self._runs.values() if r.analysis_id == analysis_id]
        return [r.model_copy(deep=True) for r in sorted(runs, key=lambda r: r.started_at, reverse=True)]
