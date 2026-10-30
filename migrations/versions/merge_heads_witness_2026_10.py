"""Merge remaining heads after add_witness_name_case_file.

Revision ID: merge_heads_witness_2026_10
Revises: ('add_witness_name_case_file', 'merge_all_heads_2026_10')
Create Date: 2026-10-30

``add_shortcomings_case_file`` branched off ``merge_inspection_heads`` instead
of the 2026-10 merge, leaving two open heads — ``flask db upgrade`` refuses to
run while a second head exists, so no migration could be applied on deploy.
This merge collapses them back to one so the chain is upgradeable again.
"""

from alembic import op

revision = "merge_heads_witness_2026_10"
down_revision = ("add_witness_name_case_file", "merge_all_heads_2026_10")
branch_labels = None
depends_on = None


def upgrade():
    pass  # merge only — no schema change


def downgrade():
    pass
