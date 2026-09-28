"""full text and stats on document_versions

Adds the whole-document extracted text plus basic stats (char/word/token
counts) directly on `document_versions` — one row per ingested version
already exists there, and this is exactly 1:1 with it, so a separate table
would just be an extra join for no benefit.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("full_text", sa.Text, nullable=True))
    op.add_column("document_versions", sa.Column("char_count", sa.Integer, nullable=True))
    op.add_column("document_versions", sa.Column("word_count", sa.Integer, nullable=True))
    op.add_column("document_versions", sa.Column("token_count_estimate", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("document_versions", "token_count_estimate")
    op.drop_column("document_versions", "word_count")
    op.drop_column("document_versions", "char_count")
    op.drop_column("document_versions", "full_text")