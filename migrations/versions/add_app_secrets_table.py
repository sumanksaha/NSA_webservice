"""add_app_secrets_table

Adds the ``app_secrets`` key/value table used to auto-provision a stable
SECRET_KEY in production when the env var is missing.

IMPORTANT: this migration is idempotent (CREATE TABLE IF NOT EXISTS) because
the app factory (``app/__init__.py::_load_or_create_production_secret_key``)
creates this table via raw SQL at boot BEFORE ``flask db upgrade`` runs — the
start command is ``flask db upgrade && gunicorn``, and importing the app to
run migrations itself triggers the SECRET_KEY guard. On a fresh DB where
SECRET_KEY IS set, the migration is the only creator; on a recovery boot where
SECRET_KEY is missing, the table already exists and this is a no-op.

Revision ID: add_app_secrets_table
Revises: a7776b1a54e3
Create Date: 2026-08-02
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_app_secrets_table"
down_revision = "a7776b1a54e3"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: the runtime auto-provisioner may have already created it.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_secrets (
            name VARCHAR(64) PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS app_secrets")
