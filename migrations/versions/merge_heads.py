"""merge: resolve multiple heads from feature branches

Combines the two divergent migration heads into a single head:
  - add_entity_relationship_tables  (Phase 14: entity/relationship tables)
  - add_version_id_inspection_sample (optimistic-concurrency version_id cols)

Revision ID: merge_heads
Revises: add_entity_relationship_tables, add_version_id_inspection_sample
Create Date: 2026-08-06
"""

from alembic import op


revision = "merge_heads"
down_revision = (
    "add_entity_relationship_tables",
    "add_version_id_inspection_sample",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
