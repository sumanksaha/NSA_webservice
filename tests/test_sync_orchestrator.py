"""Tests for the SyncOrchestrator — Sheets-only entry point.

``sync_row`` forwards a row to Google Sheets synchronously and raises
``RuntimeError`` when the Sheets call fails. Airtable/Excel redundancy lives
in the backup chain (``backup_coordinator`` targets), not in this seam.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestSyncOrchestrator:
    """Verify sync_row delegates to Sheets and raises on failure."""

    def test_sync_row_sheets_succeed(self):
        from app.services.sync_orchestrator import sync_row

        with patch("app.services.sheets_sync.sync_to_sheets", return_value=True) as mock_s:
            # Should not raise - Sheets succeeds
            sync_row("sample_repo", {"id": 1}, entity_id=1)

        mock_s.assert_called_once_with("sample_repo", {"id": 1})

    def test_sync_row_sheets_fails_raises(self):
        from app.services.sync_orchestrator import sync_row

        with patch("app.services.sheets_sync.sync_to_sheets", return_value=False):
            with pytest.raises(RuntimeError, match=r"Sync failed"):
                sync_row("billing", {"Name": "Test"}, entity_id=42)

    def test_sync_row_exception_propagates(self):
        """A crash in Sheets must not be swallowed."""

        def boom(*a, **kw):
            raise RuntimeError("target exploded")

        from app.services.sync_orchestrator import sync_row

        with patch("app.services.sheets_sync.sync_to_sheets", side_effect=boom):
            with pytest.raises(RuntimeError, match="target exploded"):
                sync_row("inspection_log", {"id": 99})

    def test_sync_row_without_entity_id_works(self):
        """Calling sync_row without entity_id should still succeed."""
        from app.services.sync_orchestrator import sync_row

        with patch("app.services.sheets_sync.sync_to_sheets", return_value=True):
            # Should not raise
            sync_row("non_sample", {"case_number": "ABC"})

    def test_sync_row_preserves_module_key(self):
        """The module key is forwarded verbatim to Sheets."""
        from app.services.sync_orchestrator import sync_row

        with patch("app.services.sheets_sync.sync_to_sheets", return_value=True) as mock_s:
            sync_row("food_cell_do_intimations", {"sample_id": 1}, entity_id=5)

        mock_s.assert_called_once_with("food_cell_do_intimations", {"sample_id": 1})
