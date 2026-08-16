"""add rag_query_log pipeline column

Revision ID: add_rag_query_log_pipeline
Revises: add_chunk_enrichment_tables
Create Date: 2026-08-16

Stamps each RAG query log with the calling pipeline (``legacy`` / ``agent``)
for the LangGraph rollout A/B comparison (plan §8).  Nullable — existing
rows and the pre-M3 code path leave it unset.
"""

from alembic import op
import sqlalchemy as sa

revision = "add_rag_query_log_pipeline"
down_revision = "add_chunk_enrichment_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rag_query_log", sa.Column("pipeline", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("rag_query_log", "pipeline")
