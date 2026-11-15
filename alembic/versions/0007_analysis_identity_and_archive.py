"""change analyses stand on their own; rows can be archived; run history

- `change_analyses` is identified by the content hashes of its two documents, not by version ids:
  the version links become nullable (`ON DELETE SET NULL`), so deleting a document no longer deletes
  its analyses. A snapshot of each document's title/label/pages is kept for display. Re-ingesting
  the same file re-links the analysis by hash.
- `change_rows` get a status (active, candidate, archived), an archive reason and date, and a count of
  how often a rescan confirmed the row. Rejected and deleted rows are archived, not removed.
- `change_runs` keeps the history of what was asked of an analysis, and what it cost.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- analyses: identity by hash ------------------------------------------------------------
    op.add_column("change_analyses", sa.Column("current_hash", sa.Text, nullable=False, server_default=""))
    op.add_column("change_analyses", sa.Column("previous_hash", sa.Text, nullable=False, server_default=""))
    op.add_column("change_analyses", sa.Column("current_doc", JSONB, nullable=False, server_default="{}"))
    op.add_column("change_analyses", sa.Column("previous_doc", JSONB, nullable=False, server_default="{}"))
    op.add_column("change_analyses", sa.Column("report_stale", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("change_analyses", sa.Column("report_revision", sa.Integer, nullable=False, server_default="0"))
    op.add_column("change_analyses", sa.Column("table_revision", sa.Integer, nullable=False, server_default="0"))
    op.execute(
        """
        UPDATE change_analyses a SET
          current_hash = vc.content_hash,
          previous_hash = vp.content_hash,
          current_doc = jsonb_build_object('title', dc.title, 'collection', dc.collection, 'label', vc.label),
          previous_doc = jsonb_build_object('title', dp.title, 'collection', dp.collection, 'label', vp.label)
        FROM document_versions vc, documents dc, document_versions vp, documents dp
        WHERE vc.id = a.current_version_id AND dc.id = vc.document_id
          AND vp.id = a.previous_version_id AND dp.id = vp.document_id
        """
    )
    for column in ("current_version_id", "previous_version_id"):
        op.drop_constraint(f"change_analyses_{column}_fkey", "change_analyses", type_="foreignkey")
        op.alter_column("change_analyses", column, nullable=True)
        op.create_foreign_key(
            f"change_analyses_{column}_fkey", "change_analyses", "document_versions", [column], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_change_analyses_hashes", "change_analyses", ["current_hash", "previous_hash"])

    # -- rows: status and archive ----------------------------------------------------------------
    op.add_column("change_rows", sa.Column("status", sa.Text, nullable=False, server_default="active"))
    op.add_column("change_rows", sa.Column("archive_reason", sa.Text, nullable=True))
    op.add_column("change_rows", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("change_rows", sa.Column("archived_by_run", UUID(as_uuid=True), nullable=True))
    op.add_column("change_rows", sa.Column("confirmations", sa.Integer, nullable=False, server_default="0"))
    op.create_index("ix_change_rows_analysis_status", "change_rows", ["analysis_id", "status"])
    op.create_check_constraint("ck_change_rows_status", "change_rows", "status IN ('active', 'candidate', 'archived')")
    op.drop_constraint("ck_change_rows_origin", "change_rows", type_="check")
    op.create_check_constraint(
        "ck_change_rows_origin", "change_rows", "origin IN ('scan', 'changelog', 'user', 'agent')"
    )
    op.drop_constraint("ck_change_analyses_status", "change_analyses", type_="check")
    op.create_check_constraint(
        "ck_change_analyses_status",
        "change_analyses",
        "status IN ('running', 'done', 'failed', 'stopped', 'identical', 'unrelated', 'superseded')",
    )

    # -- run history -------------------------------------------------------------------------------
    op.create_table(
        "change_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("analysis_id", UUID(as_uuid=True), sa.ForeignKey("change_analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request", sa.Text, nullable=False, server_default=""),
        sa.Column("kind", sa.Text, nullable=False, server_default="compare"),
        sa.Column("scope", sa.Text, nullable=False, server_default=""),
        sa.Column("usage", JSONB, nullable=False, server_default="{}"),
        sa.Column("steps", sa.Integer, nullable=False, server_default="0"),
        sa.Column("outcome", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_change_runs_analysis", "change_runs", ["analysis_id", "started_at"])


def downgrade() -> None:
    op.drop_table("change_runs")
    op.execute("UPDATE change_analyses SET status = 'stopped' WHERE status = 'superseded'")
    op.drop_constraint("ck_change_analyses_status", "change_analyses", type_="check")
    op.create_check_constraint(
        "ck_change_analyses_status",
        "change_analyses",
        "status IN ('running', 'done', 'failed', 'stopped', 'identical', 'unrelated')",
    )
    op.execute("DELETE FROM change_rows WHERE origin = 'agent' OR status <> 'active'")
    op.drop_constraint("ck_change_rows_origin", "change_rows", type_="check")
    op.create_check_constraint("ck_change_rows_origin", "change_rows", "origin IN ('scan', 'changelog', 'user')")
    op.drop_constraint("ck_change_rows_status", "change_rows", type_="check")
    op.drop_index("ix_change_rows_analysis_status", table_name="change_rows")
    for column in ("confirmations", "archived_by_run", "archived_at", "archive_reason", "status"):
        op.drop_column("change_rows", column)
    op.drop_index("ix_change_analyses_hashes", table_name="change_analyses")
    for column in ("current_version_id", "previous_version_id"):
        op.drop_constraint(f"change_analyses_{column}_fkey", "change_analyses", type_="foreignkey")
        op.execute(f"DELETE FROM change_analyses WHERE {column} IS NULL")
        op.alter_column("change_analyses", column, nullable=False)
        op.create_foreign_key(
            f"change_analyses_{column}_fkey", "change_analyses", "document_versions", [column], ["id"], ondelete="CASCADE"
        )
    for column in ("table_revision", "report_revision", "report_stale", "previous_doc", "current_doc", "previous_hash", "current_hash"):
        op.drop_column("change_analyses", column)
