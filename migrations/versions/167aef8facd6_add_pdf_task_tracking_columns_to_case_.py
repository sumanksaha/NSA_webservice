"""add pdf task tracking columns to case_files and bills

Revision ID: 167aef8facd6
Revises: 76096260c92a
Create Date: 2026-07-26 08:37:55.672598

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '167aef8facd6'
down_revision = '76096260c92a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('case_files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pdf_task_id', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('pdf_generated_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pdf_task_id', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('pdf_generated_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.drop_column('pdf_generated_at')
        batch_op.drop_column('pdf_task_id')
    with op.batch_alter_table('case_files', schema=None) as batch_op:
        batch_op.drop_column('pdf_generated_at')
        batch_op.drop_column('pdf_task_id')
