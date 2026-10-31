"""extracted PDF tables

Adds a `tables` table mirroring `images` for table regions detected during
PDF parsing (`PDFParser._extract_tables`) — these were already produced by
the parser and returned on `ParsedDocument.tables`, but had nowhere to land
in Postgres. Stored as rendered images in blob storage, same as `images`;
no structured cell data, no caption (tables aren't vision-captioned).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tables",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("bbox_x0", sa.Float, nullable=False),
        sa.Column("bbox_y0", sa.Float, nullable=False),
        sa.Column("bbox_x1", sa.Float, nullable=False),
        sa.Column("bbox_y1", sa.Float, nullable=False),
        sa.Column("uri", sa.Text, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("format", sa.Text, nullable=False),
        sa.Column("phash", sa.Text, nullable=True),
    )
    op.create_index("ix_tables_version_id", "tables", ["version_id"])


def downgrade() -> None:
    op.drop_index("ix_tables_version_id", table_name="tables")
    op.drop_table("tables")
