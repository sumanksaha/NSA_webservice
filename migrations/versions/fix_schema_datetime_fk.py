"""Fix schema issues: date columns to DateTime, PhotoEvidence FK type, code_sequence table

Revision ID: fix_schema_datetime_fk
Revises: add_fso_sample_inspection_tables
Create Date: 2026-07-24 07:54:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = 'fix_schema_datetime_fk'
down_revision = 'add_fso_sample_inspection_tables'
branch_labels = None
depends_on = None


# Helper: parse a date string to a datetime object for data migration
def _parse_date_str(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def upgrade():
    # ========================================================================
    # 1. Create code_sequence table (for race-safe code generation)
    # ========================================================================
    op.create_table('code_sequence',
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('last_value', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('key')
    )

    # ========================================================================
    # 2. Fix PhotoEvidence.case_id type: String -> Integer
    # ========================================================================
    # SQLite requires batch mode for column type changes
    with op.batch_alter_table('photo_evidence', schema=None) as batch_op:
        batch_op.alter_column('case_id',
            existing_type=sa.String(),
            type_=sa.Integer(),
            existing_nullable=True)

    # ========================================================================
    # 3. Fix FboIssue.created_at / updated_at type: String -> DateTime
    #    and add new columns (reg_lat, reg_lng, geocoded_at)
    # ========================================================================
    # NOTE: add_column calls must be done OUTSIDE batch_alter_table on
    # SQLite to avoid a CircularDependencyError in Alembic's topological
    # sort when multiple nullable columns are added in one batch.
    with op.batch_alter_table('fbo_issue', schema=None) as batch_op:
        batch_op.alter_column('created_at',
            existing_type=sa.String(),
            type_=sa.DateTime(),
            existing_nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'))
        batch_op.alter_column('updated_at',
            existing_type=sa.String(),
            type_=sa.DateTime(),
            existing_nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'))
    op.add_column('fbo_issue', sa.Column('reg_lat', sa.Float(), nullable=True))
    op.add_column('fbo_issue', sa.Column('reg_lng', sa.Float(), nullable=True))
    op.add_column('fbo_issue', sa.Column('geocoded_at', sa.DateTime(), nullable=True))

    # ========================================================================
    # 4. Fix FboIssueAudit.asserted_at type: String -> DateTime
    # ========================================================================
    with op.batch_alter_table('fbo_issue_audit', schema=None) as batch_op:
        batch_op.alter_column('asserted_at',
            existing_type=sa.String(),
            type_=sa.DateTime(),
            existing_nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'))

    # ========================================================================
    # 4. Convert date columns from String(100) to DateTime
    #    Tables: sample, inspection, case_files, adjudications, bills
    # ========================================================================
    conn = op.get_bind()

    # --- sample table ---
    with op.batch_alter_table('sample', schema=None) as batch_op:
        batch_op.alter_column('collection_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=False)
        batch_op.alter_column('submission_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=True)

    # --- inspection table ---
    with op.batch_alter_table('inspection', schema=None) as batch_op:
        batch_op.alter_column('inspection_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=False)
        batch_op.alter_column('compliance_deadline',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=False)

    # --- case_files table ---
    case_file_date_cols = [
        'authorization_date', 'inspection_date', 'mfg_date', 'expiry_date',
        'sample_submission_date', 'do_receipt_date', 'analyst_report_date',
        'directive_letter_date', 'retailer_report_receive_date',
        'manufacturer_report_receive_date',
    ]
    with op.batch_alter_table('case_files', schema=None) as batch_op:
        for col in case_file_date_cols:
            batch_op.alter_column(col,
                existing_type=sa.String(length=100),
                type_=sa.DateTime(),
                existing_nullable=False)

    # --- adjudications table ---
    with op.batch_alter_table('adjudications', schema=None) as batch_op:
        batch_op.alter_column('First_inspection_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=False)
        batch_op.alter_column('compliance_deadline',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=False)
        batch_op.alter_column('Complaint_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=True)
        batch_op.alter_column('inspection_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=False)
        batch_op.alter_column('authorization_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=True)

    # --- bills table ---
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.alter_column('TR_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=False)
        batch_op.alter_column('Submission_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=False)
        batch_op.alter_column('start_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=True)
        batch_op.alter_column('end_date',
            existing_type=sa.String(length=100),
            type_=sa.DateTime(),
            existing_nullable=True)


def downgrade():
    # Reverse the date column changes
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.alter_column('end_date', type_=sa.String(length=100), existing_nullable=True)
        batch_op.alter_column('start_date', type_=sa.String(length=100), existing_nullable=True)
        batch_op.alter_column('Submission_date', type_=sa.String(length=100), existing_nullable=False)
        batch_op.alter_column('TR_date', type_=sa.String(length=100), existing_nullable=False)

    with op.batch_alter_table('adjudications', schema=None) as batch_op:
        batch_op.alter_column('authorization_date', type_=sa.String(length=100), existing_nullable=True)
        batch_op.alter_column('inspection_date', type_=sa.String(length=100), existing_nullable=False)
        batch_op.alter_column('Complaint_date', type_=sa.String(length=100), existing_nullable=True)
        batch_op.alter_column('compliance_deadline', type_=sa.String(length=100), existing_nullable=False)
        batch_op.alter_column('First_inspection_date', type_=sa.String(length=100), existing_nullable=False)

    with op.batch_alter_table('case_files', schema=None) as batch_op:
        for col in reversed([
            'manufacturer_report_receive_date', 'retailer_report_receive_date',
            'directive_letter_date', 'analyst_report_date', 'do_receipt_date',
            'sample_submission_date', 'expiry_date', 'mfg_date',
            'inspection_date', 'authorization_date',
        ]):
            batch_op.alter_column(col, type_=sa.String(length=100), existing_nullable=False)

    with op.batch_alter_table('inspection', schema=None) as batch_op:
        batch_op.alter_column('compliance_deadline', type_=sa.String(length=100), existing_nullable=False)
        batch_op.alter_column('inspection_date', type_=sa.String(length=100), existing_nullable=False)

    with op.batch_alter_table('sample', schema=None) as batch_op:
        batch_op.alter_column('submission_date', type_=sa.String(length=100), existing_nullable=True)
        batch_op.alter_column('collection_date', type_=sa.String(length=100), existing_nullable=False)

    # Reverse FboIssueAudit.asserted_at
    with op.batch_alter_table('fbo_issue_audit', schema=None) as batch_op:
        batch_op.alter_column('asserted_at', type_=sa.String(), existing_nullable=False)

    # Reverse PhotoEvidence.case_id
    with op.batch_alter_table('photo_evidence', schema=None) as batch_op:
        batch_op.alter_column('case_id', type_=sa.String(), existing_nullable=True)

    # Drop code_sequence table
    op.drop_table('code_sequence')
