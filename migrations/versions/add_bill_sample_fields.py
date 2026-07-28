"""add bill sample fields and billed flag

Adds:
- Sample.billed column
- Bill.enforcement_price, Bill.surveillance_price, Bill.start_date, Bill.end_date
- BillSample junction table

Revision ID: add_bill_sample_fields
Revises: add_fso_sample_inspection_tables
Create Date: 2026-07-19 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_bill_sample_fields"
down_revision = "add_fso_sample_inspection_tables"
branch_labels = None
depends_on = None


def upgrade():
    # Check if bill_sample table already exists (from db.create_all with backref)
    # If it does, we need to handle it differently
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    tables = inspector.get_table_names()

    has_bill_sample = "bill_sample" in tables
    has_billed_col = "billed" in [col["name"] for col in inspector.get_columns("sample")]
    has_enforcement_price = "enforcement_price" in [col["name"] for col in inspector.get_columns("bills")]

    # Add billed column to sample table (only if doesn't exist)
    if not has_billed_col:
        op.add_column("sample", sa.Column("billed", sa.Boolean(), nullable=True, server_default="false"))

    # Add new columns to bills table (only if don't exist)
    if not has_enforcement_price:
        op.add_column("bills", sa.Column("enforcement_price", sa.Float(), nullable=False, server_default="0.0"))
        op.add_column("bills", sa.Column("surveillance_price", sa.Float(), nullable=False, server_default="0.0"))
        op.add_column("bills", sa.Column("start_date", sa.String(length=100), nullable=True))
        op.add_column("bills", sa.Column("end_date", sa.String(length=100), nullable=True))

    # Create bill_sample junction table (only if doesn't exist)
    if not has_bill_sample:
        op.create_table(
            "bill_sample",
            sa.Column("bill_id", sa.Integer(), nullable=False),
            sa.Column("sample_id", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("bill_id", "sample_id"),
            sa.ForeignKeyConstraint(
                ["bill_id"],
                ["bills.id"],
            ),
            sa.ForeignKeyConstraint(
                ["sample_id"],
                ["sample.id"],
            ),
        )

    # Create indexes for performance (only if don't exist)
    try:
        op.create_index("idx_sample_billed", "sample", ["billed"], unique=False)
    except Exception:
        pass  # Index may already exist

    # Alter sample_type column to be non-nullable with CHECK constraint
    # SQLite doesn't support ALTER TABLE ADD CONSTRAINT, so we use batch_alter_table
    with op.batch_alter_table("sample", schema=None) as batch_op:
        batch_op.alter_column("sample_type", existing_type=sa.String(length=100), nullable=False)
        batch_op.create_check_constraint("ck_sample_type", sa.text("sample_type IN ('enforcement', 'surveillance')"))


def downgrade():
    # Drop check constraint and revert sample_type to nullable
    with op.batch_alter_table("sample", schema=None) as batch_op:
        batch_op.drop_constraint("ck_sample_type", type_="check")
        batch_op.alter_column("sample_type", existing_type=sa.String(length=100), nullable=True)

    # Drop indexes first
    op.drop_index("idx_sample_billed", table_name="sample")

    # Drop bill_sample table
    op.drop_table("bill_sample")

    # Drop added columns from bills
    op.drop_column("bills", "end_date")
    op.drop_column("bills", "start_date")
    op.drop_column("bills", "surveillance_price")
    op.drop_column("bills", "enforcement_price")

    # Drop billed column from sample
    op.drop_column("sample", "billed")
