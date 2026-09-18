"""add_versioning_to_legal_document

Revision ID: add_legal_document_versioning
Revises: add_fso_email_config
Create Date: 2026-09-07

Adds versioning columns to LegalDocument for corpus rollback capability.
- version_id: integer, default 1, bumped on every re-ingest
- is_latest: boolean, marks the active version of a document
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_legal_document_versioning"
down_revision = "add_fso_email_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add version_id and is_latest columns to legal_document table."""
    op.add_column("legal_document", sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("legal_document", sa.Column("is_latest", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    # Add index for efficient version lookups
    op.create_index("idx_legal_document_version", "legal_document", ["document_id", "version_id"])
    op.create_index("idx_legal_document_is_latest", "legal_document", ["is_latest"])


def downgrade() -> None:
    """Remove versioning columns from legal_document table."""
    op.drop_index("idx_legal_document_is_latest", table_name="legal_document")
    op.drop_index("idx_legal_document_version", table_name="legal_document")
    op.drop_column("legal_document", "is_latest")
    op.drop_column("legal_document", "version_id")
