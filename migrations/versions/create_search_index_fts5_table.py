"""create_search_index_fts5_table

Revision ID: create_search_index_fts5_table
Revises: add_settings_annexure_evidence_version_models
Create Date: 2026-08-02

Creates the SQLite FTS5 virtual table (``search_index``) that powers the
global search endpoint.  On PostgreSQL the table is not created — the
search API falls back to ``LIKE`` queries on the regular tables.
"""

from alembic import op
import logging

# revision identifiers, used by Alembic.
revision = "create_search_index_fts5_table"
down_revision = "add_settings_annexure_evidence_version_models"
branch_labels = None
depends_on = None


def upgrade():
    """Create the FTS5 virtual table (SQLite only)."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "sqlite":
        logging.getLogger("alembic").info("FTS5 table skipped on non-SQLite dialect (%s)", dialect)
        return

    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5("
        "    entity_type UNINDEXED,"
        "    entity_id   UNINDEXED,"
        "    title,"
        "    content,"
        "    tokenize = 'porter unicode61'"
        ")"
    )


def downgrade():
    """Drop the FTS5 virtual table (SQLite only)."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "sqlite":
        return
    op.execute("DROP TABLE IF EXISTS search_index")
