"""add inspection service date reinspection escalation fields

Revision ID: add_inspection_notice_service_timer_fields
Revises: unify_photo_evidence
Create Date: 2026-10-25
"""

from alembic import op
import sqlalchemy as sa

revision = "add_inspection_notice_service_timer_fields"
down_revision = "unify_photo_evidence"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inspection") as batch_op:
        batch_op.add_column(sa.Column("improvement_notice_served_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("reinspection_due_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("reinspection_of_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("shortcomings_persist", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("reinspection_seq", sa.Integer(), nullable=True, server_default="1"))
        batch_op.add_column(sa.Column("shortcomings_json", sa.Text(), nullable=True))

    with op.batch_alter_table("adjudications") as batch_op:
        batch_op.add_column(sa.Column("inspection_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_adjudication_inspection", "inspection",
                                    ["inspection_id"], ["id"], ondelete="SET NULL")


def downgrade():
    op.drop_constraint("fk_adjudication_inspection", "adjudications", type_="foreignkey")
    op.drop_column("adjudications", "inspection_id")
    op.drop_column("inspection", "shortcomings_json")
    op.drop_column("inspection", "reinspection_seq")
    op.drop_column("inspection", "shortcomings_persist")
    op.drop_column("inspection", "reinspection_of_id")
    op.drop_column("inspection", "reinspection_due_at")
    op.drop_column("inspection", "improvement_notice_served_at")
