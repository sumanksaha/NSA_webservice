"""add auditor_plan_json + dossier_verified to inspection

FBO Compliance Auditor Agent (docs/FBO_AUDITOR_AGENT_BLUEPRINT.md):
persists the generated CAPA plan on the inspection record and tracks
FSO verification of the evidentiary dossier on re-inspection.

Revision ID: add_auditor_plan_to_inspection
Revises: add_archive_columns_to_cases
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa


revision = "add_auditor_plan_to_inspection"
down_revision = "add_archive_columns_to_cases"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("inspection", sa.Column("auditor_plan_json", sa.Text(), nullable=True))
    op.add_column(
        "inspection",
        sa.Column("dossier_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade():
    op.drop_column("inspection", "dossier_verified")
    op.drop_column("inspection", "auditor_plan_json")
