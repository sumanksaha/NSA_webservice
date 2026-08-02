"""add_is_admin_to_user

Adds the ``is_admin`` boolean flag to the ``user`` table, enabling
admin-only flows (e.g. resetting other users' passwords from the UI).

IMPORTANT: this migration is idempotent (column existence is checked via the
inspector before adding) because production was provisioned with
``db.create_all()`` and may already carry the column when ``flask db upgrade``
runs on a later deploy. On PostgreSQL the existing rows are back-filled to
``false`` via the server default; only an explicit promotion (``--admin`` on
``scripts/create_user.py`` or an UPDATE) grants admin rights.

Revision ID: add_is_admin_to_user
Revises: add_app_secrets_table
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_is_admin_to_user"
down_revision = "add_app_secrets_table"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("user")]
    if "is_admin" not in columns:
        op.add_column(
            "user",
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("user")]
    if "is_admin" in columns:
        op.drop_column("user", "is_admin")
