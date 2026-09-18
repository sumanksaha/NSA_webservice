"""Sync orchestration — Google Sheets only.

This simplifies the sync mechanism by focusing solely on Google Sheets,
removing Airtable and Excel support.

Usage::

    from app.services.sync_orchestrator import sync_row
    sync_row("sample_repo", row_dict, entity_id=sample.id)
    sample.synced_at = datetime.now(UTC)
    db.session.commit()
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def sync_row(module: str, row_dict: dict, entity_id: int | None = None) -> None:
    """Sync *row_dict* to Google Sheets synchronously.

    Args:
        module: Canonical module key (e.g. ``"sample_repo"``,
            ``"food_cell_do_intimations"``).
        row_dict: Field names and values to sync.
        entity_id: Optional DB record ID (unused, kept for parity).

    Raises:
        RuntimeError: If Sheets sync fails.
    """
    from app.services.sheets_sync import sync_to_sheets

    if not sync_to_sheets(module, row_dict):
        raise RuntimeError(f"Sync failed for {module}: sheets returned False")


__all__ = ["sync_row"]
