"""change analyses and their rows

The Change Analyzer's output: one `change_analyses` row per comparison of a
current version with a previous one (status, mode, diff ratio, the written
report), and one `change_rows` row per change found. The rows are the single
source of truth for display, editing, verification and Excel export.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "change_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "current_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "previous_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("mode", sa.Text, nullable=False, server_default="none"),
        sa.Column("diff_ratio", sa.Float, nullable=True),
        sa.Column("public", sa.Boolean, nullable=True),
        sa.Column("verdict_note", sa.Text, nullable=False, server_default=""),
        sa.Column("changelog", JSONB, nullable=False, server_default="{}"),
        sa.Column("report", sa.Text, nullable=True),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status in ('running', 'done', 'failed', 'stopped', 'identical', 'unrelated')",
            name="ck_change_analyses_status",
        ),
        sa.CheckConstraint("mode in ('diff', 'full', 'changelog', 'none')", name="ck_change_analyses_mode"),
    )
    op.create_index("ix_change_analyses_created_at", "change_analyses", ["created_at"])

    op.create_table(
        "change_rows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "analysis_id",
            UUID(as_uuid=True),
            sa.ForeignKey("change_analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("current_clause", sa.Text, nullable=False, server_default=""),
        sa.Column("previous_clause", sa.Text, nullable=False, server_default=""),
        sa.Column("change_description", sa.Text, nullable=False, server_default=""),
        sa.Column("nature", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default=""),
        sa.Column("compliance_requirement", sa.Text, nullable=False, server_default="No change"),
        sa.Column("binding", sa.Text, nullable=False, server_default="unchanged"),
        sa.Column("applicability_scope", sa.Text, nullable=False, server_default=""),
        sa.Column("verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("proof", JSONB, nullable=False, server_default="[]"),
        sa.Column("verification_note", sa.Text, nullable=True),
        sa.Column("approved", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("origin", sa.Text, nullable=False, server_default="scan"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "nature in ('new', 'modified', 'editorial', 'removed', 'relocated')", name="ck_change_rows_nature"
        ),
        sa.CheckConstraint(
            "binding in ('normative', 'recommended', 'optional', 'informative', 'unchanged')",
            name="ck_change_rows_binding",
        ),
        sa.CheckConstraint("origin in ('scan', 'changelog', 'user')", name="ck_change_rows_origin"),
    )
    op.create_index("ix_change_rows_analysis_ordinal", "change_rows", ["analysis_id", "ordinal"])


def downgrade() -> None:
    op.drop_table("change_rows")
    op.drop_table("change_analyses")
