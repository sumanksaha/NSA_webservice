"""Tests for the SyncOrchestrator — the single entry point that replaces
the 7× duplicated triple-sync try/except blocks across route files.

Uses mock adapters so no real Google Sheets / Airtable / Excel credentials
are needed.
"""

from __future__ import annotations

from unittest.mock import patch


class TestSyncOrchestrator:
    """Verify sync_row delegates to all three targets and returns per-target flags."""

    def test_sync_row_all_targets_succeed(self):
        from app.services.sync_orchestrator import sync_row, SyncResult

        # The orchestrator lazy-imports the real sync functions, so we patch them at their source modules.
        with (
            patch("app.services.sheets_sync.sync_to_sheets", return_value=True) as mock_s,
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True) as mock_a,
            patch("app.services.excel_sync.sync_to_excel", return_value=True) as mock_e,
        ):
            result = sync_row("sample_repo", {"id": 1}, entity_id=1)

        assert result == {"sheets": True, "airtable": True, "excel": True}
        mock_s.assert_called_once_with("sample_repo", {"id": 1})
        mock_a.assert_called_once_with("sample_repo", {"id": 1}, 1)
        mock_e.assert_called_once_with("sample_repo", {"id": 1}, 1)
        # Type check: result is a SyncResult
        assert isinstance(result, dict)

    def test_sync_row_sheets_fails_airtable_succeeds(self):
        from app.services.sync_orchestrator import sync_row

        with (
            patch("app.services.sheets_sync.sync_to_sheets", return_value=False),
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True),
            patch("app.services.excel_sync.sync_to_excel", return_value=True),
        ):
            result = sync_row("billing", {"Name": "Test"}, entity_id=42)

        assert result == {"sheets": False, "airtable": True, "excel": True}

    def test_sync_row_independent_failure_isolation(self):
        """A crash in one target must not prevent the others."""
        from app.services.sync_orchestrator import sync_row

        def boom(*a, **kw):
            raise RuntimeError("target exploded")

        with (
            patch("app.services.sheets_sync.sync_to_sheets", side_effect=boom),
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True),
            patch("app.services.excel_sync.sync_to_excel", return_value=True),
        ):
            result = sync_row("inspection_log", {"id": 99})

        assert result == {"sheets": False, "airtable": True, "excel": True}

    def test_sync_row_without_entity_id_works(self):
        """Calling sync_row without entity_id should still succeed."""
        from app.services.sync_orchestrator import sync_row

        with (
            patch("app.services.sheets_sync.sync_to_sheets", return_value=True),
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True),
            patch("app.services.excel_sync.sync_to_excel", return_value=True),
        ):
            result = sync_row("non_sample", {"case_number": "ABC"})

        assert result == {"sheets": True, "airtable": True, "excel": True}

    def test_sync_row_preserves_module_key(self):
        """The module key is forwarded verbatim to all targets."""
        from app.services.sync_orchestrator import sync_row

        with (
            patch("app.services.sheets_sync.sync_to_sheets", return_value=True) as mock_s,
            patch("app.services.airtable_sync.sync_to_airtable", return_value=False),
            patch("app.services.excel_sync.sync_to_excel", return_value=False),
        ):
            sync_row("food_cell_do_intimations", {"sample_id": 1}, entity_id=5)

        mock_s.assert_called_once_with("food_cell_do_intimations", {"sample_id": 1})

    def test_sync_row_dormant_excel_returns_false(self):
        """When ENABLE_EXCEL_SYNC is false (dormant), sync_to_excel returns False."""
        from app.services.sync_orchestrator import sync_row

        with (
            patch("app.services.sheets_sync.sync_to_sheets", return_value=True),
            patch("app.services.airtable_sync.sync_to_airtable", return_value=True),
            # sync_to_excel checks ENABLE_EXCEL_SYNC — simulate dormant
            patch("app.services.excel_sync.sync_to_excel", return_value=False),
        ):
            result = sync_row("sample_repo", {"id": 1})

        assert result["sheets"] is True
        assert result["airtable"] is True
        assert result["excel"] is False
