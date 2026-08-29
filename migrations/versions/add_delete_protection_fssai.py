"""Add DELETE protection trigger to FSSAI license/registration tables.

This migration creates a database-level trigger that blocks all DELETE operations
on the fssai_licenses and fssai_registrations tables. This ensures the lookup
data can never be accidentally or maliciously deleted from the production database.

Only TRUNCATE bypasses triggers, which is acceptable as it's a more obvious
intentional operation that requires superuser access.

Revision ID: add_delete_protection_fssai
Revises: <previous_migration>
Create Date: 2026-08-29
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "add_delete_protection_fssai"
down_revision = None  # Update to the previous migration revision
branch_labels = None
depends_on = None


def upgrade():
    """Create prevent_delete triggers on FSSAI lookup tables."""
    # Create a function that raises an exception on DELETE
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_fssai_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'DELETE operations are not allowed on the % table. '
                'This table contains protected FSSAI lookup data. '
                'Use UPDATE to modify records or contact the database administrator.',
                TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Apply trigger to fssai_licenses
    op.execute(
        """
        CREATE TRIGGER fssai_licenses_no_delete
        BEFORE DELETE ON fssai_licenses
        FOR EACH ROW
        EXECUTE FUNCTION prevent_fssai_delete();
        """
    )

    # Apply trigger to fssai_registrations
    op.execute(
        """
        CREATE TRIGGER fssai_registrations_no_delete
        BEFORE DELETE ON fssai_registrations
        FOR EACH ROW
        EXECUTE FUNCTION prevent_fssai_delete();
        """
    )

    # Document the protection
    op.execute(
        """
        COMMENT ON TRIGGER fssai_licenses_no_delete ON fssai_licenses IS
        'Protects FSSAI license lookup data from deletion. Added 2026-08-29.';
        """
    )
    op.execute(
        """
        COMMENT ON TRIGGER fssai_registrations_no_delete ON fssai_registrations IS
        'Protects FSSAI registration lookup data from deletion. Added 2026-08-29.';
        """
    )


def downgrade():
    """Remove the DELETE protection triggers."""
    op.execute("DROP TRIGGER IF EXISTS fssai_licenses_no_delete ON fssai_licenses;")
    op.execute("DROP TRIGGER IF EXISTS fssai_registrations_no_delete ON fssai_registrations;")
    op.execute("DROP FUNCTION IF EXISTS prevent_fssai_delete();")