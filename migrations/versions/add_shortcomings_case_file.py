"""add shortcomings column to case_file

Revision ID: add_shortcomings_case_file
Revises: merge_inspection_heads
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa

revision = "add_shortcomings_case_file"
down_revision = "merge_inspection_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("case_file", sa.Column("shortcomings", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("case_file", "shortcomings")
