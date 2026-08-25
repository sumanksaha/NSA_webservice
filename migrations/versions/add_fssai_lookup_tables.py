"""add fssai_licenses and fssai_registrations lookup tables

Migrates FSSAI lookup reference data out of the git-tracked SQLite files
db/license_data.db / db/registration_data.db into Postgres (see
docs/FSSAI_LOOKUP_POSTGRES_PLAN.md). Backed by app/models/lookup.py:
FssaiLicense -> fssai_licenses (PK license_no), FssaiRegistration ->
fssai_registrations (PK registration_no). All columns Text to preserve the
byte-exact pass-through contract (expiry_date holds DD-MM-YYYY strings that
are never parsed or compared). No extra indexes: lookups are exact-match
primary-key only.

Revision ID: add_fssai_lookup_tables
Revises: merge_heads_phase17
Create Date: 2026-08-25

"""

import sqlalchemy as sa
from alembic import op


revision = "add_fssai_lookup_tables"
down_revision = "merge_heads_phase17"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fssai_licenses",
        sa.Column("license_no", sa.Text, nullable=False),
        sa.Column("company_name", sa.Text, nullable=True),
        sa.Column("full_address", sa.Text, nullable=True),
        sa.Column("expiry_date", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("license_no"),
    )
    op.create_table(
        "fssai_registrations",
        sa.Column("registration_no", sa.Text, nullable=False),
        sa.Column("company_name", sa.Text, nullable=True),
        sa.Column("full_address", sa.Text, nullable=True),
        sa.Column("expiry_date", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("registration_no"),
    )


def downgrade():
    op.drop_table("fssai_registrations")
    op.drop_table("fssai_licenses")
