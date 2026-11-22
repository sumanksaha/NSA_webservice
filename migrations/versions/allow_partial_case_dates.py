"""allow partial case-file date entry

Case files can now be saved before every date is known.  The six date columns
below become nullable so a draft can be persisted without inventing placeholder
legal dates; generation/preview still require the full form.

Revision ID: allow_partial_case_dates
Revises: drop_versions_table
Create Date: 2026-11-22
"""

from alembic import op
import sqlalchemy as sa

revision = "allow_partial_case_dates"
down_revision = "drop_versions_table"
branch_labels = None
depends_on = None

_DATE_COLUMNS = (
    "inspection_date",
    "sample_submission_date",
    "do_receipt_date",
    "analyst_report_date",
    "directive_letter_date",
    "retailer_report_receive_date",
)


def upgrade():
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        for column in _DATE_COLUMNS:
            batch_op.alter_column(column, existing_type=sa.DateTime(), nullable=True)


def downgrade():
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        for column in _DATE_COLUMNS:
            batch_op.alter_column(column, existing_type=sa.DateTime(), nullable=False)
