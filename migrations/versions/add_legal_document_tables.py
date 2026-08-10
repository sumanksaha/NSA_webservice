"""Add corpus tables for Agent A (LegalDocument, LegalChunk)

Creates the two corpus/embedding tables defined in ``RAG_AGENT_A_SCOPE.md``
§5.3 / §5.2:

- legal_document   corpus document registry (file_hash UNIQUE -> SHA-256 dedup)
- legal_chunk      per-chunk metadata + content_hash (Day 5 dedup)

Revision ID: add_legal_document_tables
Revises: add_rag_tables
Create Date: 2026-08-08

"""
from alembic import op
import sqlalchemy as sa


revision = "add_legal_document_tables"
down_revision = "add_rag_tables"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legal_document",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_uri", sa.String(length=512), nullable=False, unique=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("authority", sa.String(length=255), nullable=True),
        sa.Column("jurisdiction", sa.String(length=255), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("enactment_date", sa.Date(), nullable=True),
        sa.Column("amended_date", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default="1", nullable=True),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=True),
        sa.Column("qdrant_collection", sa.String(length=64), server_default="fssai_legal_768", nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_legal_document_status", "legal_document", ["status"])
    op.create_index("idx_legal_document_type", "legal_document", ["document_type"])

    op.create_table(
        "legal_chunk",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("section_number", sa.String(length=32), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("hierarchy_level", sa.Integer(), server_default="0", nullable=True),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column("references", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("qdrant_point_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_legal_chunk_doc_section", "legal_chunk", ["document_id", "section_number"])
    op.create_index("idx_legal_chunk_parent", "legal_chunk", ["parent_id"])
    op.create_index("idx_legal_chunk_content_hash", "legal_chunk", ["content_hash"])
    # Single-column indexes mirroring the model's per-column ``index=True``
    # flags (scope §5.2) — without these, ``flask db migrate`` would drift.
    op.create_index("ix_legal_chunk_document_id", "legal_chunk", ["document_id"])
    op.create_index("ix_legal_chunk_document_type", "legal_chunk", ["document_type"])
    op.create_index("ix_legal_chunk_section_number", "legal_chunk", ["section_number"])
    op.create_index("ix_legal_chunk_chunk_index", "legal_chunk", ["chunk_index"])
    op.create_index("ix_legal_chunk_parent_id", "legal_chunk", ["parent_id"])
    op.create_unique_constraint("uq_chunk_doc_index", "legal_chunk", ["document_id", "chunk_index"])


def downgrade():
    op.drop_constraint("uq_chunk_doc_index", "legal_chunk", type_="unique")
    op.drop_index("ix_legal_chunk_parent_id", table_name="legal_chunk")
    op.drop_index("ix_legal_chunk_chunk_index", table_name="legal_chunk")
    op.drop_index("ix_legal_chunk_section_number", table_name="legal_chunk")
    op.drop_index("ix_legal_chunk_document_type", table_name="legal_chunk")
    op.drop_index("ix_legal_chunk_document_id", table_name="legal_chunk")
    op.drop_index("idx_legal_chunk_content_hash", table_name="legal_chunk")
    op.drop_index("idx_legal_chunk_parent", table_name="legal_chunk")
    op.drop_index("idx_legal_chunk_doc_section", table_name="legal_chunk")
    op.drop_table("legal_chunk")

    op.drop_index("idx_legal_document_type", table_name="legal_document")
    op.drop_index("idx_legal_document_status", table_name="legal_document")
    op.drop_table("legal_document")
