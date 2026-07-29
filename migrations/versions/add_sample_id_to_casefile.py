"""Add sample_id FK to case_files table

This migration adds a nullable foreign key from case_files to sample,
enabling CaseFile-Sample linkage (Step 5).

Revision ID: add_sample_id_to_casefile
Revises: 453157859db7
Create Date: 2026-07-17 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_sample_id_to_casefile"
down_revision = "453157859db7"
branch_labels = None
depends_on = None


def upgrade():
    # Add nullable sample_id foreign key to case_files table
    op.add_column("case_files", sa.Column("sample_id", sa.Integer(), nullable=True))

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_case_files_sample_id",
        "case_files",
        "sample",
        ["sample_id"],
        ["id"],
        ondelete="SET NULL",  # If sample is deleted, set casefile.sample_id to NULL
    )

    # Add index for performance on the FK
    op.create_index("idx_case_files_sample_id", "case_files", ["sample_id"])


def downgrade():
    # Drop index first
    op.drop_index("idx_case_files_sample_id", table_name="case_files")

    # Drop foreign key constraint
    op.drop_constraint("fk_case_files_sample_id", "case_files", type_="foreignkey")

    # Drop column
    op.drop_column("case_files", "sample_id")
