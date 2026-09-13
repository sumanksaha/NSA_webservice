"""merge: reconcile add_legal_document_versioning and add_delete_protection_fssai heads

Without this merge, `flask db upgrade` fails with "Multiple head revisions
are present", which blocks the Render boot-time migration step entirely.

Revision ID: merge_heads_2026_09
Revises: add_legal_document_versioning, add_delete_protection_fssai
Create Date: 2026-09-13
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "merge_heads_2026_09"
down_revision = (
    "add_legal_document_versioning",
    "add_delete_protection_fssai",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
