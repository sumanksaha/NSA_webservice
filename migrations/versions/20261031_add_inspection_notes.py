"""Restore the ``inspection.notes`` column.

The column was introduced by the "inspection notes for discarded items" feature
(abadccf) but dropped from the model in b4dcb4d while the edit form and the
adjudication hand-off kept reading/writing it. The model now declares it again,
so databases created before this revision need the column or the boot-time
required-columns check (``app/db_bootstrap.py``) refuses to start.

Idempotent: skips the column when it is already present, which is the case for
databases where the feature originally shipped.

Revision ID: 20261031_inspection_notes
Revises: 20261030_reinspection_cols
Create Date: 2026-10-31
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261031_inspection_notes"
down_revision = "20261030_reinspection_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    present = {col["name"] for col in sa.inspect(bind).get_columns("inspection")}
    if "notes" not in present:
        op.add_column("inspection", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = {col["name"] for col in sa.inspect(bind).get_columns("inspection")}
    if "notes" in present:
        op.drop_column("inspection", "notes")
