"""merge inspection + user_fso_name migration branches

Revision ID: merge_inspection_heads
Revises: ('add_user_fso_name', 'add_inspection_checklist_notice')
Create Date: 2026-08-27

Both branches grew from ``add_inspection_visit_purpose``; this merge makes a
single head again so ``flask db upgrade`` works without naming a target.
"""

from alembic import op

revision = "merge_inspection_heads"
down_revision = ("add_user_fso_name", "add_inspection_checklist_notice")
branch_labels = None
depends_on = None


def upgrade():
    pass  # merge only — no schema change


def downgrade():
    pass
