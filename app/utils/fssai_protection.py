"""Verification that FSSAI lookup data is protected from deletion.

This module provides functions to verify that the delete-protection triggers
are active on the fssai_licenses and fssai_registrations tables.
"""

from sqlalchemy import text

from app.extensions import db


def verify_delete_protection() -> dict:
    """Verify DELETE protection triggers exist on FSSAI lookup tables.

    Returns:
        dict with 'protected' bool and 'details' list of table statuses
    """
    tables = ["fssai_licenses", "fssai_registrations"]
    results = {"protected": True, "details": []}

    for table in tables:
        try:
            with db.engine.connect() as conn:
                # Check if trigger exists.
                trigger_sql = """
                    SELECT 1 FROM pg_trigger t
                    JOIN pg_class c ON t.tgrelid = c.oid
                    WHERE c.relname = :table
                    AND t.tgname LIKE '%no_delete%'
                    AND t.tgenabled = 'O'
                """
                trigger_result = conn.execute(text(trigger_sql), {"table": table}).fetchone()

                if trigger_result:
                    results["details"].append({"table": table, "protected": True, "trigger": f"{table}_no_delete"})
                else:
                    results["protected"] = False
                    results["details"].append({
                        "table": table,
                        "protected": False,
                        "error": "No delete protection trigger found",
                    })
        except Exception as e:
            results["protected"] = False
            results["details"].append({"table": table, "protected": False, "error": str(e)})

    return results


def count_fssai_records() -> dict:
    """Count records in FSSAI lookup tables.

    Returns:
        dict with counts per table
    """
    counts = {}
    tables = ["fssai_licenses", "fssai_registrations"]

    with db.engine.connect() as conn:
        for table in tables:
            # Table names come from the internal allowlist above, never user input.
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
            counts[table] = count

    return counts
