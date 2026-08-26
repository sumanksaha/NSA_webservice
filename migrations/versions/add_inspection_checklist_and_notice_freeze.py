"""add checklist_json + notice_issued_at to inspection

Revision ID: add_inspection_checklist_notice
Revises: add_inspection_visit_purpose
Create Date: 2026-08-27

``checklist_json`` stores the 12-item FSO inspection checklist as a single
JSON blob ({"field_name": "yes"/"no"}). ``notice_issued_at`` stamps the first
Improvement Notice render; non-null freezes the record (PUT -> 409).

Dev environments create tables via the startup ``db.create_all()`` fallback,
so the migration matters mainly for Postgres.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_inspection_checklist_notice"
down_revision = "add_inspection_visit_purpose"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.add_column(sa.Column("checklist_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("notice_issued_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.drop_column("notice_issued_at")
        batch_op.drop_column("checklist_json")
