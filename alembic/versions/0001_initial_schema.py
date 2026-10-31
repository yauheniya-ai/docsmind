"""initial schema

Creates the vector/pgcrypto extensions and every table from the domain
model: documents, document_versions, sections, images, chunks (text + image,
one hybrid index), agent_tables + revisions.

Revision ID: 0001
Revises:
Create Date: 2026-09-26
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match DocsmindConfig.embedding_dimensions for whichever embedder you
# run this migration with. Changing embedders to a different dimensionality
# later requires a new migration (ALTER COLUMN ... TYPE vector(new_dim)) and
# re-embedding every chunk — there's no way around that with pgvector.
EMBEDDING_DIMENSIONS = int(os.environ.get("DOCSMIND_EMBEDDING_DIMENSIONS", "3072"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # gives us gen_random_uuid()

    # --- documents ---------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("collection", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("tenant_id", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_documents_collection", "documents", ["collection"])
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])

    # --- document_versions ---------------------------------------------------
    op.create_table(
        "document_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("effective_date", sa.Date, nullable=True),
        sa.Column("content_hash", sa.Text, nullable=False, unique=True),
        sa.Column("supersedes_id", UUID(as_uuid=True), sa.ForeignKey("document_versions.id"), nullable=True),
        sa.Column("blob_uri", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    # --- sections (structural tree, aligned across versions via `path`) ------
    op.create_table(
        "sections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("sections.id"), nullable=True),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("heading", sa.Text, nullable=True),
        sa.Column("number", sa.Text, nullable=True),
        sa.Column("page_start", sa.Integer, nullable=True),
        sa.Column("page_end", sa.Integer, nullable=True),
        sa.Column("ordinal", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_sections_version_id", "sections", ["version_id"])
    op.create_index("ix_sections_version_path", "sections", ["version_id", "path"])

    # --- images ---------------------------------------------------------------
    op.create_table(
        "images",
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
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("caption_model", sa.Text, nullable=True),
        sa.Column("captioned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_images_version_id", "images", ["version_id"])
    # Speeds up "have we already captioned a byte-identical image" lookups
    # (repeated logos/headers across a long PDF) without a full rescan.
    op.create_index("ix_images_phash", "images", ["phash"])

    # --- chunks (text AND image chunks share this table/index) ----------------
    op.create_table(
        "chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", UUID(as_uuid=True), sa.ForeignKey("sections.id"), nullable=True),
        sa.Column("chunk_type", sa.Text, nullable=False, server_default="text"),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("image_id", UUID(as_uuid=True), sa.ForeignKey("images.id"), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column("embedding_model", sa.Text, nullable=True),
        sa.Column("page_start", sa.Integer, nullable=True),
        sa.Column("page_end", sa.Integer, nullable=True),
        sa.Column(
            "tsv",
            TSVECTOR,
            sa.Computed("to_tsvector('english', coalesce(content, ''))", persisted=True),
            nullable=True,
        ),
        sa.CheckConstraint("chunk_type in ('text', 'image', 'table')", name="ck_chunks_chunk_type"),
    )
    op.create_index("ix_chunks_version_id", "chunks", ["version_id"])
    op.create_index("ix_chunks_chunk_type", "chunks", ["chunk_type"])
    # HNSW for vector similarity (pgvector >= 0.5). Cosine distance to match
    # OpenAI/Anthropic embedding conventions; switch to vector_l2_ops if you
    # change embedders and they recommend otherwise.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)"
    )
    op.execute("CREATE INDEX ix_chunks_tsv_gin ON chunks USING gin (tsv)")

    # --- agent_tables (persistent, agent- and user-editable) -------------------
    op.create_table(
        "agent_tables",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("columns", JSONB, nullable=False),
        sa.Column("data", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Text, nullable=False, server_default="agent"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("created_by in ('agent', 'user')", name="ck_agent_tables_created_by"),
    )
    op.create_index("ix_agent_tables_workspace_id", "agent_tables", ["workspace_id"])

    op.create_table(
        "agent_table_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("table_id", UUID(as_uuid=True), sa.ForeignKey("agent_tables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("table_id", "revision", name="uq_agent_table_revisions_table_revision"),
    )


def downgrade() -> None:
    op.drop_table("agent_table_revisions")
    op.drop_table("agent_tables")
    op.drop_table("chunks")
    op.drop_table("images")
    op.drop_table("sections")
    op.drop_table("document_versions")
    op.drop_table("documents")
    # Extensions are left in place deliberately — other databases on the same
    # Postgres instance (managed Neon/Supabase projects especially) may depend
    # on them, and dropping is a project-wide, not schema-wide, operation.
