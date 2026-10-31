"""Postgres implementations of `ConversationStore` and `MemoryStore`."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from docsmind.adapters.storage.postgres.engine import get_sessionmaker
from docsmind.adapters.storage.postgres.tables import agent_memory as memory_table
from docsmind.adapters.storage.postgres.tables import conversations as conversations_table
from docsmind.adapters.storage.postgres.tables import messages as messages_table
from docsmind.config import DocsmindConfig
from docsmind.domain.agent import Conversation, MemoryItem, StoredMessage


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresConversationStore:
    def __init__(self, config: DocsmindConfig) -> None:
        self._sessionmaker = get_sessionmaker(config)

    async def create(self, title: str) -> Conversation:
        conversation = Conversation(title=title[:120])
        async with self._sessionmaker() as session:
            await session.execute(
                insert(conversations_table).values(
                    id=conversation.id,
                    title=conversation.title,
                    summary=None,
                    summarized_upto=0,
                    created_at=conversation.created_at,
                    updated_at=conversation.updated_at,
                )
            )
            await session.commit()
        return conversation

    async def get(self, conversation_id: UUID) -> Conversation | None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(conversations_table).where(conversations_table.c.id == conversation_id)
            )
            row = result.mappings().first()
        return Conversation(**dict(row)) if row else None

    async def list(self, limit: int = 50) -> list[dict]:
        """Newest first, with what the Activity page needs to render a row:
        message count and the latest assistant answer (+ its citations)."""
        stmt = text(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count,
                   (SELECT m.content FROM messages m
                     WHERE m.conversation_id = c.id AND m.role = 'assistant'
                     ORDER BY m.ordinal DESC LIMIT 1) AS last_answer,
                   (SELECT m.citations FROM messages m
                     WHERE m.conversation_id = c.id AND m.role = 'assistant'
                     ORDER BY m.ordinal DESC LIMIT 1) AS last_citations
            FROM conversations c
            ORDER BY c.updated_at DESC
            LIMIT :limit
            """
        )
        async with self._sessionmaker() as session:
            result = await session.execute(stmt, {"limit": limit})
            return [dict(row) for row in result.mappings().all()]

    async def messages(self, conversation_id: UUID) -> list[StoredMessage]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(messages_table)
                .where(messages_table.c.conversation_id == conversation_id)
                .order_by(messages_table.c.ordinal)
            )
            return [
                StoredMessage(
                    role=row["role"],
                    content=row["content"],
                    citations=row["citations"] or [],
                    meta=row["meta"] or {},
                    ordinal=row["ordinal"],
                    created_at=row["created_at"],
                )
                for row in result.mappings().all()
            ]

    async def append(self, conversation_id: UUID, messages: list[StoredMessage]) -> None:
        if not messages:
            return
        async with self._sessionmaker() as session:
            # Serialize concurrent turns on the same conversation: the
            # (conversation_id, ordinal) unique constraint is the backstop.
            result = await session.execute(
                select(func.coalesce(func.max(messages_table.c.ordinal), -1)).where(
                    messages_table.c.conversation_id == conversation_id
                )
            )
            next_ordinal = int(result.scalar_one()) + 1
            await session.execute(
                insert(messages_table),
                [
                    {
                        "id": uuid4(),
                        "conversation_id": conversation_id,
                        "ordinal": next_ordinal + i,
                        "role": m.role,
                        "content": m.content,
                        "citations": m.citations,
                        "meta": m.meta,
                        "created_at": m.created_at,
                    }
                    for i, m in enumerate(messages)
                ],
            )
            await session.execute(
                update(conversations_table)
                .where(conversations_table.c.id == conversation_id)
                .values(updated_at=_now())
            )
            await session.commit()

    async def set_summary(self, conversation_id: UUID, summary: str, upto: int) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                update(conversations_table)
                .where(conversations_table.c.id == conversation_id)
                .values(summary=summary, summarized_upto=upto)
            )
            await session.commit()

    async def delete(self, conversation_id: UUID) -> bool:
        async with self._sessionmaker() as session:
            result = await session.execute(
                delete(conversations_table).where(conversations_table.c.id == conversation_id)
            )
            await session.commit()
            return result.rowcount > 0


class PostgresMemoryStore:
    def __init__(self, config: DocsmindConfig) -> None:
        self._sessionmaker = get_sessionmaker(config)

    async def add(self, items: list[MemoryItem]) -> int:
        if not items:
            return 0
        added = 0
        async with self._sessionmaker() as session:
            for item in items:
                result = await session.execute(
                    pg_insert(memory_table)
                    .values(
                        id=uuid4(),
                        kind=item.kind,
                        version_id=item.version_id,
                        content=item.content,
                        key=item.key,
                        source_conversation_id=item.source_conversation_id,
                        created_at=item.created_at,
                    )
                    .on_conflict_do_nothing(index_elements=["key"])
                )
                added += result.rowcount or 0
            await session.commit()
        return added

    async def recall(self, *, version_ids: list[UUID] | None, limit: int) -> list[MemoryItem]:
        stmt = select(memory_table).order_by(memory_table.c.created_at.desc()).limit(limit)
        if version_ids is not None:
            # Facts are global; notes only for the documents in scope.
            stmt = stmt.where(
                (memory_table.c.kind == "fact") | (memory_table.c.version_id.in_(version_ids))
            )
        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            return [
                MemoryItem(
                    kind=row["kind"],
                    content=row["content"],
                    version_id=row["version_id"],
                    source_conversation_id=row["source_conversation_id"],
                    created_at=row["created_at"],
                )
                for row in result.mappings().all()
            ]
