"""add evidence_assessment table for probabilistic photo-evidence scoring

Revision ID: add_evidence_assessment
Revises: widen_record_audit_record_id
Create Date: 2026-10-15

``evidence_assessment`` — one append-only probabilistic assessment of a photo
evidence row. A vision model scores the captured premises photo against the
photo-visible subset of the Inspection Checklist and returns a per-item
probability. Records ``provider_model`` vs ``resolved_model`` separately (they
differ for router models such as the default ``typesafe/jev-router``) so a score
is reproducible, plus ``prompt_version`` so a prompt edit surfaces as a new row
rather than unexplained numeric drift.

Advisory only: nothing in this table writes a checklist value. See
``docs/adr/0011-photo-evidence-assessment.md``.

Dev environments create tables via the startup ``db.create_all()`` fallback,
so the migration matters mainly for Postgres.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_evidence_assessment"
down_revision = "widen_record_audit_record_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_assessment",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "evidence_id",
            sa.String(36),
            sa.ForeignKey("evidence.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "adjudication_id",
            sa.Integer(),
            sa.ForeignKey("adjudications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("overall_observable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("suggested_fields", sa.Text(), nullable=True),
        sa.Column("provider_model", sa.String(200), nullable=True),
        sa.Column("resolved_model", sa.String(200), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("token_usage", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_evidence_assessment_evidence_id", "evidence_assessment", ["evidence_id"])
    op.create_index("idx_evidence_assessment_adjudication_id", "evidence_assessment", ["adjudication_id"])


def downgrade() -> None:
    op.drop_index("idx_evidence_assessment_adjudication_id", table_name="evidence_assessment")
    op.drop_index("idx_evidence_assessment_evidence_id", table_name="evidence_assessment")
    op.drop_table("evidence_assessment")
