"""Add witness_name to case_files.

The petition names the local witness the FSO called while taking the sample.
The value was hardcoded in the HTML template and absent from the form, so it
could not be captured per case. This column makes it a per-case, optional
field; pre-existing rows stay NULL and the petition prints a blank line.

Revision ID: add_witness_name_case_file
Revises: add_shortcomings_case_file
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_witness_name_case_file"
down_revision = "add_shortcomings_case_file"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: fresh `create_all` databases already have this column while
    # stamped at or below the previous head — a bare add would fail the deploy.
    bind = op.get_bind()
    present = {col["name"] for col in sa.inspect(bind).get_columns("case_files")}
    if "witness_name" not in present:
        op.add_column(
            "case_files",
            sa.Column("witness_name", sa.String(length=200), nullable=True),
        )


def downgrade():
    op.drop_column("case_files", "witness_name")
