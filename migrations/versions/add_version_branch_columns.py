"""add_version_branch_columns

Adds branch/draft support to the ``versions`` table (Phase 9):

- ``branch_name``  -- NULL on the mainline, free-form label on branch versions
- ``branch_of``    -- FK to the source version the branch was forked from

The unique indexes on (case, doc_type, version_number) are extended to include
``branch_name`` so a branch may restart numbering at 1 without colliding with
the mainline. (SQLite/PostgreSQL treat NULLs as distinct in UNIQUE indexes, so
mainline rows remain unconstrained by the DB — monotonic numbering is enforced
by VersionService.)

Revision ID: add_version_branch_columns
Revises: unify_photo_evidence
Create Date: 2026-08-04
"""

from alembic import op

revision = "add_version_branch_columns"
down_revision = "unify_photo_evidence"
branch_labels = None
depends_on = None


def upgrade():
    # --- new columns (nullable, so existing rows are untouched) ---
    op.execute("ALTER TABLE versions ADD COLUMN branch_name VARCHAR(100)")
    op.execute(
        "ALTER TABLE versions ADD COLUMN branch_of INTEGER "
        "REFERENCES versions(id) ON DELETE SET NULL"
    )

    # --- re-create unique indexes ---
    # Partial indexes: the mainline (branch_name IS NULL) keeps the original
    # (case, doc_type, version_number) uniqueness, while branches are unique
    # per (case, doc_type, version_number, branch_name). A single index on
    # (..., branch_name) would not enforce mainline uniqueness because NULLs
    # are distinct in SQL UNIQUE indexes.
    op.execute("DROP INDEX IF EXISTS uq_version_case_doc")
    op.execute("DROP INDEX IF EXISTS uq_version_adjudication_doc")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_version_case_doc "
        "ON versions(case_id, doc_type, version_number) "
        "WHERE branch_name IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_version_case_doc_branch "
        "ON versions(case_id, doc_type, version_number, branch_name) "
        "WHERE branch_name IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_version_adjudication_doc "
        "ON versions(adjudication_id, doc_type, version_number) "
        "WHERE branch_name IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_version_adjudication_doc_branch "
        "ON versions(adjudication_id, doc_type, version_number, branch_name) "
        "WHERE branch_name IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_version_branch_of ON versions(branch_of)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_version_branch_of")
    op.execute("DROP INDEX IF EXISTS uq_version_adjudication_doc_branch")
    op.execute("DROP INDEX IF EXISTS uq_version_case_doc_branch")
    op.execute("DROP INDEX IF EXISTS uq_version_adjudication_doc")
    op.execute("DROP INDEX IF EXISTS uq_version_case_doc")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_version_case_doc "
        "ON versions(case_id, doc_type, version_number)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_version_adjudication_doc "
        "ON versions(adjudication_id, doc_type, version_number)"
    )
    op.execute("ALTER TABLE versions DROP COLUMN branch_of")
    op.execute("ALTER TABLE versions DROP COLUMN branch_name")
