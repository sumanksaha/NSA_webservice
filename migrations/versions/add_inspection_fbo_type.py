"""add fbo_type to inspection

Revision ID: add_inspection_fbo_type
Revises: add_inspection_sample_collection
Create Date: 2026-10-28

Adds ``fbo_type`` (nullable String) to the ``inspection`` table so the
Inspection UI can record whether the FBO is a manufacturer, retailer, or
food vending establishment. Nullable so existing rows keep working.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_inspection_fbo_type"
down_revision = "add_inspection_sample_collection"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _column_exists("inspection", "fbo_type"):
        with op.batch_alter_table("inspection", schema=None) as batch_op:
            batch_op.add_column(sa.Column("fbo_type", sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.drop_column("fbo_type")
