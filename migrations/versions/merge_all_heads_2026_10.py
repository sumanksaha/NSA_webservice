"""merge all migration heads (2026-10)

Revision ID: merge_all_heads_2026_10
Revises: ('add_evidence_assessment', 'add_inspection_fbo_type', 'add_inspection_notice_service_timer_fields', 'add_inspection_provisional_id', 'add_inspection_sample_link_and_status', 'add_post_processing_fields_to_adjudication')
Create Date: 2026-10-28

Merge migration to combine all open heads into a single head so that
``flask db upgrade`` works without naming a target revision.
"""

from alembic import op

revision = "merge_all_heads_2026_10"
down_revision = (
    "add_evidence_assessment",
    "add_inspection_fbo_type",
    "add_inspection_notice_service_timer_fields",
    "add_inspection_provisional_id",
    "add_inspection_sample_link_and_status",
    "add_post_processing_fields_to_adjudication",
)
branch_labels = None
depends_on = None


def upgrade():
    pass  # merge only — no schema change


def downgrade():
    pass
