"""add users.fso_name link

Revision ID: add_user_fso_name
Revises: add_inspection_visit_purpose
Create Date: 2026-08-26

Phase 18 RBAC: binds each `fso`-role account to exactly one FSO. Nullable —
pure admin accounts have no FSO.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_user_fso_name"
down_revision = "add_inspection_visit_purpose"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fso_name", sa.String(length=100), nullable=True))
        batch_op.create_foreign_key(
            "fk_user_fso_name_fso",
            "fso",
            ["fso_name"],
            ["fso_name"],
        )


def downgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_constraint("fk_user_fso_name_fso", type_="foreignkey")
        batch_op.drop_column("fso_name")
