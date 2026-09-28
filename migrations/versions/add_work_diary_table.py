"""add work_diary table (owner-scoped bulk daily activity entry)

Schema for the Work Diary bulk editor (``app/diary.py``). ``fso_name`` is
half of the primary key so each officer's rows are isolated; the module used
to create/ALTER this table per request, which raced across processes and
skipped the migration workflow.

A pre-release development database may already hold an unscoped
``work_diary`` table (no ``fso_name``, ``work_date``-keyed) created by that
per-request bootstrap. Those rows carry no officer ownership and cannot be
attributed, so the table is dropped and recreated rather than migrated.

Revision ID: add_work_diary_table
Revises: allow_null_rcm_fields
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa

revision = "add_work_diary_table"
down_revision = "allow_null_rcm_fields"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "work_diary" in inspector.get_table_names():
        op.drop_table("work_diary")
    op.create_table(
        "work_diary",
        sa.Column("fso_name", sa.String(length=100), nullable=False),
        sa.Column("work_date", sa.String(length=10), nullable=False),
        sa.Column("activity", sa.String(length=32), nullable=True),
        sa.Column("premises", sa.Integer(), nullable=True),
        sa.Column("samples", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("fso_name", "work_date"),
    )


def downgrade():
    op.drop_table("work_diary")
