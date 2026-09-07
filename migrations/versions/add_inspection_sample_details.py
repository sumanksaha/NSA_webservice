"""add sample_count + sample_type to inspection

Revision ID: add_inspection_sample_details
Revises: add_inspection_visit_purpose
Create Date: 2026-08-26

Adds number (count) and kind (type) of samples collected so the Work Diary
Activity column can read: "notice issued at <FBO>, <count> <type> samples
collected".
"""

import sqlalchemy as sa
from alembic import op

revision = "add_inspection_sample_details"
down_revision = "add_inspection_visit_purpose"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sample_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("sample_type", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.drop_column("sample_count")
        batch_op.drop_column("sample_type")
