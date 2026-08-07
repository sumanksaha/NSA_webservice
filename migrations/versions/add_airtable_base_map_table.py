"""add airtable_base_map table

Priority 7 (Multi-Target Sheets Redundancy - Airtable):
Tracks which local DB record maps to which Airtable record + base.

Revision ID: add_airtable_base_map
Revises: add_food_cell_do_intimation
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone


revision = "add_airtable_base_map"
down_revision = "add_food_cell_do_intimation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "airtable_base_map",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("record_id", sa.Integer, nullable=False, index=True),
        sa.Column("module", sa.String(64), nullable=False, index=True),
        sa.Column("airtable_record_id", sa.String(256), nullable=True),
        sa.Column("airtable_base_id", sa.String(256), nullable=True),
        sa.Column("airtable_table_name", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_airtable_map_record_id", "airtable_base_map", ["record_id"])
    op.create_index("idx_airtable_map_module", "airtable_base_map", ["module"])
    op.create_index("idx_airtable_map_base_id", "airtable_base_map", ["airtable_base_id"])


def downgrade():
    op.drop_index("idx_airtable_map_base_id", table_name="airtable_base_map")
    op.drop_index("idx_airtable_map_module", table_name="airtable_base_map")
    op.drop_index("idx_airtable_map_record_id", table_name="airtable_base_map")
    op.drop_table("airtable_base_map")
