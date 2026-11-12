"""Postgres implementation of `ChangeStore`: change analyses and their rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, select, text, update

from docsmind.adapters.storage.postgres.engine import get_sessionmaker
from docsmind.adapters.storage.postgres.tables import change_analyses as analyses_table
from docsmind.adapters.storage.postgres.tables import change_rows as rows_table
from docsmind.config import DocsmindConfig
from docsmind.domain.changes import ChangeAnalysis, ChangeRow, Proof

_ROW_FIELDS = {
    "current_clause",
    "previous_clause",
    "change_description",
    "nature",
    "category",
    "compliance_requirement",
    "binding",
    "applicability_scope",
    "verified",
    "proof",
    "verification_note",
    "approved",
}
_ANALYSIS_FIELDS = {
    "status",
    "mode",
    "diff_ratio",
    "public",
    "verdict_note",
    "changelog",
    "report",
    "meta",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_from_db(row: Any) -> ChangeRow:
    data = dict(row)
    data["proof"] = [Proof(**p) for p in (data.get("proof") or [])]
    return ChangeRow(**data)


def _row_values(row: ChangeRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "analysis_id": row.analysis_id,
        "ordinal": row.ordinal,
        "current_clause": row.current_clause,
        "previous_clause": row.previous_clause,
        "change_description": row.change_description,
        "nature": row.nature.value,
        "category": row.category,
        "compliance_requirement": row.compliance_requirement,
        "binding": row.binding.value,
        "applicability_scope": row.applicability_scope,
        "verified": row.verified,
        "proof": [p.model_dump(mode="json") for p in row.proof],
        "verification_note": row.verification_note,
        "approved": row.approved,
        "origin": row.origin,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _plain(value: Any) -> Any:
    """Enums and models to what JSONB/text columns take."""
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, list):
        return [p.model_dump(mode="json") if hasattr(p, "model_dump") else p for p in value]
    return value


class PostgresChangeStore:
    def __init__(self, config: DocsmindConfig) -> None:
        self._sessionmaker = get_sessionmaker(config)

    # -- analyses ---------------------------------------------------------------

    async def create_analysis(self, analysis: ChangeAnalysis) -> None:
        async with self._sessionmaker() as session:
            await session.execute(insert(analyses_table).values(**analysis.model_dump()))
            await session.commit()

    async def update_analysis(self, analysis_id: UUID, **fields: Any) -> None:
        unknown = set(fields) - _ANALYSIS_FIELDS
        if unknown:
            raise ValueError(f"Cannot update analysis fields: {sorted(unknown)}")
        async with self._sessionmaker() as session:
            await session.execute(
                update(analyses_table)
                .where(analyses_table.c.id == analysis_id)
                .values(**{k: _plain(v) for k, v in fields.items()}, updated_at=_now())
            )
            await session.commit()

    async def get_analysis(self, analysis_id: UUID) -> ChangeAnalysis | None:
        async with self._sessionmaker() as session:
            result = await session.execute(select(analyses_table).where(analyses_table.c.id == analysis_id))
            row = result.mappings().first()
        return ChangeAnalysis(**dict(row)) if row else None

    _LIST_SQL = text(
        """
        SELECT a.id, a.status, a.mode, a.diff_ratio, a.created_at, a.updated_at,
               a.current_version_id, a.previous_version_id,
               dc.title AS current_title, vc.label AS current_label,
               dp.title AS previous_title, vp.label AS previous_label,
               (SELECT count(*) FROM change_rows r WHERE r.analysis_id = a.id) AS row_count,
               (SELECT count(*) FROM change_rows r WHERE r.analysis_id = a.id AND r.verified) AS verified_count
        FROM change_analyses a
        JOIN document_versions vc ON vc.id = a.current_version_id
        JOIN documents dc ON dc.id = vc.document_id
        JOIN document_versions vp ON vp.id = a.previous_version_id
        JOIN documents dp ON dp.id = vp.document_id
        ORDER BY a.created_at DESC
        LIMIT :limit
        """
    )

    async def list_analyses(self, limit: int = 50) -> list[dict]:
        async with self._sessionmaker() as session:
            result = await session.execute(self._LIST_SQL, {"limit": limit})
            return [dict(row) for row in result.mappings().all()]

    async def delete_analysis(self, analysis_id: UUID) -> bool:
        async with self._sessionmaker() as session:
            result = await session.execute(delete(analyses_table).where(analyses_table.c.id == analysis_id))
            await session.commit()
            return result.rowcount > 0

    # -- rows ----------------------------------------------------------------------

    async def add_row(self, row: ChangeRow) -> ChangeRow:
        async with self._sessionmaker() as session:
            next_ordinal = (
                await session.execute(
                    select(func.coalesce(func.max(rows_table.c.ordinal), -1)).where(
                        rows_table.c.analysis_id == row.analysis_id
                    )
                )
            ).scalar_one() + 1
            stored = row.model_copy(update={"ordinal": int(next_ordinal)})
            await session.execute(insert(rows_table).values(**_row_values(stored)))
            await session.commit()
        return stored

    async def update_row(self, row_id: UUID, **fields: Any) -> ChangeRow | None:
        unknown = set(fields) - _ROW_FIELDS
        if unknown:
            raise ValueError(f"Cannot update row fields: {sorted(unknown)}")
        async with self._sessionmaker() as session:
            await session.execute(
                update(rows_table)
                .where(rows_table.c.id == row_id)
                .values(**{k: _plain(v) for k, v in fields.items()}, updated_at=_now())
            )
            await session.commit()
        return await self.get_row(row_id)

    async def get_row(self, row_id: UUID) -> ChangeRow | None:
        async with self._sessionmaker() as session:
            result = await session.execute(select(rows_table).where(rows_table.c.id == row_id))
            row = result.mappings().first()
        return _row_from_db(row) if row else None

    async def delete_row(self, row_id: UUID) -> bool:
        async with self._sessionmaker() as session:
            result = await session.execute(delete(rows_table).where(rows_table.c.id == row_id))
            await session.commit()
            return result.rowcount > 0

    async def list_rows(self, analysis_id: UUID) -> list[ChangeRow]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(rows_table).where(rows_table.c.analysis_id == analysis_id).order_by(rows_table.c.ordinal)
            )
            return [_row_from_db(r) for r in result.mappings().all()]
