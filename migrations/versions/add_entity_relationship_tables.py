"""add entity and relationship tables

Phase 14 (plan.md §3.4): knowledge-graph node/edge tables for the
Entity/Relationship extraction engine.  Nodes (``entity``) cover cases,
FBOs, inspectors, samples, labs, legal sections, and evidence; directed
edges (``relationship``) carry a typed label + confidence weight.

Revision ID: add_entity_relationship_tables
Revises: a1b2c3d4e5f6
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa


revision = "add_entity_relationship_tables"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "entity",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_table", sa.String(64), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_entity_type", "entity", ["entity_type"], unique=False)
    op.create_index(
        "idx_entity_type_source", "entity", ["entity_type", "source_table", "source_id"], unique=False
    )

    op.create_table(
        "relationship",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("entity.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("entity.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", sa.String(64), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "target_id", "relationship_type", name="uq_relationship_edge"),
    )
    op.create_index("idx_relationship_source_id", "relationship", ["source_id"], unique=False)
    op.create_index("idx_relationship_target_id", "relationship", ["target_id"], unique=False)
    op.create_index("idx_relationship_type", "relationship", ["relationship_type"], unique=False)


def downgrade():
    op.drop_index("idx_relationship_type", table_name="relationship")
    op.drop_index("idx_relationship_target_id", table_name="relationship")
    op.drop_index("idx_relationship_source_id", table_name="relationship")
    op.drop_table("relationship")
    op.drop_index("idx_entity_type_source", table_name="entity")
    op.drop_index("idx_entity_type", table_name="entity")
    op.drop_table("entity")
