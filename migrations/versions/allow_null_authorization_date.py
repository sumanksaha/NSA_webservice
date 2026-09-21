"""allow null authorization_date on case_files

The authorization date is issued by the Designated Officer when the
permission file is submitted — after first data entry.  It stays editable
on the case and is recorded on the petition file.

Revision ID: allow_null_authorization_date
Revises: add_auditor_plan_to_inspection
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa


revision = "allow_null_authorization_date"
down_revision = "add_auditor_plan_to_inspection"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.alter_column("authorization_date", existing_type=sa.DateTime(), nullable=True)


def downgrade():
    # Requires no NULL authorization_date rows (fails loudly otherwise —
    # backfilling a legal issue date with a placeholder would falsify records).
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.alter_column("authorization_date", existing_type=sa.DateTime(), nullable=False)
