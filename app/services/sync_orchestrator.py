"""Sync orchestration layer — replaces 7× duplicated triple-sync try/except blocks.

Single entry point for syncing a row dict to all parallel sync targets:
Google Sheets (primary), Airtable (redundant), Excel Online (dormant).

Usage (replaces the triple try/except in every call site)::

    from app.services.sync_orchestrator import sync_row
    sync_row("sample_repo", row_dict, entity_id=sample.id)
    sample.synced_at = datetime.now(UTC)
    db.session.commit()

Sync is now **synchronous and mandatory** — any failure propagates so the
caller knows the operation failed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SyncError(RuntimeError):
    """Raised when a sync target fails.

    All parallel targets are tried before the exception is raised, so a
    partial success leaves Sheets/Airtable updated even though the
    caller is notified.
    """


def sync_row(module: str, row_dict: dict, entity_id: int | None = None) -> None:
    """Sync *row_dict* to all parallel sync targets synchronously.

    Args:
        module: Canonical module key (e.g. ``"sample_repo"``,
            ``"food_cell_do_intimations"``).
        row_dict: Field names and values to sync.
        entity_id: Optional DB record ID (passed to Airtable for tracking).

    Raises:
        SyncError: If any sync target fails. The error message lists all
            targets that failed.

    Sync is **mandatory and synchronous** — all three targets are
    attempted in order (Sheets → Airtable → Excel) and any failure is
    captured. After all targets are tried, a ``SyncError`` is raised
    listing every failure, so the caller is always aware of the outcome.
    """
    failures: list[str] = []

    # Sheets (primary target) — mandatory
    try:
        from app.services.sheets_sync import sync_to_sheets

        if not sync_to_sheets(module, row_dict):
            failures.append(f"sheets: returned False for {module}")
    except Exception as e:
        failures.append(f"sheets [{module}]: {e}")
        logger.error("Sheets sync failed [%s]: %s", module, e)

    # Airtable (redundant target) — mandatory
    try:
        from app.services.airtable_sync import sync_to_airtable

        if not sync_to_airtable(module, row_dict, entity_id):
            failures.append(f"airtable: returned False for {module}")
    except Exception as e:
        failures.append(f"airtable [{module}]: {e}")
        logger.error("Airtable sync failed [%s]: %s", module, e)

    # Excel Online (dormant — only if explicitly enabled)
    try:
        from app.shared.config import cfg

        if cfg.enable_excel_sync:
            from app.services.excel_sync import sync_to_excel

            if not sync_to_excel(module, row_dict, entity_id):
                failures.append(f"excel: returned False for {module}")
    except Exception as e:
        failures.append(f"excel [{module}]: {e}")
        logger.error("Excel sync failed [%s]: %s", module, e)

    if failures:
        raise SyncError(f"Sync failed for {module} ({len(failures)} target(s) failed): " + "; ".join(failures))


__all__ = ["SyncError", "sync_row"]
