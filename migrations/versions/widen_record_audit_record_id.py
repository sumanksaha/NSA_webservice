"""widen record_audit.record_id to VARCHAR(255)

``WorkDiaryEntry`` is keyed by the composite
``(fso_name, work_date, duty_seq)``; the audit hook renders that into a
``record_id`` like ``fso_name=Suman Saha|work_date=2026-10-01|duty_seq=1``
(51 chars, worst case ~142). The column was created as VARCHAR(50), so on
Postgres the audit insert raised ``StringDataRightTruncation`` and — because
the audit row shares the diary save's transaction — rolled back the
officer's legitimate edits, surfacing as a 500 on ``POST /diary/bulk``
(observed 2026-10-13).

Widens ``record_id`` to VARCHAR(255): enough for the worst-case identifier
with headroom. Non-destructive — existing shorter rows are unaffected. The
other audited models (Adjudication, Bill, CaseFile) use short integer keys,
so they need no more than the old width but are unaffected by the wider
column.

Dev environments create tables via the startup ``db.create_all()`` fallback,
so the migration matters mainly for Postgres.

NOTE: the revision id must stay within 32 chars — production's
``alembic_version.version_num`` is VARCHAR(32) and a longer id aborts the
deploy with StringDataRightTruncation on the version stamp.

Revision ID: widen_record_audit_record_id
Revises: add_note_answer_table
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa


revision = "widen_record_audit_record_id"
down_revision = "add_note_answer_table"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("record_audit", schema=None) as batch_op:
        batch_op.alter_column(
            "record_id",
            existing_type=sa.String(length=50),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade():
    # Best-effort: fails loudly if any row now exceeds 50 chars — you cannot
    # safely narrow a column that has grown (backfilling a truncated
    # identifier would falsify the audit trail).
    with op.batch_alter_table("record_audit", schema=None) as batch_op:
        batch_op.alter_column(
            "record_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=50),
            existing_nullable=False,
        )
