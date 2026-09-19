"""add_versioning_to_legal_document

Revision ID: add_legal_document_versioning
Revises: add_fso_email_config
Create Date: 2026-09-07

Adds versioning columns to LegalDocument for corpus rollback capability.
- version_id: integer, default 1, bumped on every re-ingest
- is_latest: boolean, marks the active version of a document

Matches the LegalDocument model (app/models/rag.py): no document_id column
exists on legal_document (that lives on legal_chunk), so no version index
is created here.
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
    op.add_column(
        "legal_document", sa.Column("is_latest", sa.Boolean(), nullable=False, server_default=sa.text("true"))
    )


def downgrade() -> None:
    """Remove versioning columns from legal_document table."""
    op.drop_column("legal_document", "is_latest")
    op.drop_column("legal_document", "version_id")
