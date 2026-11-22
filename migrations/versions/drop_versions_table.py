"""drop_versions_table

Removes the ``versions`` table and its indexes. The document viewer/editor and
version-control features were removed (ADR-0018); no code reads or writes
version snapshots any more.

To make the drop reversible, existing rows are first copied to
``versions_archive`` (same schema, no indexes). The downgrade recreates the
original table and restores any archived rows, so a rollback after a production
deploy recovers the version history instead of leaving an empty table.

Revision ID: drop_versions_table
Revises: 20261031_inspection_notes
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa

revision = "drop_versions_table"
down_revision = "20261031_inspection_notes"
branch_labels = None
depends_on = None

_VERSION_INDEXES = [
    "uq_version_case_doc",
    "uq_version_case_doc_branch",
    "uq_version_adjudication_doc",
    "uq_version_adjudication_doc_branch",
    "idx_version_case_id",
    "idx_version_adjudication_id",
    "idx_version_content_hash",
    "idx_version_created_at",
    "idx_version_user_id",
    "idx_version_branch_of",
]


def _table_exists(bind: sa.engine.Connection, table_name: str) -> bool:
    return table_name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    for index_name in _VERSION_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    if _table_exists(bind, "versions"):
        # Archive rows before dropping so a rollback can restore them.
        op.execute("DROP TABLE IF EXISTS versions_archive")
        op.execute("CREATE TABLE versions_archive AS SELECT * FROM versions")
        op.execute("DROP TABLE IF EXISTS versions")
    else:
        # Fresh DB with no versions table — just make sure no stale archive lingers.
        op.execute("DROP TABLE IF EXISTS versions_archive")


def downgrade() -> None:
    op.create_table(
        "versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("case_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "adjudication_id",
            sa.Integer(),
            sa.ForeignKey("adjudications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("doc_type", sa.String(length=20), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("html_snapshot", sa.Text(), nullable=False),
        sa.Column("delta", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("branch_name", sa.String(length=100), nullable=True),
        sa.Column(
            "branch_of",
            sa.Integer(),
            sa.ForeignKey("versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_version_case_doc",
        "versions",
        ["case_id", "doc_type", "version_number"],
        unique=True,
        sqlite_where=sa.text("branch_name IS NULL"),
        postgresql_where=sa.text("branch_name IS NULL"),
    )
    op.create_index(
        "uq_version_case_doc_branch",
        "versions",
        ["case_id", "doc_type", "version_number", "branch_name"],
        unique=True,
        sqlite_where=sa.text("branch_name IS NOT NULL"),
        postgresql_where=sa.text("branch_name IS NOT NULL"),
    )
    op.create_index(
        "uq_version_adjudication_doc",
        "versions",
        ["adjudication_id", "doc_type", "version_number"],
        unique=True,
        sqlite_where=sa.text("branch_name IS NULL"),
        postgresql_where=sa.text("branch_name IS NULL"),
    )
    op.create_index(
        "uq_version_adjudication_doc_branch",
        "versions",
        ["adjudication_id", "doc_type", "version_number", "branch_name"],
        unique=True,
        sqlite_where=sa.text("branch_name IS NOT NULL"),
        postgresql_where=sa.text("branch_name IS NOT NULL"),
    )
    op.create_index("idx_version_case_id", "versions", ["case_id"])
    op.create_index("idx_version_adjudication_id", "versions", ["adjudication_id"])
    op.create_index("idx_version_content_hash", "versions", ["content_hash"])
    op.create_index("idx_version_created_at", "versions", ["created_at"])
    op.create_index("idx_version_user_id", "versions", ["user_id"])
    op.create_index("idx_version_branch_of", "versions", ["branch_of"])

    # Restore archived rows if the upgrade ran on a populated database.
    # Use an explicit column list so a column-order mismatch in the archive
    # (e.g. from the older raw-SQL create migration) cannot silently misalign data.
    bind = op.get_bind()
    if _table_exists(bind, "versions_archive"):
        op.execute(
            "INSERT INTO versions ("
            "id, case_id, adjudication_id, doc_type, version_number, "
            "content_hash, html_snapshot, delta, created_at, user_id, "
            "change_summary, branch_name, branch_of"
            ") SELECT "
            "id, case_id, adjudication_id, doc_type, version_number, "
            "content_hash, html_snapshot, delta, created_at, user_id, "
            "change_summary, branch_name, branch_of "
            "FROM versions_archive"
        )
        op.execute("DROP TABLE IF EXISTS versions_archive")