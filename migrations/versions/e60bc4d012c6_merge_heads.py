"""merge heads

Revision ID: e60bc4d012c6
Revises: add_bill_sample_fields, fix_schema_datetime_fk
Create Date: 2026-07-25 07:12:19.068656

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e60bc4d012c6"
down_revision = ("add_bill_sample_fields", "fix_schema_datetime_fk")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
