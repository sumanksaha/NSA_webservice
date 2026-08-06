"""add do_intimation table and food_cell_forwarded column to sample

Phase 21 (Food Cell DO Intimation): tracks DO letter generation, PDF storage,
and Food Cell forwarding status per sample.

Revision ID: add_food_cell_do_intimation
Revises: merge_heads
Create Date: 2026-08-06

"""
from alembic import op
import sqlalchemy as sa


revision = "add_food_cell_do_intimation"
down_revision = "merge_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "do_intimation",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("version_id", sa.Integer, nullable=False, server_default="1"),
        sa.Column("sample_id", sa.Integer, nullable=False, index=True),
        sa.Column("do_reference_no", sa.String(length=100), nullable=False, unique=True, index=True),
        sa.Column("html_path", sa.String(length=512), nullable=True),
        sa.Column("pdf_url", sa.String(length=512), nullable=True),
        sa.Column("food_cell_forwarded", sa.DateTime, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("sync_status", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.ForeignKeyConstraint(["sample_id"], ["sample.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_do_intimation_sample_id", "do_intimation", ["sample_id"])
    op.create_index("idx_do_intimation_status", "do_intimation", ["status"])
    op.create_index("idx_do_intimation_forwarded", "do_intimation", ["food_cell_forwarded"])

    with op.batch_alter_table("sample", schema=None) as batch_op:
        batch_op.add_column(sa.Column("food_cell_forwarded", sa.DateTime, nullable=True, index=True))


def downgrade():
    with op.batch_alter_table("sample", schema=None) as batch_op:
        batch_op.drop_column("food_cell_forwarded")

    op.drop_index("idx_do_intimation_forwarded", table_name="do_intimation")
    op.drop_index("idx_do_intimation_status", table_name="do_intimation")
    op.drop_index("idx_do_intimation_sample_id", table_name="do_intimation")
    op.drop_table("do_intimation")
