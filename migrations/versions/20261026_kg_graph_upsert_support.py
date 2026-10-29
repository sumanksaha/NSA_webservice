"""knowledge-graph upsert columns and Section model

Legal RAG knowledge-graph roadmap (docs/knowledge_graph_implementation_plan.md):

* new ``kg_sections`` table (ORM: ``app.models.Section``) — canonical source of
  section metadata (full text, effective / expiry dates, jurisdiction) used to
  enrich ``section`` KG nodes.
* ``updated_at`` on ``case_files`` and ``adjudications`` — change detection for
  ``KnowledgeGraphEngine.upsert_graph``.
* ``graph_last_modified`` on ``entity``, ``relationship``,
  ``adjudication_entity`` and ``adjudication_relationship`` — stamps when a
  graph row was last persisted so incremental upserts can skip unchanged
  records.

Revision ID: 20261026_kg_graph_upsert_support
Revises: 2be5aee24701
Create Date: 2026-10-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20261026_kg_graph_upsert_support"
down_revision = "2be5aee24701"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kg_sections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("section_number", sa.String(length=32), nullable=False),
        sa.Column("act", sa.String(length=120), nullable=False, server_default="FSS Act, 2006"),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("full_text", sa.Text(), nullable=True),
        sa.Column("effective_date", sa.DateTime(), nullable=True),
        sa.Column("expiry_date", sa.DateTime(), nullable=True),
        sa.Column("jurisdiction", sa.String(length=80), nullable=True),
        sa.Column("source_table", sa.String(length=64), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kg_sections_section_number", "kg_sections", ["section_number"], unique=True)
    op.create_index("idx_kg_sections_number_act", "kg_sections", ["section_number", "act"], unique=False)

    with op.batch_alter_table("case_files") as batch_op:
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("adjudications") as batch_op:
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))

    for table in ("entity", "relationship", "adjudication_entity", "adjudication_relationship"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("graph_last_modified", sa.DateTime(), nullable=True))


def downgrade() -> None:
    for table in ("adjudication_relationship", "adjudication_entity", "relationship", "entity"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("graph_last_modified")

    with op.batch_alter_table("adjudications") as batch_op:
        batch_op.drop_column("updated_at")

    with op.batch_alter_table("case_files") as batch_op:
        batch_op.drop_column("updated_at")

    op.drop_index("idx_kg_sections_number_act", table_name="kg_sections")
    op.drop_index("ix_kg_sections_section_number", table_name="kg_sections")
    op.drop_table("kg_sections")
