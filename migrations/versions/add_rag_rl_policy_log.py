"""add rag_rl_policy_log table (Phase 4: RL-augmented retrieval)

Revision ID: add_rag_rl_policy_log
Revises: add_is_unsafe_to_case_files
Create Date: 2026-10-07

Creates the ``rag_rl_policy_log`` table to store (context, action, reward)
tuples for the RL contextual multi-armed bandit that tunes retrieval
parameters (``top_k``, ``rrf_k``) per query context.

Mirrors the ``RLEGPolicyLog`` ORM model in ``app/models/rag.py``.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_rag_rl_policy_log"
down_revision = "add_is_unsafe_to_case_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_rl_policy_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("query_type", sa.String(length=32), nullable=False),
        sa.Column("legal_confidence_bucket", sa.String(length=16), nullable=False),
        sa.Column("has_identifier", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("query_length_bucket", sa.String(length=16), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("rrf_k", sa.Float(), nullable=False),
        sa.Column("reward", sa.Float(), nullable=False),
        sa.Column("faithfulness", sa.Float(), nullable=True),
        sa.Column("groundedness", sa.Float(), nullable=True),
        sa.Column("citation_recall", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("is_exploration", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_rl_policy_created", "rag_rl_policy_log", ["created_at"])
    op.create_index(
        "idx_rl_policy_context",
        "rag_rl_policy_log",
        ["query_type", "legal_confidence_bucket", "has_identifier", "query_length_bucket"],
    )
    op.create_index("idx_rl_policy_exploration", "rag_rl_policy_log", ["is_exploration"])


def downgrade() -> None:
    op.drop_index("idx_rl_policy_exploration", table_name="rag_rl_policy_log")
    op.drop_index("idx_rl_policy_context", table_name="rag_rl_policy_log")
    op.drop_index("idx_rl_policy_created", table_name="rag_rl_policy_log")
    op.drop_table("rag_rl_policy_log")
