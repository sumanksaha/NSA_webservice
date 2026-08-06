"""fix missing RBAC tables (role / user_roles / comment)

The Phase 18 RBAC migration (``a1b2c3d4e5f6``) was inserted into the
migration chain AFTER some production databases had already been migrated
(or stamped) past that point. Alembic never replays an ancestor migration,
so ``flask db upgrade`` is a permanent no-op for it and the ``role`` /
``user_roles`` / ``comment`` tables were never created. Because
``User.roles`` is an eager (``lazy="joined"``) relationship, every ``User``
query — including ``POST /auth/login`` — then crashed with
``psycopg2.errors.UndefinedTable: relation "user_roles" does not exist``.

This migration re-creates those three tables idempotently (guarded by
existence checks), so it is a safe no-op on databases where the original
migration already ran or where ``db.create_all()`` created the tables.

Revision ID: fix_rbac_tables
Revises: add_food_cell_do_intimation
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "fix_rbac_tables"
down_revision = "add_food_cell_do_intimation"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    # Tables are created only when missing; indexes use IF NOT EXISTS so the
    # migration is fully idempotent per-object (safe even if a table exists
    # without its indexes). Both SQLite and PostgreSQL support IF NOT EXISTS.
    if not _table_exists("role"):
        op.create_table(
            "role",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("description", sa.String(256)),
        )

    if not _table_exists("user_roles"):
        op.create_table(
            "user_roles",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("role_id", sa.Integer(), sa.ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
        )
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_user_id ON user_roles (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_role_id ON user_roles (role_id)")

    if not _table_exists("comment"):
        op.create_table(
            "comment",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("case_id", sa.Integer(), nullable=False),
            sa.Column("case_type", sa.String(32), nullable=False, server_default="case_file"),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("section_id", sa.String(128)),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_comment_case_id ON comment (case_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_comment_user_id ON comment (user_id)")


def downgrade():
    if _table_exists("comment"):
        op.drop_index("ix_comment_user_id", table_name="comment")
        op.drop_index("ix_comment_case_id", table_name="comment")
        op.drop_table("comment")
    if _table_exists("user_roles"):
        op.drop_index("idx_user_roles_role_id", table_name="user_roles")
        op.drop_index("idx_user_roles_user_id", table_name="user_roles")
        op.drop_table("user_roles")
    if _table_exists("role"):
        op.drop_table("role")
