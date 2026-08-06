"""add_rbac_and_comment_tables

Phase 18 (task.md / plan.md §3.4): multi-user Role-Based Access Control.

New tables:
  - role            canonical roles (admin, inspector, adjudicator, viewer)
  - user_roles      many-to-many association between user <-> role
  - comment         per-case document comments (anchored to a section_id)

Revision ID: a1b2c3d4e5f6
Revises: add_timeline_event_table
Create Date: 2026-08-05 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "add_timeline_event_table"
branch_labels = None
depends_on = None


def upgrade():
    # roles table
    op.create_table(
        "role",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.String(256)),
    )
    # association table user_roles
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("idx_user_roles_user_id", "user_roles", ["user_id"], unique=False)
    op.create_index("idx_user_roles_role_id", "user_roles", ["role_id"], unique=False)
    # comments table
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
    op.create_index("ix_comment_case_id", "comment", ["case_id"], unique=False)
    op.create_index("ix_comment_user_id", "comment", ["user_id"], unique=False)


def downgrade():
    op.drop_index("ix_comment_user_id", table_name="comment")
    op.drop_index("ix_comment_case_id", table_name="comment")
    op.drop_table("comment")
    op.drop_index("idx_user_roles_role_id", table_name="user_roles")
    op.drop_index("idx_user_roles_user_id", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_table("role")
