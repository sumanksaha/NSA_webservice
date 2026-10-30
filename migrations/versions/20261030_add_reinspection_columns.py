"""Add reinspection fields to adjudications and inspection.

Adds columns that exist on the Adjudication/Inspection models but were never
introduced by a migration, so pre-existing databases (e.g. Render) lack them
and fail the boot-time required-columns check:

adjudications
- is_reinspection (Boolean)
- days_allowed_for_errors (Integer)
- errors_in_second_inspection (Integer)
- notes (Text)

inspection
- is_reinspection (Boolean)
- days_allowed_for_errors (Integer)
- errors_in_second_inspection (Integer)

Idempotent: skips columns that are already present.

Revision ID: 20261030_reinspection_cols
Revises: merge_all_heads_2026_10, merge_heads_witness_2026_10
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261030_reinspection_cols"
down_revision = ("merge_all_heads_2026_10", "merge_heads_witness_2026_10")
branch_labels = None
depends_on = None

_ADJUDICATION_COLUMNS = [
    sa.Column("is_reinspection", sa.Boolean(), nullable=True),
    sa.Column("days_allowed_for_errors", sa.Integer(), nullable=True),
    sa.Column("errors_in_second_inspection", sa.Integer(), nullable=True),
    sa.Column("notes", sa.Text(), nullable=True),
]

_INSPECTION_COLUMNS = [
    sa.Column("is_reinspection", sa.Boolean(), nullable=True),
    sa.Column("days_allowed_for_errors", sa.Integer(), nullable=True),
    sa.Column("errors_in_second_inspection", sa.Integer(), nullable=True),
]


def _add_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    present = {col["name"] for col in sa.inspect(bind).get_columns(table)}
    if column.name not in present:
        op.add_column(table, column)


def upgrade() -> None:
    for column in _ADJUDICATION_COLUMNS:
        _add_if_missing("adjudications", column)
    for column in _INSPECTION_COLUMNS:
        _add_if_missing("inspection", column)


def downgrade() -> None:
    for column in _INSPECTION_COLUMNS:
        op.drop_column("inspection", column.name)
    for column in _ADJUDICATION_COLUMNS:
        op.drop_column("adjudications", column.name)
