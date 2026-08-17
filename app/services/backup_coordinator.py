"""Backup coordinator for Multi-Target Sheets Redundancy (Priority 7).

This module is referenced by ``TASK_REGISTRY`` as
``backup_redundant_sheets`` and executed by the QStash webhook when the
daily schedule fires (or via the admin route / standalone script).

It calls the three parallel-target export functions:
  - ``sheets_sync.export_sheets_to_r2()``
  - ``airtable_sync.export_airtable_all_bases_to_r2()``
  - ``excel_sync.export_excel_to_r2()``

Each export is wrapped in try/except so a failure in one target does
not prevent the others from running.
"""

import logging

logger = logging.getLogger(__name__)


def run_backup() -> dict:
    """Export all three redundant sync targets to R2 (or local fallback).

    Returns a dict with per-target success flags::

        {"sheets": True, "airtable": False, "excel": True, "r2_keys": [...]}
    """
    results = {"sheets": False, "airtable": False, "excel": False, "r2_keys": []}

    # --- Google Sheets ---
    try:
        from app.services.sheets_sync import export_sheets_to_r2

        key = export_sheets_to_r2()
        if key:
            results["sheets"] = True
            results["r2_keys"].append(key)
        else:
            logger.warning("Sheets export returned None (not configured or no data)")
    except Exception as e:
        logger.error("Sheets backup failed: %s", e)

    # --- Airtable ---
    try:
        from app.services.airtable_sync import export_airtable_all_bases_to_r2

        key = export_airtable_all_bases_to_r2()
        if key:
            results["airtable"] = True
            results["r2_keys"].append(key)
        else:
            logger.warning("Airtable export returned None (not configured or no data)")
    except Exception as e:
        logger.error("Airtable backup failed: %s", e)

    # --- Excel Online ---
    try:
        from app.services.excel_sync import export_excel_to_r2

        key = export_excel_to_r2()
        if key:
            results["excel"] = True
            results["r2_keys"].append(key)
        else:
            logger.warning("Excel export returned None (not configured or no data)")
    except Exception as e:
        logger.error("Excel backup failed: %s", e)

    logger.info(
        "Redundant backup complete: sheets=%s airtable=%s excel=%s",
        results["sheets"],
        results["airtable"],
        results["excel"],
    )
    return results
