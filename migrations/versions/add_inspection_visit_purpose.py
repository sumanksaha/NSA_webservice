"""add visit_purpose to inspection

Revision ID: add_inspection_visit_purpose
Revises: add_fssai_lookup_tables
Create Date: 2026-08-26

Adds the explicit per-visit purpose ("routine" | "complaint") that FSOs now
pick at inspection-entry time; the Work Diary uses it instead of inferring
from the presence of a ``problem`` description. Nullable so legacy rows keep
working via the heuristic fallback.

Note: this repo currently has multiple Alembic heads (see
``migrations/versions/``); this revision chains off ``add_fssai_lookup_tables``
(the most recent). Dev environments create tables via the startup
``db.create_all()`` fallback, so the migration matters mainly for Postgres.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_inspection_visit_purpose"
down_revision = "add_fssai_lookup_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.add_column(sa.Column("visit_purpose", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.drop_column("visit_purpose")
