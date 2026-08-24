"""add sync_state, sync_conflicts, sync_log tables

Phase 17 (Supabase cloud-sync bridge): tracks per-record sync state,
pending conflicts, and an audit trail — without modifying the existing
business-model tables.

Revision ID: add_phase17_sync_tables
Revises: fix_rbac_tables
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa

revision = "add_phase17_sync_tables"
down_revision = "fix_rbac_tables"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("table_name", sa.String(length=100), nullable=False),
        sa.Column("local_id", sa.Integer, nullable=False),
        sa.Column("sync_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_name", "local_id", name="uq_sync_state_table_local"),
    )
    op.create_index("idx_sync_state_synced", "sync_state", ["synced_at"])
    op.create_index("idx_sync_state_table_local", "sync_state", ["table_name", "local_id"])

    op.create_table(
        "sync_conflicts",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("table_name", sa.String(length=100), nullable=False),
        sa.Column("local_id", sa.Integer, nullable=False),
        sa.Column("local_version", sa.Integer, nullable=False),
        sa.Column("remote_version", sa.Integer, nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("remote_snapshot", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sync_conflict_table_local", "sync_conflicts", ["table_name", "local_id"])
    op.create_index("idx_sync_conflict_direction", "sync_conflicts", ["direction"])
    op.create_index("idx_sync_conflict_created", "sync_conflicts", ["created_at"])

    op.create_table(
        "sync_log",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("operation", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("pushed", sa.Integer, nullable=True),
        sa.Column("pulled", sa.Integer, nullable=True),
        sa.Column("conflicts", sa.Integer, nullable=True),
        sa.Column("errors_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sync_log_op", "sync_log", ["operation"])
    op.create_index("idx_sync_log_created", "sync_log", ["created_at"])


def downgrade():
    op.drop_index("idx_sync_log_created", table_name="sync_log")
    op.drop_index("idx_sync_log_op", table_name="sync_log")
    op.drop_table("sync_log")

    op.drop_index("idx_sync_conflict_created", table_name="sync_conflicts")
    op.drop_index("idx_sync_conflict_direction", table_name="sync_conflicts")
    op.drop_index("idx_sync_conflict_table_local", table_name="sync_conflicts")
    op.drop_table("sync_conflicts")

    op.drop_index("idx_sync_state_table_local", table_name="sync_state")
    op.drop_index("idx_sync_state_synced", table_name="sync_state")
    op.drop_table("sync_state")
