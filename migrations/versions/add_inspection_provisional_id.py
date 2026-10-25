"""Add provisional_id and captured_at to inspection for offline capture.

Phase 1 offline capture: a locally-captured inspection is assigned a
device-issued provisional id (``PEND-<deviceId>-<ts>-<rand4>``) and a
device ``captured_at`` timestamp at capture time. The server-issued
``inspection_code`` (``INSP-YYYY-####``) remains NULL until sync reconciles
the provisional id into a real id and allocates the code.

Revision ID: add_inspection_provisional_id
Revises: merge_heads_phase17
Create Date: 2026-10-25
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_inspection_provisional_id"
down_revision = "merge_heads_phase17"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "inspection",
        sa.Column("provisional_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "inspection",
        sa.Column("captured_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_inspection_provisional_id",
        "inspection",
        ["provisional_id"],
        unique=False,
    )
    op.alter_column("inspection", "inspection_code", nullable=True)


def downgrade():
    op.alter_column("inspection", "inspection_code", nullable=False)
    op.drop_index("idx_inspection_provisional_id", table_name="inspection")
    op.drop_column("inspection", "captured_at")
    op.drop_column("inspection", "provisional_id")
