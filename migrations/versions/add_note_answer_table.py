"""add note_answer table for the Notepad Legal-RAG Q&A thread

Revision ID: add_note_answer_table
Revises: add_rag_answer_feedback
Create Date: 2026-10-11

``note_answer`` — one append-only grounded RAG answer to one question in a
Note's thread: the exact question asked, the answer, citations (JSON), the
statement snapshot at ask time, verification signals (groundedness,
hallucination flag), the degraded flag for circuit-open stub fallbacks, and
the ``rag_query_log`` join-back. See ``docs/NOTEPAD_LEGAL_RAG_SPEC.md`` §5.1.

Dev environments create tables via the startup ``db.create_all()`` fallback,
so the migration matters mainly for Postgres.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_note_answer_table"
down_revision = "add_rag_answer_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_answer",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "note_id",
            sa.Integer(),
            sa.ForeignKey("note.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("citations", sa.Text(), nullable=True),
        sa.Column("note_snapshot", sa.Text(), nullable=True),
        sa.Column("groundedness_score", sa.Float(), nullable=True),
        sa.Column("hallucination_detected", sa.Boolean(), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider_model", sa.String(200), nullable=True),
        sa.Column("token_usage", sa.Integer(), nullable=True),
        sa.Column("rag_log_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_note_answer_note_id", "note_answer", ["note_id"])


def downgrade() -> None:
    op.drop_index("idx_note_answer_note_id", table_name="note_answer")
    op.drop_table("note_answer")
