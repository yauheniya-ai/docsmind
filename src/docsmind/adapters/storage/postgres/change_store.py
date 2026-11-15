"""Postgres implementation of `ChangeStore`: change analyses and their rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, select, text, update

from docsmind.adapters.storage.postgres.engine import get_sessionmaker
from docsmind.adapters.storage.postgres.tables import change_analyses as analyses_table
from docsmind.adapters.storage.postgres.tables import change_rows as rows_table
from docsmind.adapters.storage.postgres.tables import change_runs as runs_table
from docsmind.config import DocsmindConfig
from docsmind.domain.changes import NON_REPORT_FIELDS, ChangeAnalysis, ChangeRow, ChangeRun, Proof

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
    "status",
    "archive_reason",
    "archived_at",
    "archived_by_run",
    "confirmations",
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
    "current_version_id",
    "previous_version_id",
    "current_doc",
    "previous_doc",
    "report_stale",
    "report_revision",
    "table_revision",
}
_RUN_FIELDS = {"request", "kind", "scope", "usage", "steps", "outcome"}


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
        "status": row.status,
        "archive_reason": row.archive_reason,
        "archived_at": row.archived_at,
        "archived_by_run": row.archived_by_run,
        "confirmations": row.confirmations,
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
        values = {k: _plain(v) for k, v in fields.items()}
        if values.get("report") is not None:
            # A new report covers the table as it is now.
            values["report_revision"] = select(analyses_table.c.table_revision).where(
                analyses_table.c.id == analysis_id
            ).scalar_subquery()
            values["report_stale"] = False
        async with self._sessionmaker() as session:
            await session.execute(
                update(analyses_table).where(analyses_table.c.id == analysis_id).values(**values, updated_at=_now())
            )
            await session.commit()

    async def _touch(self, session, analysis_id: UUID) -> None:
        """The active rows of an analysis changed: bump the revision, and a report written earlier is now stale."""
        await session.execute(
            update(analyses_table)
            .where(analyses_table.c.id == analysis_id)
            .values(
                table_revision=analyses_table.c.table_revision + 1,
                report_stale=analyses_table.c.report.isnot(None),
                updated_at=_now(),
            )
        )

    async def get_analysis(self, analysis_id: UUID) -> ChangeAnalysis | None:
        async with self._sessionmaker() as session:
            result = await session.execute(select(analyses_table).where(analyses_table.c.id == analysis_id))
            row = result.mappings().first()
        return ChangeAnalysis(**dict(row)) if row else None

    _LIST_SQL = text(
        """
        SELECT a.id, a.status, a.mode, a.diff_ratio, a.created_at, a.updated_at,
               a.current_version_id, a.previous_version_id,
               COALESCE(dc.title, a.current_doc->>'title') AS current_title,
               COALESCE(vc.label, a.current_doc->>'label') AS current_label,
               COALESCE(dp.title, a.previous_doc->>'title') AS previous_title,
               COALESCE(vp.label, a.previous_doc->>'label') AS previous_label,
               a.report_stale,
               (SELECT count(*) FROM change_rows r WHERE r.analysis_id = a.id AND r.status = 'active') AS row_count,
               (SELECT count(*) FROM change_rows r WHERE r.analysis_id = a.id AND r.status = 'active' AND r.verified) AS verified_count
        FROM change_analyses a
        LEFT JOIN document_versions vc ON vc.id = a.current_version_id
        LEFT JOIN documents dc ON dc.id = vc.document_id
        LEFT JOIN document_versions vp ON vp.id = a.previous_version_id
        LEFT JOIN documents dp ON dp.id = vp.document_id
        WHERE a.status <> 'superseded'
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

    async def find_analysis(self, current_hash: str, previous_hash: str) -> ChangeAnalysis | None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(analyses_table)
                .where(
                    analyses_table.c.current_hash == current_hash,
                    analyses_table.c.previous_hash == previous_hash,
                    analyses_table.c.status != "superseded",
                )
                .order_by(analyses_table.c.created_at.desc())
                .limit(1)
            )
            row = result.mappings().first()
        return ChangeAnalysis(**dict(row)) if row else None

    async def relink_versions(self, content_hash: str, version_id: UUID) -> int:
        if not content_hash:
            return 0
        linked = 0
        async with self._sessionmaker() as session:
            for hash_column, link_column in (("current_hash", "current_version_id"), ("previous_hash", "previous_version_id")):
                result = await session.execute(
                    update(analyses_table)
                    .where(analyses_table.c[hash_column] == content_hash, analyses_table.c[link_column].is_(None))
                    .values({link_column: version_id})
                )
                linked += result.rowcount
            await session.commit()
        return linked

    # -- rows ----------------------------------------------------------------------

    async def add_row(self, row: ChangeRow) -> ChangeRow:
        async with self._sessionmaker() as session:
            # Ordinals count every row, archived ones too, so a handle (R12) is never reused.
            next_ordinal = (
                await session.execute(
                    select(func.coalesce(func.max(rows_table.c.ordinal), -1)).where(
                        rows_table.c.analysis_id == row.analysis_id
                    )
                )
            ).scalar_one() + 1
            stored = row.model_copy(update={"ordinal": int(next_ordinal)})
            await session.execute(insert(rows_table).values(**_row_values(stored)))
            if stored.status == "active":
                await self._touch(session, stored.analysis_id)
            await session.commit()
        return stored

    async def update_row(self, row_id: UUID, **fields: Any) -> ChangeRow | None:
        unknown = set(fields) - _ROW_FIELDS
        if unknown:
            raise ValueError(f"Cannot update row fields: {sorted(unknown)}")
        async with self._sessionmaker() as session:
            result = await session.execute(
                update(rows_table)
                .where(rows_table.c.id == row_id)
                .values(**{k: _plain(v) for k, v in fields.items()}, updated_at=_now())
                .returning(rows_table.c.analysis_id, rows_table.c.status)
            )
            changed = result.first()
            if (
                changed is not None
                and (changed.status == "active" or "status" in fields)
                and not set(fields) <= NON_REPORT_FIELDS
            ):
                await self._touch(session, changed.analysis_id)
            await session.commit()
        return await self.get_row(row_id)

    async def get_row(self, row_id: UUID) -> ChangeRow | None:
        async with self._sessionmaker() as session:
            result = await session.execute(select(rows_table).where(rows_table.c.id == row_id))
            row = result.mappings().first()
        return _row_from_db(row) if row else None

    async def delete_row(self, row_id: UUID) -> bool:
        """Removes a row for good. The app archives instead (`archive_row`); this is for clean-up."""
        async with self._sessionmaker() as session:
            result = await session.execute(
                delete(rows_table).where(rows_table.c.id == row_id).returning(rows_table.c.analysis_id, rows_table.c.status)
            )
            gone = result.first()
            if gone is not None and gone.status == "active":
                await self._touch(session, gone.analysis_id)
            await session.commit()
            return gone is not None

    async def archive_row(self, row_id: UUID, reason: str, run_id: UUID | None = None) -> ChangeRow | None:
        return await self.update_row(
            row_id, status="archived", archive_reason=reason, archived_at=_now(), archived_by_run=run_id
        )

    async def restore_row(self, row_id: UUID) -> ChangeRow | None:
        return await self.update_row(
            row_id, status="active", archive_reason=None, archived_at=None, archived_by_run=None
        )

    async def list_rows(self, analysis_id: UUID, status: str | None = "active") -> list[ChangeRow]:
        stmt = select(rows_table).where(rows_table.c.analysis_id == analysis_id)
        if status is not None:
            stmt = stmt.where(rows_table.c.status == status)
        async with self._sessionmaker() as session:
            result = await session.execute(stmt.order_by(rows_table.c.ordinal))
            return [_row_from_db(r) for r in result.mappings().all()]

    # -- runs ------------------------------------------------------------------------

    async def add_run(self, run: ChangeRun) -> None:
        async with self._sessionmaker() as session:
            await session.execute(insert(runs_table).values(**run.model_dump()))
            await session.commit()

    async def update_run(self, run_id: UUID, **fields: Any) -> None:
        unknown = set(fields) - _RUN_FIELDS
        if unknown:
            raise ValueError(f"Cannot update run fields: {sorted(unknown)}")
        async with self._sessionmaker() as session:
            await session.execute(update(runs_table).where(runs_table.c.id == run_id).values(**fields))
            await session.commit()

    async def list_runs(self, analysis_id: UUID) -> list[ChangeRun]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(runs_table).where(runs_table.c.analysis_id == analysis_id).order_by(runs_table.c.started_at.desc())
            )
            return [ChangeRun(**dict(r)) for r in result.mappings().all()]
