"""Tests for the BackupTarget registry and unified CSV-restore engine.

Interface-level tests for the Priority 7 redundancy cluster: the single
canonical module→worksheet table, the parameterized restore engine behind the
three historical wrappers, and target-registry extension (adding a fourth
redundancy copy without editing callers).
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from app.services import backup_coordinator
from app.services.backup_restorer import BACKUP_MODULE_TO_TABLE, BackupRestorer
from app.utils.sync import (
    restore_from,
    restore_from_airtable_csv,
    restore_from_excel_csv,
    restore_from_sheets_csv,
)


def _fake_gdrive_export():
    """Module-level stub target function for the fourth-target registry test."""
    return "r2:g.csv"


class TestCanonicalMap:
    def test_canonical_map_has_expected_modules(self):
        """The canonical map maps module keys to table names — adding a
        synced module edits once here."""
        assert BACKUP_MODULE_TO_TABLE["non_sample"] == "adjudications"
        assert BACKUP_MODULE_TO_TABLE["sample"] == "case_files"
        assert BACKUP_MODULE_TO_TABLE["billing"] == "bills"
        assert BACKUP_MODULE_TO_TABLE["sample_repo"] == "samples"
        assert BACKUP_MODULE_TO_TABLE["inspection_log"] == "inspections"
        assert BACKUP_MODULE_TO_TABLE["food_cell_do_intimations"] == "do_intimations"


class TestRestoreEngine:
    def test_parameterized_engine_dispatches_by_prefix(self):
        with (
            patch.object(BackupRestorer, "_list_r2_csv_backups", return_value=["r2:e.csv"]) as m_list,
            patch.object(BackupRestorer, "_download_r2_csv", return_value="module,base_id\nsample_repo,1"),
            patch.object(BackupRestorer, "_csv_to_records", return_value=[{"module": "sample_repo", "base_id": "1"}]),
            patch.object(BackupRestorer, "_restore_from_records", return_value=4) as m_restore,
        ):
            count = restore_from("excel")
        assert count == 4
        m_list.assert_called_once_with("excel")
        m_restore.assert_called_once_with([{"module": "sample_repo", "base_id": "1"}], "excel")

    def test_wrappers_delegate_to_engine(self):
        calls = []
        with patch.object(BackupRestorer, "restore_from", side_effect=lambda t: calls.append(t) or 7):
            assert restore_from_airtable_csv() == 7
            assert restore_from_excel_csv() == 7
            assert restore_from_sheets_csv() == 7
        assert calls == ["airtable", "excel", "sheets"]

    def test_engine_returns_zero_when_no_backups(self):
        with patch.object(BackupRestorer, "_list_r2_csv_backups", return_value=[]):
            assert restore_from("airtable") == 0


class TestBackupTargets:
    def test_registry_covers_all_four_targets(self):
        names = [t.name for t in backup_coordinator.TARGETS]
        assert names == ["sheets", "airtable", "excel", "full_archive"]

    def test_new_target_needs_only_one_row(self):
        """A fourth redundancy copy = one adapter row; zero caller edits."""
        sentinel = backup_coordinator.BackupTarget(
            name="gdrive",
            result_key="gdrive",
            module_name=__name__,
            func_name="_fake_gdrive_export",
        )
        with patch.object(backup_coordinator, "TARGETS", (*backup_coordinator.TARGETS, sentinel)):
            with patch(f"{__name__}._fake_gdrive_export", return_value="r2:g.csv"):
                results = backup_coordinator.run_backup()
        assert results["gdrive"] is True
        assert "r2:g.csv" in results["r2_keys"]

    def test_run_backup_isolates_target_failure(self):
        broken = backup_coordinator.BackupTarget(
            name="broken",
            result_key="broken",
            module_name=__name__,
            func_name="_nonexistent_function_xyz",
        )
        targets = tuple(t if t.name != "full_archive" else broken for t in backup_coordinator.TARGETS)
        with (
            patch.object(backup_coordinator, "TARGETS", targets),
            patch("app.services.sheets_sync.export_sheets_to_r2", return_value="r2:s.csv"),
            patch("app.services.airtable_sync.export_airtable_all_bases_to_r2", return_value=None),
            patch("app.services.excel_sync.export_excel_to_r2", return_value=None),
        ):
            results = backup_coordinator.run_backup()
        # The failing target is isolated; the rest still ran.
        assert results["sheets"] is True
        assert results["broken"] is False
        assert results["r2_keys"] == ["r2:s.csv"]
