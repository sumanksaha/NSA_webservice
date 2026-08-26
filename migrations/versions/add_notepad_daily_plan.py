"""add daily_plans table for the Notepad Daily Plan feature

Revision ID: add_daily_plan_table
Revises: add_inspection_checklist_notice
Create Date: 2026-08-27

Append-only AI-generated battle plans; payload JSON holds items / ranking /
portfolio_rationale. Dev environments create tables via db.create_all().
"""

import sqlalchemy as sa
from alembic import op

revision = "add_daily_plan_table"
down_revision = "add_notepad_tables"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "daily_plan",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "author_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_daily_plan_author_id", "daily_plan", ["author_id"])


def downgrade():
    op.drop_index("idx_daily_plan_author_id", table_name="daily_plan")
    op.drop_table("daily_plan")
