"""Add retailer_cum_manufacturer to case_files.

Retailer-cum-Manufacturer loose foods (commit 796808d) added the column to
the ``CaseFile`` model without a migration, so every pre-existing database
500s on any case-file read (``UndefinedColumn``, f405) while fresh
``create_all`` databases work. This migration backfills the column so
``flask db upgrade`` repairs those databases.

Revision ID: add_retailer_cum_manufacturer
Revises: allow_null_authorization_date
Create Date: 2026-09-22
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_retailer_cum_manufacturer"
down_revision = "allow_null_authorization_date"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: fresh `create_all` databases (and operators who ran the
    # manual repair SQL) already have this column while stamped at or below
    # the previous head — a bare add would fail the deploy on them.
    bind = op.get_bind()
    present = {col["name"] for col in sa.inspect(bind).get_columns("case_files")}
    if "retailer_cum_manufacturer" not in present:
        op.add_column(
            "case_files",
            sa.Column("retailer_cum_manufacturer", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    op.drop_column("case_files", "retailer_cum_manufacturer")
