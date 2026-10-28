"""link sample to inspection + sample_status on sample/adjudication

Revision ID: add_inspection_sample_link_and_status
Revises: add_inspection_sample_collection
Create Date: 2026-10-28

Adds:
- sample.inspection_id (nullable FK → inspection.id, unique when not null)
- sample.sample_status (nullable VARCHAR(20), CHECK ok/substandard/misbranded/unsafe)
- adjudications.sample_id (nullable FK → sample.id)
- index on inspection(fbo_name, inspection_date) for FBO-grouped timeline

All columns nullable so existing rows keep working. Dev environments create
tables via db.create_all() fallback; this migration matters mainly for Postgres.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_inspection_sample_link_and_status"
down_revision = "add_inspection_sample_collection"
branch_labels = None
depends_on = None

_SAMPLE_STATUS_CHECK = "sample_status IN ('ok','substandard','misbranded','unsafe')"


def _column_exists(table, column):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    # sample.inspection_id + sample.sample_status
    if not _column_exists("sample", "inspection_id"):
        with op.batch_alter_table("sample", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("inspection_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_sample_inspection_id",
                "inspection",
                ["inspection_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index(
                "idx_sample_inspection_id", ["inspection_id"], unique=False
            )
            batch_op.create_unique_constraint(
                "uq_sample_inspection_id", ["inspection_id"]
            )
            batch_op.add_column(
                sa.Column("sample_status", sa.String(length=20), nullable=True)
            )
            batch_op.create_check_constraint(
                "ck_sample_status", _SAMPLE_STATUS_CHECK
            )

    # adjudications.sample_id
    if not _column_exists("adjudications", "sample_id"):
        with op.batch_alter_table("adjudications", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("sample_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_adjudications_sample_id",
                "sample",
                ["sample_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index(
                "idx_adjudications_sample_id", ["sample_id"], unique=False
            )

    # index on inspection(fbo_name, inspection_date) if missing
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_idx = {
        (i["name"], tuple(i["column_names"]))
        for i in inspector.get_indexes("inspection")
    }
    if ("idx_inspection_fbo_date", ("fbo_name", "inspection_date")) not in existing_idx:
        with op.batch_alter_table("inspection", schema=None) as batch_op:
            batch_op.create_index(
                "idx_inspection_fbo_date",
                ["fbo_name", "inspection_date"],
                unique=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("inspection", schema=None) as batch_op:
        batch_op.drop_index("idx_inspection_fbo_date", table_name="inspection")

    with op.batch_alter_table("adjudications", schema=None) as batch_op:
        batch_op.drop_index("idx_adjudications_sample_id", table_name="adjudications")
        batch_op.drop_constraint(
            "fk_adjudications_sample_id", type_="foreignkey"
        )
        batch_op.drop_column("sample_id")

    with op.batch_alter_table("sample", schema=None) as batch_op:
        batch_op.drop_constraint("ck_sample_status", type_="check")
        batch_op.drop_constraint("uq_sample_inspection_id", type_="unique")
        batch_op.drop_constraint("fk_sample_inspection_id", type_="foreignkey")
        batch_op.drop_index("idx_sample_inspection_id", table_name="sample")
        batch_op.drop_column("sample_status")
        batch_op.drop_column("inspection_id")
