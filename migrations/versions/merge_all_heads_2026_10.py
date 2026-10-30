"""merge all migration heads (2026-10)

Revision ID: merge_all_heads_2026_10
Revises: ('add_evidence_assessment', 'add_inspection_fbo_type', '20261028_insp_notice_timer', 'add_inspection_provisional_id', '20261028_insp_sample_link', '20261028_adj_post_fields')
Create Date: 2026-10-28

Merge migration to combine all open heads into a single head so that
``flask db upgrade`` works without naming a target revision.
"""

from alembic import op

revision = "merge_all_heads_2026_10"
down_revision = (
    "add_evidence_assessment",
    "add_inspection_fbo_type",
    "20261028_insp_notice_timer",
    "add_inspection_provisional_id",
    "20261028_insp_sample_link",
    "20261028_adj_post_fields",
)
branch_labels = None
depends_on = None


def upgrade():
    pass  # merge only — no schema change


def downgrade():
    pass
