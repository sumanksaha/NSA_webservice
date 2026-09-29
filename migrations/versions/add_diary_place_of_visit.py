"""add place_of_visit to work_diary (monthly diary grid)

Monthly Diary rows need a per-day "Place of Visit" input so the entry can
be printed in the official Work Diary report column (ii).

Revision ID: add_diary_place_of_visit
Revises: add_work_diary_table
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa

revision = "add_diary_place_of_visit"
down_revision = "add_work_diary_table"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    cols = [c["name"] for c in inspector.get_columns("work_diary")] if "work_diary" in inspector.get_table_names() else []
    if "place_of_visit" not in cols:
        op.add_column("work_diary", sa.Column("place_of_visit", sa.String(length=200), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    cols = [c["name"] for c in inspector.get_columns("work_diary")] if "work_diary" in inspector.get_table_names() else []
    if "place_of_visit" in cols:
        op.drop_column("work_diary", "place_of_visit")
