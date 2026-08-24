"""merge: reconcile add_rag_query_log_pipeline and add_phase17_sync_tables heads

Both branches descend from fix_rbac_tables. Without this merge, `flask db
upgrade` fails with "Multiple head revisions are present", which blocks the
Render boot-time migration step entirely.

Revision ID: merge_heads_phase17
Revises: add_rag_query_log_pipeline, add_phase17_sync_tables
Create Date: 2026-08-24
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "merge_heads_phase17"
down_revision = (
    "add_rag_query_log_pipeline",
    "add_phase17_sync_tables",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
