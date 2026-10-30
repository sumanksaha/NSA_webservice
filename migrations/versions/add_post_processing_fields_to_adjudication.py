"""Add optional post-processing placeholder fields to case_files and adjudications.

Adds three nullable columns to both the sample-adjudication (CaseFile) and
non-sample-adjudication (Adjudication) tables:

- ``case_submission_date`` (DateTime) — date the case was submitted
- ``hearing_date`` (DateTime) — date of the hearing
- ``penalty`` (String) — penalty amount/direction

These are post-processing placeholder fields: they are stored as data but are
NOT rendered into the petition document. All three are optional — existing rows
keep NULL and the UI allows them to be left blank.

Idempotent: skips columns that are already present so fresh ``create_all``
databases and hand-repaired databases do not fail on deploy.

Revision ID: 20261028_adj_post_fields
Revises: 20261026_kg_graph_upsert_support
Create Date: 2026-10-28
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_post_processing_fields_to_adjudication"
down_revision = "20261026_kg_graph_upsert_support"
branch_labels = None
depends_on = None

_NEW_COLUMNS = [
    sa.Column("case_submission_date", sa.DateTime(), nullable=True),
    sa.Column("hearing_date", sa.DateTime(), nullable=True),
    sa.Column("penalty", sa.String(length=200), nullable=True),
]


def _add_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    present = {col["name"] for col in sa.inspect(bind).get_columns(table)}
    if column.name not in present:
        op.add_column(table, column)


def upgrade() -> None:
    for table in ("case_files", "adjudications"):
        for column in _NEW_COLUMNS:
            _add_if_missing(table, column)


def downgrade() -> None:
    for table in ("adjudications", "case_files"):
        for column in _NEW_COLUMNS:
            op.drop_column(table, column.name)
