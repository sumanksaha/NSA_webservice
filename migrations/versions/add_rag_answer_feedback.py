"""add rag_answer_feedback + rag_research_target tables (autoresearch input)

Revision ID: add_rag_answer_feedback
Revises: add_rag_rl_policy_log
Create Date: 2026-10-11

Human answer ratings (``rag_answer_feedback``) and the deduplicated research
queue they feed (``rag_research_target``).  A rating at or below
``RAG_FEEDBACK_ENQUEUE_MAX_RATING`` opens or refreshes a research target, so
badly-answered questions become the input list for the next autoresearch
session.  Also stamps ``rag_query_log.research_swept_at`` so the autoresearch
sweep over stored log rows is idempotent.

Mirrors the ``RAGAnswerFeedback`` / ``RAGResearchTarget`` ORM models and the
``RAGQueryLog.research_swept_at`` column in ``app/models/rag.py``.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_rag_answer_feedback"
down_revision = "add_rag_rl_policy_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_answer_feedback",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "query_log_id",
            sa.String(length=36),
            sa.ForeignKey("rag_query_log.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_type", sa.String(length=32), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("failure_reasons", sa.JSON(), nullable=True),
        sa.Column("pipeline", sa.String(length=16), nullable=True),
        sa.Column("rated_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_rag_answer_feedback_created", "rag_answer_feedback", ["created_at"])
    op.create_index("idx_rag_answer_feedback_rating", "rag_answer_feedback", ["rating"])

    op.create_table(
        "rag_research_target",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_type", sa.String(length=32), nullable=True),
        sa.Column("failure_class", sa.String(length=32), nullable=False),
        sa.Column("taxonomy_category", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="human_rating"),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("query_hash", name="uq_rag_research_target_query_hash"),
    )
    op.create_index("idx_rag_research_target_created", "rag_research_target", ["created_at"])
    op.create_index("idx_rag_research_target_status", "rag_research_target", ["status"])

    # Idempotency marker for the autoresearch query-log sweep.
    op.add_column("rag_query_log", sa.Column("research_swept_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("rag_query_log", "research_swept_at")
    op.drop_index("idx_rag_research_target_status", table_name="rag_research_target")
    op.drop_index("idx_rag_research_target_created", table_name="rag_research_target")
    op.drop_table("rag_research_target")
    op.drop_index("idx_rag_answer_feedback_rating", table_name="rag_answer_feedback")
    op.drop_index("idx_rag_answer_feedback_created", table_name="rag_answer_feedback")
    op.drop_table("rag_answer_feedback")
