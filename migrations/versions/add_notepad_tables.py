"""create notepad tables (note, note_evaluation)

Revision ID: add_notepad_tables
Revises: add_inspection_checklist_notice
Create Date: 2026-08-28

``note`` — Notepad intake-queue items (pasted/PDF text), shared with all
users by default. ``note_evaluation`` — append-only AI evaluation payloads.
See ``docs/NOTEPAD_IMPLEMENTATION_PLAN.md``.

Dev environments create tables via the startup ``db.create_all()`` fallback,
so the migration matters mainly for Postgres.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_notepad_tables"
down_revision = "merge_inspection_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "author_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False, server_default="pasted"),
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("implemented_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_note_author_id", "note", ["author_id"])
    op.create_index("idx_note_status", "note", ["status"])

    op.create_table(
        "note_evaluation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "note_id",
            sa.Integer(),
            sa.ForeignKey("note.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("provider_model", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_note_evaluation_note_id", "note_evaluation", ["note_id"])


def downgrade() -> None:
    op.drop_index("idx_note_evaluation_note_id", table_name="note_evaluation")
    op.drop_table("note_evaluation")
    op.drop_index("idx_note_status", table_name="note")
    op.drop_index("idx_note_author_id", table_name="note")
    op.drop_table("note")
