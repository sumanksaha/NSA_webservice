"""Add chunk enrichment store tables (incremental legal enrichment).

Creates the four tables defined in ``app/models/enrichment.py``:

- chunk_enrichment              versioned v1.0 enrichment record per chunk
- enrichment_checkpoint         resumable batch checkpoints (Phase 11)
- chunk_cross_reference         resolved REFERS_TO edges (Phase 6)
- enrichment_resource_usage     memory / timing telemetry (Phase 10)

All tables are additive — nothing here touches ``legal_chunk``,
``legal_document`` or the Qdrant payload (original chunk text stays
immutable).

Revision ID: add_chunk_enrichment_tables
Revises: add_entities_to_legal_chunk
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa


revision = "add_chunk_enrichment_tables"
down_revision = "add_entities_to_legal_chunk"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chunk_enrichment",
        sa.Column("chunk_id", sa.String(length=64), primary_key=True),
        sa.Column("enrichment_version", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("original_sha256", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("llm_used", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_enrichment_status", "chunk_enrichment", ["status"])
    op.create_index("idx_enrichment_version", "chunk_enrichment", ["enrichment_version"])

    op.create_table(
        "enrichment_checkpoint",
        sa.Column("batch_id", sa.String(length=64), primary_key=True),
        sa.Column("last_chunk_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=True),
        sa.Column("enriched", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=True),
        sa.Column("skipped", sa.Integer(), nullable=True),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "chunk_cross_reference",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("source_chunk_id", sa.String(length=64), nullable=False),
        sa.Column("target_chunk_id", sa.String(length=64), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("provenance", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        # SQLite cannot ALTER-add constraints, so the unique edge constraint
        # is declared inline at table creation (batch-mode alternative).
        sa.UniqueConstraint("source_chunk_id", "target_chunk_id", "relation", name="uq_xref_edge"),
    )
    op.create_index("idx_xref_source_relation", "chunk_cross_reference", ["source_chunk_id", "relation"])
    op.create_index("ix_chunk_cross_reference_source_chunk_id", "chunk_cross_reference", ["source_chunk_id"])
    op.create_index("ix_chunk_cross_reference_target_chunk_id", "chunk_cross_reference", ["target_chunk_id"])

    op.create_table(
        "enrichment_resource_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("peak_ram_mb", sa.Float(), nullable=True),
        sa.Column("avg_ram_mb", sa.Float(), nullable=True),
        sa.Column("batch_size", sa.Integer(), nullable=True),
        sa.Column("processed", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=True),
        sa.Column("retries", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_ru_run_id", "enrichment_resource_usage", ["run_id"])


def downgrade():
    # Drop tables wholesale.  The uq_xref_edge unique constraint was declared
    # INLINE at create_table (SQLite cannot ALTER-drop constraints), so
    # dropping the table is the only supported reversal.
    op.drop_index("idx_ru_run_id", table_name="enrichment_resource_usage")
    op.drop_table("enrichment_resource_usage")

    op.drop_index("ix_chunk_cross_reference_target_chunk_id", table_name="chunk_cross_reference")
    op.drop_index("ix_chunk_cross_reference_source_chunk_id", table_name="chunk_cross_reference")
    op.drop_index("idx_xref_source_relation", "chunk_cross_reference", table_name="chunk_cross_reference")
    op.drop_table("chunk_cross_reference")

    op.drop_table("enrichment_checkpoint")

    op.drop_index("idx_enrichment_version", table_name="chunk_enrichment")
    op.drop_index("idx_enrichment_status", "chunk_enrichment")
    op.drop_table("chunk_enrichment")
