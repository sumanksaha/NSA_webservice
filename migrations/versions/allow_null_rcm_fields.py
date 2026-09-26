"""allow null RCM-exempt fields on case_files

Retailer-cum-Manufacturer loose foods have no separate manufacturer and
carry no batch / mfg / expiry numbers, so the app legitimately stores
NULL there (see app/shared/rcm_policy.py). The model declares these
columns nullable, but the production schema (baseline
``add_missing_base_tables``) still enforces NOT NULL — every RCM save
500s with ``NotNullViolation: null value in column "mfg_date"``
(observed 2026-09-26 on case 2026/FSS/108, Mutton biryani).

Relaxes the four RCM-exempt columns to match the model. Fresh
``create_all`` databases already have them nullable; the batch alter is
a no-op there.

Revision ID: allow_null_rcm_fields
Revises: add_retailer_cum_manufacturer
Create Date: 2026-09-26

NOTE: the id must stay within 32 chars — production's
``alembic_version.version_num`` is VARCHAR(32) and a longer id aborts
the deploy with StringDataRightTruncation on the version stamp.
"""

from alembic import op
import sqlalchemy as sa


revision = "allow_null_rcm_fields"
down_revision = "add_retailer_cum_manufacturer"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.alter_column("mfg_date", existing_type=sa.DateTime(), nullable=True)
        batch_op.alter_column("expiry_date", existing_type=sa.DateTime(), nullable=True)
        batch_op.alter_column(
            "manufacturer_report_receive_date", existing_type=sa.DateTime(), nullable=True
        )
        batch_op.alter_column("batch_no", existing_type=sa.String(100), nullable=True)


def downgrade():
    # Requires no NULLs in these columns (fails loudly otherwise —
    # backfilling batch/mfg/expiry with placeholders would falsify
    # records, and RCM cases legitimately have none).
    with op.batch_alter_table("case_files", schema=None) as batch_op:
        batch_op.alter_column("mfg_date", existing_type=sa.DateTime(), nullable=False)
        batch_op.alter_column("expiry_date", existing_type=sa.DateTime(), nullable=False)
        batch_op.alter_column(
            "manufacturer_report_receive_date", existing_type=sa.DateTime(), nullable=False
        )
        batch_op.alter_column("batch_no", existing_type=sa.String(100), nullable=False)
