"""add OCR pipeline models + Sample OCR fields

Phase A (task.md / plan.md §3.4): foundation for the OCR extraction ->
review -> conflict-resolution -> autopopulation pipeline.

New tables:
  - field_authority       source authority weights (for conflict resolution)
  - ocr_document           raw extraction payload + status + content hash
  - lab_test_parameter     standard vs observed values per extracted parameter
  - ocr_correction         log of manual corrections during review
  - conflict_log            conflicting field values surfaced for review

New columns on ``sample``:
  - nature_of_food, batch_no, mfd, exp, manufacturer_details

Revision ID: add_ocr_pipeline_models
Revises: add_version_branch_columns
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa


revision = "add_ocr_pipeline_models"
down_revision = "add_version_branch_columns"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "field_authority",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source"),
    )

    op.create_table(
        "ocr_document",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("extracted_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["sample_id"], ["sample.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ocr_document_file_hash", "ocr_document", ["file_hash"], unique=False)
    op.create_index("ix_ocr_document_sample_id", "ocr_document", ["sample_id"], unique=False)

    op.create_table(
        "lab_test_parameter",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ocr_document_id", sa.String(length=36), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=True),
        sa.Column("parameter_name", sa.String(length=128), nullable=False),
        sa.Column("standard_value", sa.String(length=256), nullable=True),
        sa.Column("observed_value", sa.String(length=256), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("source_authority", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ocr_document_id"], ["ocr_document.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sample_id"], ["sample.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_labtest_ocr_doc", "lab_test_parameter", ["ocr_document_id"], unique=False)
    op.create_index("ix_lab_test_parameter_sample_id", "lab_test_parameter", ["sample_id"], unique=False)

    op.create_table(
        "ocr_correction",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ocr_document_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ocr_document_id"], ["ocr_document.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_correction_ocr_doc", "ocr_correction", ["ocr_document_id"], unique=False)
    op.create_index("ix_ocr_correction_user_id", "ocr_correction", ["user_id"], unique=False)

    op.create_table(
        "conflict_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ocr_document_id", sa.String(length=36), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=True),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("values_json", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_value", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ocr_document_id"], ["ocr_document.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sample_id"], ["sample.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_conflict_ocr_doc", "conflict_log", ["ocr_document_id"], unique=False)
    op.create_index("ix_conflict_log_sample_id", "conflict_log", ["sample_id"], unique=False)

    # --- Sample OCR autopopulation fields ---
    op.add_column("sample", sa.Column("nature_of_food", sa.String(length=200), nullable=True))
    op.add_column("sample", sa.Column("batch_no", sa.String(length=100), nullable=True))
    op.add_column("sample", sa.Column("mfd", sa.String(length=50), nullable=True))
    op.add_column("sample", sa.Column("exp", sa.String(length=50), nullable=True))
    op.add_column("sample", sa.Column("manufacturer_details", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("sample", "manufacturer_details")
    op.drop_column("sample", "exp")
    op.drop_column("sample", "mfd")
    op.drop_column("sample", "batch_no")
    op.drop_column("sample", "nature_of_food")

    op.drop_index("ix_conflict_log_sample_id", table_name="conflict_log")
    op.drop_index("idx_conflict_ocr_doc", table_name="conflict_log")
    op.drop_table("conflict_log")

    op.drop_index("ix_ocr_correction_user_id", table_name="ocr_correction")
    op.drop_index("idx_correction_ocr_doc", table_name="ocr_correction")
    op.drop_table("ocr_correction")

    op.drop_index("ix_lab_test_parameter_sample_id", table_name="lab_test_parameter")
    op.drop_index("idx_labtest_ocr_doc", table_name="lab_test_parameter")
    op.drop_table("lab_test_parameter")

    op.drop_index("ix_ocr_document_sample_id", table_name="ocr_document")
    op.drop_index("ix_ocr_document_file_hash", table_name="ocr_document")
    op.drop_table("ocr_document")

    op.drop_table("field_authority")
