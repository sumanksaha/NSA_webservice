"""Add per-FSO email/SMTP configuration fields to the fso table.

Revision ID: add_fso_email_config
Revises: add_inspection_sample_collection
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa

revision = "add_fso_email_config"
down_revision = "add_inspection_sample_collection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fso", sa.Column("email", sa.String(200), nullable=True))
    op.add_column("fso", sa.Column("smtp_host", sa.String(200), nullable=True))
    op.add_column("fso", sa.Column("smtp_port", sa.Integer(), nullable=True, server_default="587"))
    op.add_column("fso", sa.Column("smtp_user", sa.String(200), nullable=True))
    op.add_column("fso", sa.Column("smtp_password", sa.String(500), nullable=True))
    op.add_column("fso", sa.Column("smtp_use_tls", sa.Boolean(), nullable=True, server_default=sa.text("true")))


def downgrade() -> None:
    op.drop_column("fso", "smtp_use_tls")
    op.drop_column("fso", "smtp_password")
    op.drop_column("fso", "smtp_user")
    op.drop_column("fso", "smtp_port")
    op.drop_column("fso", "smtp_host")
    op.drop_column("fso", "email")
