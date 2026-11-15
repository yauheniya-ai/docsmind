"""normalized text on document_versions

The sentence-per-line text (headers, footers and page numbers removed) that
the Change Analyzer diffs. Saved next to `full_text` so it can be inspected
when a comparison looks wrong.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("normalized_text", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("document_versions", "normalized_text")
