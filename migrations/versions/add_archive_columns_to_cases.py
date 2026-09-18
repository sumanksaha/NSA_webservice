"""Add archive (soft-delete) columns to case_files and adjudications.

Archived cases are hidden from the UI case lists but retained in the DB
and synced to Supabase (``is_archived`` flows through the generic
``_model_to_payload`` upsert — the remote ``case_files`` / ``adjudications``
tables need matching ``is_archived`` / ``archived_at`` columns; see
``docs/`` note in the edit+archive change).

Revision ID: add_archive_columns_to_cases
Revises: merge_heads_2026_09
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_archive_columns_to_cases"
down_revision = "merge_heads_2026_09"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("case_files", "adjudications"):
        op.add_column(table, sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.add_column(table, sa.Column("archived_at", sa.DateTime(), nullable=True))
        op.create_index(f"idx_{table}_is_archived", table, ["is_archived"])


def downgrade():
    for table in ("case_files", "adjudications"):
        op.drop_index(f"idx_{table}_is_archived", table_name=table)
        op.drop_column(table, "archived_at")
        op.drop_column(table, "is_archived")
