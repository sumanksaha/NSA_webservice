"""Add RAG pipeline tables (Agent B — Phase 1 Retrieval Foundation)

Creates three tables for the Retrieval / Generation / Evaluation pipeline:

- rag_query_log    per-query retrieval log (hash-keyed for dedup/trending)
- rag_eval_result   per-query evaluation metric scores
- rag_eval_dataset   ground-truth queries for batch evaluation

This migration also merges the two current Alembic heads:
``add_airtable_base_map`` (Priority 7) and ``fix_rbac_tables`` (Phase 18),
so subsequent ``flask db upgrade`` runs from a single head.

Revision ID: add_rag_tables
Revises: add_airtable_base_map, fix_rbac_tables
Create Date: 2026-08-07

"""
from alembic import op
import sqlalchemy as sa


revision = "add_rag_tables"
down_revision = ("add_airtable_base_map", "fix_rbac_tables")
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rag_query_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_type", sa.String(length=32), nullable=False),
        sa.Column("retrieved_chunk_ids", sa.JSON(), nullable=True),
        sa.Column("retrieval_scores", sa.JSON(), nullable=True),
        sa.Column("retrieval_latency_ms", sa.Integer(), nullable=True),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column("llm_model", sa.String(length=128), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("cited_chunk_ids", sa.JSON(), nullable=True),
        sa.Column("groundedness_score", sa.Float(), nullable=True),
        sa.Column("hallucination_detected", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("hallucinated_claims", sa.JSON(), nullable=True),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_rag_query_log_created", "rag_query_log", ["created_at"])
    op.create_index("idx_rag_query_log_type", "rag_query_log", ["query_type"])
    op.create_index("idx_rag_query_log_content_hash", "rag_query_log", ["content_hash"])

    op.create_table(
        "rag_eval_result",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("eval_run_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=True),
        sa.Column("expected_citations", sa.JSON(), nullable=True),
        sa.Column("actual_answer", sa.Text(), nullable=True),
        sa.Column("actual_citations", sa.JSON(), nullable=True),
        sa.Column("faithfulness_score", sa.Float(), nullable=True),
        sa.Column("answer_relevance_score", sa.Float(), nullable=True),
        sa.Column("context_precision_score", sa.Float(), nullable=True),
        sa.Column("context_recall_score", sa.Float(), nullable=True),
        sa.Column("citation_recall_score", sa.Float(), nullable=True),
        sa.Column("groundedness_score", sa.Float(), nullable=True),
        sa.Column("avg_score", sa.Float(), nullable=True),
        sa.Column("retrieval_mrr", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("passed", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_rag_eval_run", "rag_eval_result", ["eval_run_id"])
    op.create_index("idx_rag_eval_created", "rag_eval_result", ["created_at"])

    op.create_table(
        "rag_eval_dataset",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_type", sa.String(length=32), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=False),
        sa.Column("expected_section", sa.String(length=32), nullable=True),
        sa.Column("expected_citations", sa.JSON(), nullable=True),
        sa.Column("difficulty", sa.String(length=16), server_default="medium", nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_eval_dataset_active", "rag_eval_dataset", ["is_active"])
    op.create_index("idx_eval_dataset_type", "rag_eval_dataset", ["query_type"])


def downgrade():
    op.drop_index("idx_eval_dataset_type", table_name="rag_eval_dataset")
    op.drop_index("idx_eval_dataset_active", table_name="rag_eval_dataset")
    op.drop_table("rag_eval_dataset")

    op.drop_index("idx_rag_eval_created", table_name="rag_eval_result")
    op.drop_index("idx_rag_eval_run", table_name="rag_eval_result")
    op.drop_table("rag_eval_result")

    op.drop_index("idx_rag_query_log_content_hash", table_name="rag_query_log")
    op.drop_index("idx_rag_query_log_type", table_name="rag_query_log")
    op.drop_index("idx_rag_query_log_created", table_name="rag_query_log")
    op.drop_table("rag_query_log")
