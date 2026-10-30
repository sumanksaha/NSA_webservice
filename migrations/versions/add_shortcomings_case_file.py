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
    # Table is ``case_files`` (CaseFile.__tablename__); the original revision
    # named it ``case_file``, which does not exist, so the upgrade aborted.
    # Idempotent: fresh ``create_all`` databases already carry the column.
    bind = op.get_bind()
    present = {col["name"] for col in sa.inspect(bind).get_columns("case_files")}
    if "shortcomings" not in present:
        op.add_column("case_files", sa.Column("shortcomings", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("case_files", "shortcomings")
