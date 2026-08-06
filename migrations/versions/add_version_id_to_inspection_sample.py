"""add version_id optimistic-concurrency columns to inspection and sample

Revision ID: add_version_id_inspection_sample
Revises: fix_schema_datetime_fk
Create Date: 2026-08-06

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_version_id_inspection_sample"
down_revision = "fix_schema_datetime_fk"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.add_column(sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"))

    with op.batch_alter_table("sample", schema=None) as batch_op:
        batch_op.add_column(sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    with op.batch_alter_table("sample", schema=None) as batch_op:
        batch_op.drop_column("version_id")

    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.drop_column("version_id")
