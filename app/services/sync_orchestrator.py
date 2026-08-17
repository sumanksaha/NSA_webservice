"""Sync orchestration layer — replaces 7× duplicated triple-sync try/except blocks.

Single entry point for syncing a row dict to all enabled parallel targets:
Google Sheets (primary), Airtable (redundant), Excel Online (dormant).

Usage (replaces the triple try/except in every call site)::

    from app.services.sync_orchestrator import sync_row
    result = sync_row("sample_repo", row_dict, entity_id=sample.id)
    if result["sheets"]:     # primary target succeeded
        sample.synced_at = datetime.now(UTC)
        db.session.commit()

Each target is attempted independently — a failure in one never blocks
the others. Returns per-target success flags so callers can react
(e.g. update ``synced_at`` on Sheets success).
"""

from __future__ import annotations

import logging
from typing import TypedDict

logger = logging.getLogger(__name__)


class SyncResult(TypedDict):
    """Per-target sync success flags returned by :func:`sync_row`."""

    sheets: bool
    airtable: bool
    excel: bool


def sync_row(module: str, row_dict: dict, entity_id: int | None = None) -> SyncResult:
    """Sync *row_dict* to all enabled parallel sync targets.

    Args:
        module: Canonical module key (e.g. ``"sample_repo"``,
            ``"food_cell_do_intimations"``).
        row_dict: Field names and values to sync.
        entity_id: Optional DB record ID (passed to Airtable for tracking).

    Returns:
        ``SyncResult`` with per-target success flags.

    Each target is independently guarded — ``sync_to_sheets``,
    ``sync_to_airtable``, and ``sync_to_excel`` are lazy-imported so the
    orchestrator works even when optional dependencies (``gspread``,
    ``pyairtable``, ``msal``) are absent.
    """
    results: SyncResult = {"sheets": False, "airtable": False, "excel": False}

    # Sheets (primary target)
    try:
        from app.services.sheets_sync import sync_to_sheets

        results["sheets"] = sync_to_sheets(module, row_dict)
    except Exception as e:
        logger.warning("Sheets sync failed [%s]: %s", module, e)

    # Airtable (redundant target)
    try:
        from app.services.airtable_sync import sync_to_airtable

        results["airtable"] = sync_to_airtable(module, row_dict, entity_id)
    except Exception as e:
        logger.warning("Airtable sync failed [%s]: %s", module, e)

    # Excel Online (dormant — ENABLE_EXCEL_SYNC=false)
    try:
        from app.services.excel_sync import sync_to_excel

        results["excel"] = sync_to_excel(module, row_dict, entity_id)
    except Exception as e:
        logger.warning("Excel sync failed [%s]: %s", module, e)

    return results


__all__ = ["SyncResult", "sync_row"]
