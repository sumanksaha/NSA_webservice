"""add timeline_event table

Phase 13 (task.md / plan.md §3.4): auto-generated milestone events for a case.
Populated by ``app/timeline/engine.py`` from date fields across CaseFile,
Inspection, Sample, and Adjudication records; rendered by the Gantt/timeline UI.

Revision ID: add_timeline_event_table
Revises: add_ocr_pipeline_models
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa


revision = "add_timeline_event_table"
down_revision = "add_ocr_pipeline_models"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "timeline_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("case_type", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("document_ref", sa.String(length=256), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["case_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_timeline_event_case_id", "timeline_event", ["case_id"], unique=False)
    op.create_index("idx_timeline_event_timestamp", "timeline_event", ["timestamp"], unique=False)
    op.create_index(
        "idx_timeline_case_ts", "timeline_event", ["case_id", "timestamp"], unique=False
    )
    op.create_index(
        "idx_timeline_event_type", "timeline_event", ["case_type", "event_type"], unique=False
    )


def downgrade():
    op.drop_index("idx_timeline_event_type", table_name="timeline_event")
    op.drop_index("idx_timeline_case_ts", table_name="timeline_event")
    op.drop_index("idx_timeline_event_timestamp", table_name="timeline_event")
    op.drop_index("idx_timeline_event_case_id", table_name="timeline_event")
    op.drop_table("timeline_event")
