"""add sample collection fields to inspection

Revision ID: add_inspection_sample_collection
Revises: add_notepad_daily_plan
Create Date: 2026-08-28

Adds ``sample_collected`` (nullable Boolean) and ``sample_code`` (nullable
String) to the ``inspection`` table so the Inspection UI can record whether
a sample was drawn during the visit and attach the SL/WB/XXXXXX/XXXX/XXXXX
sample code that links up with the Sample UI. Both columns are nullable so
existing rows keep working (NULL == not collected / no code recorded).

Note: this repo currently has multiple Alembic heads (see
``migrations/versions/``); this revision chains off ``add_notepad_daily_plan``
(the most recent). Dev environments create tables via the startup
``db.create_all()`` fallback, so the migration matters mainly for Postgres.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_inspection_sample_collection"
down_revision = "add_notepad_daily_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sample_collected", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("sample_code", sa.String(length=100), nullable=True))
        batch_op.create_index(
            "idx_inspection_sample_code", ["sample_code"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.drop_index("idx_inspection_sample_code", table_name="inspection")
        batch_op.drop_column("sample_code")
        batch_op.drop_column("sample_collected")