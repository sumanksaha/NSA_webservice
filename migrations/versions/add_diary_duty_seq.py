"""add duty_seq to work_diary (split-duty day: field + VVIP on one date)

A calendar day holds up to two duty slots (duty_seq 1|2), each an
independent Activity/Place/counts/Notes record. Existing rows become
seq=1; a blank slot means no row.

Revision ID: add_diary_duty_seq
Revises: add_diary_place_of_visit
Create Date: 2026-09-30
"""

from alembic import op
import sqlalchemy as sa

revision = "add_diary_duty_seq"
down_revision = "add_diary_place_of_visit"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "work_diary" not in inspector.get_table_names():
        return
    cols = [c["name"] for c in inspector.get_columns("work_diary")]
    if "duty_seq" in cols:
        return
    # Rebuild the table with the new 3-column PK (works on both
    # PostgreSQL and SQLite, where ALTER of a PK is not supported).
    # NOTE: on PostgreSQL the rebuilt PK constraint keeps the
    # auto-generated name (work_diary_new_pkey); a future migration must
    # reference that name, not work_diary_pkey, when touching the PK.
    op.create_table(
        "work_diary_new",
        sa.Column("fso_name", sa.String(length=100), nullable=False),
        sa.Column("work_date", sa.String(length=10), nullable=False),
        sa.Column("duty_seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("activity", sa.String(length=32), nullable=True),
        sa.Column("premises", sa.Integer(), nullable=True),
        sa.Column("samples", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("place_of_visit", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("fso_name", "work_date", "duty_seq"),
    )
    op.execute(
        "INSERT INTO work_diary_new "
        "(fso_name, work_date, duty_seq, activity, premises, samples, "
        "notes, place_of_visit, summary) "
        "SELECT fso_name, work_date, 1, activity, premises, samples, "
        "notes, place_of_visit, summary FROM work_diary"
    )
    op.drop_table("work_diary")
    op.rename_table("work_diary_new", "work_diary")


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "work_diary" not in inspector.get_table_names():
        return
    cols = [c["name"] for c in inspector.get_columns("work_diary")]
    if "duty_seq" not in cols:
        return
    op.create_table(
        "work_diary_old",
        sa.Column("fso_name", sa.String(length=100), nullable=False),
        sa.Column("work_date", sa.String(length=10), nullable=False),
        sa.Column("activity", sa.String(length=32), nullable=True),
        sa.Column("premises", sa.Integer(), nullable=True),
        sa.Column("samples", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("place_of_visit", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("fso_name", "work_date"),
    )
    # Keep duty 1 on downgrade; duty 2 rows are dropped (they cannot be
    # represented in the old schema).
    op.execute(
        "INSERT INTO work_diary_old "
        "(fso_name, work_date, activity, premises, samples, "
        "notes, place_of_visit, summary) "
        "SELECT fso_name, work_date, activity, premises, samples, "
        "notes, place_of_visit, summary FROM work_diary WHERE duty_seq = 1"
    )
    op.drop_table("work_diary")
    op.rename_table("work_diary_old", "work_diary")
