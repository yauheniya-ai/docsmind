"""one analysis per ordered pair of documents

Several analyses of the same pair (earlier runs created a new one every time) are reduced to one:
the newest stays, older ones are marked `superseded` (kept, hidden from the list), and a unique
index on the hash pair makes sure a pair has only one live analysis from now on.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-15
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE change_analyses a SET status = 'superseded'
        WHERE a.current_hash <> '' AND a.previous_hash <> '' AND a.status <> 'superseded'
          AND EXISTS (
            SELECT 1 FROM change_analyses b
            WHERE b.current_hash = a.current_hash AND b.previous_hash = a.previous_hash
              AND b.status <> 'superseded' AND b.id <> a.id
              AND (b.created_at > a.created_at OR (b.created_at = a.created_at AND b.id > a.id))
          )
        """
    )
    op.create_index(
        "uq_change_analyses_pair",
        "change_analyses",
        ["current_hash", "previous_hash"],
        unique=True,
        postgresql_where="status <> 'superseded' AND current_hash <> '' AND previous_hash <> ''",
    )


def downgrade() -> None:
    op.drop_index("uq_change_analyses_pair", table_name="change_analyses")
