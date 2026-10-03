"""Add is_unsafe verdict column to case_files.

The sample-adjudication UI gained an "Unsafe" verdict toggle (mirroring
is_misbranded / is_substandard) that gates the Unsafe-File (prohibition order,
Sec 36(3)(b)) download option. A sample may be unsafe independently of — or in
addition to — substandard/misbranded, so this is a distinct column, not folded
into analysis_result.

Idempotent: fresh ``create_all`` databases (tests, new installs) and any
operator who already applied the column by hand must not fail the deploy —
``flask db upgrade`` is a no-op on those, and the boot-time schema check
(``db_bootstrap._verify_required_columns``) is satisfied once the column exists.

Revision ID: add_is_unsafe_to_case_files
Revises: add_diary_duty_seq
Create Date: 2026-10-03
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_is_unsafe_to_case_files"
down_revision = "add_diary_duty_seq"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: skip if the column is already present (e.g. a database that
    # was repaired by hand or created via create_all from an already-migrated
    # model import).
    bind = op.get_bind()
    present = {col["name"] for col in sa.inspect(bind).get_columns("case_files")}
    if "is_unsafe" not in present:
        op.add_column(
            "case_files",
            sa.Column("is_unsafe", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    op.drop_column("case_files", "is_unsafe")
