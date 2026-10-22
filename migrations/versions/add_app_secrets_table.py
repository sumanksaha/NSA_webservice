"""add_app_secrets_table

Revision ID: add_app_secrets_table
Revises: a7776b1a54e3
Create Date: 2026-08-01
"""

from alembic import op

revision = "add_app_secrets_table"
down_revision = "a7776b1a54e3"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_secrets (
            name VARCHAR(64) PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS app_secrets")
