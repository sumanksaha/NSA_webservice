#!/usr/bin/env python
"""Standalone backup script for Multi-Target Sheets Redundancy (Priority 7).

Usage:
    python scripts/backup_redundant_sheets.py

Calls ``export_sheets_to_r2()``, ``export_airtable_all_bases_to_r2()``,
and ``export_excel_to_r2()`` via the backup coordinator, printing a summary.

Each export is independently best-effort: a failure in one target does not
prevent the others from running.
"""

import sys
from pathlib import Path

# Ensure the app package is importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app


def run_backup() -> dict:
    """Export all three redundant sync targets to R2 (or local fallback).

    Returns a dict with per-target success flags and R2 keys::

        {"sheets": True, "airtable": False, "excel": True, "r2_keys": [...]}
    """
    app = create_app()
    with app.app_context():
        from app.services.backup_coordinator import run_backup as _run_backup

        return _run_backup()


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        results = run_backup()
        # Exit non-zero if NO target succeeded
        if not any(results[k] for k in ("sheets", "airtable", "excel")):
            sys.exit(1)
