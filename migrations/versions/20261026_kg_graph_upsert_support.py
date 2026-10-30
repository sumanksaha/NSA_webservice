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


def _existing_tables() -> set[str]:
    """Table names from a FRESH inspector on the migration bind.

    Callers re-invoke this after any CREATE so a stale reflection snapshot
    can never make a just-created table report as missing.
    """
    from sqlalchemy import inspect as sa_inspect

    return set(sa_inspect(op.get_bind()).get_table_names())


def _ensure_adjudication_graph_tables() -> None:
    """Create ``adjudication_entity`` / ``adjudication_relationship`` if absent.

    No earlier revision in this chain creates these two tables — before the
    guard they existed only via the ORM ``db.create_all()`` self-heal in
    ``app/db_bootstrap.py``, so a database whose alembic pointer was below
    this revision crashed ``flask db upgrade`` with
    ``psycopg.errors.UndefinedTable: relation "adjudication_entity" does not
    exist`` (Render deploy 2026-10-29). Schema mirrors
    ``app.models.document.AdjudicationEntity`` / ``AdjudicationRelationship``.
    Already-populated databases (tables arrived via ``create_all``) are
    skipped untouched.
    """
    if "adjudication_entity" not in _existing_tables():
        op.create_table(
            "adjudication_entity",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("entity_type", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("source_table", sa.String(length=64), nullable=True),
            sa.Column("source_id", sa.Integer(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("graph_last_modified", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_adj_entity_type", "adjudication_entity", ["entity_type"], unique=False)

    if "adjudication_relationship" not in _existing_tables():
        op.create_table(
            "adjudication_relationship",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column(
                "source_id",
                sa.Integer(),
                sa.ForeignKey("adjudication_entity.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "target_id",
                sa.Integer(),
                sa.ForeignKey("adjudication_entity.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("relationship_type", sa.String(length=64), nullable=False),
            sa.Column("weight", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("graph_last_modified", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_id", "target_id", "relationship_type", name="uq_adj_relationship_edge"),
        )
        op.create_index(
            "idx_adj_relationship_source_id",
            "adjudication_relationship",
            ["source_id"],
            unique=False,
        )


def _add_graph_last_modified(table: str) -> None:
    """Stamp ``graph_last_modified`` onto ``table`` when both exist.

    ``entity`` / ``relationship`` have their own migrations earlier in the
    chain, but a guard costs one catalog read and keeps a drifted database
    from failing the whole upgrade.
    """
    if table not in _existing_tables():
        return
    columns = {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}
    if "graph_last_modified" in columns:
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(sa.Column("graph_last_modified", sa.DateTime(), nullable=True))


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    """Idempotent ``ADD COLUMN`` — skip when the column is already on disk.

    Same pattern as ``add_post_processing_fields_to_adjudication``: a
    database ``db.create_all()``-booted at head (model columns already
    present) must survive replays from an earlier revision.
    """
    present = {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in present:
        op.add_column(table, column)


def upgrade() -> None:
    if "kg_sections" not in _existing_tables():
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

    _add_column_if_missing("case_files", sa.Column("updated_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("adjudications", sa.Column("updated_at", sa.DateTime(), nullable=True))

    _ensure_adjudication_graph_tables()
    for table in ("adjudication_entity", "adjudication_relationship", "entity", "relationship"):
        _add_graph_last_modified(table)


def downgrade() -> None:
    existing = _existing_tables()
    for table in ("adjudication_relationship", "adjudication_entity", "relationship", "entity"):
        if table in existing:
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column("graph_last_modified")

    with op.batch_alter_table("adjudications") as batch_op:
        batch_op.drop_column("updated_at")

    with op.batch_alter_table("case_files") as batch_op:
        batch_op.drop_column("updated_at")

    op.drop_index("idx_kg_sections_number_act", table_name="kg_sections")
    op.drop_index("ix_kg_sections_section_number", table_name="kg_sections")
    op.drop_table("kg_sections")
