"""Tests for the SyncOrchestrator — the single entry point that replaces
the 7× duplicated triple-sync try/except blocks across route files.

Uses mock adapters so no real Google Sheets / Airtable / Excel credentials
are needed.
"""

from __future__ import annotations

from unittest.mock import patch


class TestSyncOrchestrator:
    """Verify sync_row delegates to all three targets and raises on failure."""

    def test_sync_row_all_targets_succeed(self):
        from app.services.sync_orchestrator import sync_row

        # The orchestrator lazy-imports the real sync functions, so we patch them at their source modules.
        with (
            patch("app.services.sheets_sync.sync_to_sheets", return_value=True) as mock_s,
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True) as mock_a,
            patch("app.services.excel_sync.sync_to_excel", return_value=True) as mock_e,
        ):
            # Should not raise - all targets succeed
            sync_row("sample_repo", {"id": 1}, entity_id=1)

        mock_s.assert_called_once_with("sample_repo", {"id": 1})
        mock_a.assert_called_once_with("sample_repo", {"id": 1}, 1)
        # Excel may or may not be called depending on cfg.excel_sync_enabled

    def test_sync_row_sheets_fails_raises(self):
        from app.services.sync_orchestrator import SyncError, sync_row

        with (
            patch("app.services.sheets_sync.sync_to_sheets", return_value=False),
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True),
            patch("app.services.excel_sync.sync_to_excel", return_value=True),
        ):
            try:
                sync_row("billing", {"Name": "Test"}, entity_id=42)
                assert False, "expected SyncError"
            except SyncError as e:
                assert "sheets" in str(e).lower()

    def test_sync_row_exception_in_target_raises(self):
        """A crash in one target must be reported via SyncError."""
        from app.services.sync_orchestrator import SyncError, sync_row

        def boom(*a, **kw):
            raise RuntimeError("target exploded")

        with (
            patch("app.services.sheets_sync.sync_to_sheets", side_effect=boom),
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True),
            patch("app.services.excel_sync.sync_to_excel", return_value=True),
        ):
            try:
                sync_row("inspection_log", {"id": 99})
                assert False, "expected SyncError"
            except SyncError as e:
                assert "sheets" in str(e).lower()

    def test_sync_row_without_entity_id_works(self):
        """Calling sync_row without entity_id should still succeed."""
        from app.services.sync_orchestrator import sync_row

        with (
            patch("app.services.sheets_sync.sync_to_sheets", return_value=True),
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True),
            patch("app.services.excel_sync.sync_to_excel", return_value=True),
        ):
            # Should not raise
            sync_row("non_sample", {"case_number": "ABC"})

    def test_sync_row_preserves_module_key(self):
        """The module key is forwarded verbatim to all targets."""
        from app.services.sync_orchestrator import sync_row

        with (
            patch("app.services.sheets_sync.sync_to_sheets", return_value=True) as mock_s,
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True),
            patch("app.services.excel_sync.sync_to_excel", return_value=True),
        ):
            sync_row("food_cell_do_intimations", {"sample_id": 1}, entity_id=5)

        mock_s.assert_called_once_with("food_cell_do_intimations", {"sample_id": 1})
