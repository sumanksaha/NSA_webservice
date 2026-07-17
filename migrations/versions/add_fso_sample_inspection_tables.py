"""add fso sample inspection tables

Adds the three new tables for the Sample Inspection Pipeline:
- fso: Food Safety Officer master table
- sample: Sample data table with FK to fso
- inspection: Inspection table with FK to fso and adjudication

Revision ID: add_fso_sample_inspection_tables
Revises: add_sample_id_to_casefile
Create Date: 2026-07-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_fso_sample_inspection_tables'
down_revision = 'add_sample_id_to_casefile'
branch_labels = None
depends_on = None


def upgrade():
    # ============================================================================
    # FSO Table (Food Safety Officer master)
    # ============================================================================
    op.create_table('fso',
        sa.Column('fso_name', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('fso_name')
    )
    with op.batch_alter_table('fso', schema=None) as batch_op:
        batch_op.create_index('idx_fso_name', ['fso_name'], unique=False)

    # ============================================================================
    # Sample Table
    # ============================================================================
    op.create_table('sample',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('sample_code', sa.String(length=50), nullable=False),
        sa.Column('sample_name', sa.String(length=200), nullable=False),
        sa.Column('sample_type', sa.String(length=100), nullable=True),
        sa.Column('fso_name', sa.String(length=100), nullable=False),
        sa.Column('collection_date', sa.String(length=100), nullable=False),
        sa.Column('submission_date', sa.String(length=100), nullable=True),
        sa.Column('retailer_fssai', sa.String(length=50), nullable=True),
        sa.Column('retailer_name', sa.String(length=200), nullable=True),
        sa.Column('price', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sample_code')
    )
    with op.batch_alter_table('sample', schema=None) as batch_op:
        batch_op.create_index('idx_sample_code', ['sample_code'], unique=False)
        batch_op.create_index('idx_sample_collection_date', ['collection_date'], unique=False)
        batch_op.create_index('idx_sample_fso_name', ['fso_name'], unique=False)
    
    # Add FK from sample.fso_name to fso.fso_name
    op.create_foreign_key(
        'fk_sample_fso_name',
        'sample', 'fso',
        ['fso_name'], ['fso_name']
    )

    # ============================================================================
    # Inspection Table
    # ============================================================================
    op.create_table('inspection',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('inspection_code', sa.String(length=50), nullable=False),
        sa.Column('fso_name', sa.String(length=100), nullable=False),
        sa.Column('fssai_license', sa.String(length=50), nullable=True),
        sa.Column('ce_license_no', sa.String(length=100), nullable=True),
        sa.Column('fbo_name', sa.String(length=200), nullable=True),
        sa.Column('fbo_address', sa.Text(), nullable=True),
        sa.Column('concerned_food', sa.String(length=200), nullable=True),
        sa.Column('problem', sa.Text(), nullable=True),
        sa.Column('inspection_date', sa.String(length=100), nullable=False),
        sa.Column('compliance_deadline', sa.String(length=100), nullable=False),
        sa.Column('is_dismissed', sa.Boolean(), nullable=True),
        sa.Column('dismissed_by', sa.String(length=100), nullable=True),
        sa.Column('dismissed_at', sa.DateTime(), nullable=True),
        sa.Column('adjudication_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('inspection_code')
    )
    with op.batch_alter_table('inspection', schema=None) as batch_op:
        batch_op.create_index('idx_inspection_code', ['inspection_code'], unique=False)
        batch_op.create_index('idx_inspection_date', ['inspection_date'], unique=False)
        batch_op.create_index('idx_inspection_compliance_deadline', ['compliance_deadline'], unique=False)
        batch_op.create_index('idx_inspection_fso_name', ['fso_name'], unique=False)
    
    # Add FK from inspection.fso_name to fso.fso_name
    op.create_foreign_key(
        'fk_inspection_fso_name',
        'inspection', 'fso',
        ['fso_name'], ['fso_name']
    )


def downgrade():
    # Drop FK constraints first (in reverse order of creation)
    op.drop_constraint('fk_inspection_fso_name', 'inspection', type_='foreignkey')
    op.drop_constraint('fk_sample_fso_name', 'sample', type_='foreignkey')

    # Drop indexes
    with op.batch_alter_table('inspection', schema=None) as batch_op:
        batch_op.drop_index('idx_inspection_fso_name')
        batch_op.drop_index('idx_inspection_compliance_deadline')
        batch_op.drop_index('idx_inspection_date')
        batch_op.drop_index('idx_inspection_code')

    with op.batch_alter_table('sample', schema=None) as batch_op:
        batch_op.drop_index('idx_sample_fso_name')
        batch_op.drop_index('idx_sample_collection_date')
        batch_op.drop_index('idx_sample_code')

    # Drop tables (in reverse order of creation)
    op.drop_table('inspection')
    op.drop_table('sample')
    op.drop_table('fso')
