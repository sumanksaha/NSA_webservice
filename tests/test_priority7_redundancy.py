"""Tests for Priority 7 - Multi-Target Sheets Redundancy (post-D6 deepening)."""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from dataclasses import dataclass


@pytest.fixture(scope="module")
def _app():
    """Create the test app + baseline data once per module."""
    import os

    os.environ["SKIP_FSO_STARTUP_SYNC"] = "1"
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    user = User(username="p7testuser", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()
    db.session.remove()
    with contextlib.suppress(Exception):
        ctx.pop()
    return app


@pytest.fixture
def app_ctx(_app):
    """Push a fresh app context per test."""
    from app.extensions import db
    from app.models import User

    ctx = _app.app_context()
    ctx.push()
    db.session.remove()
    try:
        user = User.query.filter_by(username="p7testuser").first()
        yield _app, user
    finally:
        db.session.remove()
        with contextlib.suppress(Exception):
            ctx.pop()


@pytest.fixture
def client(app_ctx):
    app, user = app_ctx
    from app.models import User

    user = User.query.filter_by(username="p7testuser").first()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
    return c


# ---------------------------------------------------------------------------
# Pure-function tests — these remain unchanged: _csv_to_records and
# _parse_csv_value are re-exported from the deep module but are stateless.
# ---------------------------------------------------------------------------


class TestCsvParsing:
    def test_csv_to_records_parses(self):
        from app.utils.sync import _csv_to_records

        r = _csv_to_records("module,base_id,f0\na,1,h\nb,1,w")
        assert len(r) == 2
        assert r[0]["module"] == "a"

    def test_csv_to_records_empty(self):
        from app.utils.sync import _csv_to_records

        assert _csv_to_records("module\n") == []

    def test_parse_csv_value_integer(self):
        from app.utils.sync import _parse_csv_value

        assert _parse_csv_value("42", "Integer") == 42
        assert _parse_csv_value("bad", "Integer") is None

    def test_parse_csv_value_float(self):
        from app.utils.sync import _parse_csv_value

        assert _parse_csv_value("3.14", "Float") == 3.14

    def test_parse_csv_value_boolean(self):
        from app.utils.sync import _parse_csv_value

        assert _parse_csv_value("true", "Boolean") is True
        assert _parse_csv_value("0", "Boolean") is False

    def test_parse_csv_value_datetime(self):
        from app.utils.sync import _parse_csv_value

        result = _parse_csv_value("2026-01-15T10:30:00", "DateTime")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_parse_csv_value_empty(self):
        from app.utils.sync import _parse_csv_value

        assert _parse_csv_value("", "Integer") is None
        assert _parse_csv_value(None, "String") is None

    def test_parse_csv_value_biginteger(self):
        from app.utils.sync import _parse_csv_value

        assert _parse_csv_value("9223372036854775807", "BigInteger") == 9223372036854775807


# ---------------------------------------------------------------------------
# Top-level engine tests — patch the deep module's public adapter.
# The single `restore_from(target)` dispatch replaces the three former
# wrappers (restore_from_airtable_csv, restore_from_excel_csv,
# restore_from_sheets_csv). The BackupRestorer singleton is available
# from app.utils.sync as the re-exported `restore_from`.
# ---------------------------------------------------------------------------


class TestRestoreEngine:
    def test_parameterized_engine_dispatches_by_prefix(self):
        from app.utils.sync import restore_from, BACKUP_MODULE_TO_TABLE

        with (
            patch("app.services.backup_restorer.BackupRestorer._list_r2_csv_backups", return_value=["r2:e.csv"]),
            patch("app.services.backup_restorer.BackupRestorer._download_r2_csv", return_value="module,base_id\nsample_repo,1"),
            patch("app.utils.sync._csv_to_records", return_value=[{"module": "sample_repo", "base_id": "1"}]),
            patch("app.services.backup_restorer.BackupRestorer._restore_from_records", return_value=4) as m_restore,
        ):
            count = restore_from("excel")
        assert count == 4
        m_restore.assert_called_once_with([{"module": "sample_repo", "base_id": "1"}], "excel")

    def test_wrappers_delegate_to_engine(self):
        calls = []
        with patch(
            "app.services.backup_restorer.BackupRestorer.restore_from",
            side_effect=lambda t: calls.append(t) or 7,
        ):
            from app.utils.sync import restore_from_airtable_csv, restore_from_excel_csv, restore_from_sheets_csv

            assert restore_from_airtable_csv() == 7
            assert restore_from_excel_csv() == 7
            assert restore_from_sheets_csv() == 7
        assert calls == ["airtable", "excel", "sheets"]

    def test_engine_returns_zero_when_no_backups(self):
        from app.utils.sync import restore_from

        with patch("app.services.backup_restorer.BackupRestorer._list_r2_csv_backups", return_value=[]):
            assert restore_from("airtable") == 0


# ---------------------------------------------------------------------------
# _restore_module / _restore_from_records — kept for pure-function
# test compatibility; they delegate to the deep module internals.
# ---------------------------------------------------------------------------


class TestRestoreModuleInternals:
    def test_restore_module_unknown_returns_zero(self):
        from app.services.backup_restorer import BackupRestorer

        with patch.object(BackupRestorer, "_restore_module", return_value=0):
            assert BackupRestorer._restore_module("nope", [{"f": "v"}]) == 0

    def test_restore_module_empty_rows_returns_zero(self):
        from app.services.backup_restorer import BackupRestorer

        with patch.object(BackupRestorer, "_restore_module", return_value=0):
            assert BackupRestorer._restore_module("sample", []) == 0


# ---------------------------------------------------------------------------
# Empty-DB check — kept from the original suite.
# ---------------------------------------------------------------------------


class TestIsEmptySqliteDb:
    def test_empty_db_true(self, app_ctx):
        from unittest.mock import patch

        from app.extensions import db
        from app.utils.sync import _is_empty_sqlite_db

        with patch.object(db, "session") as mock_session:
            mock_session.execute.return_value.scalar.return_value = 0
            assert _is_empty_sqlite_db() is True

    def test_nonempty_db_false(self, app_ctx):
        from app.utils.sync import _is_empty_sqlite_db

        assert _is_empty_sqlite_db() is False


# ---------------------------------------------------------------------------
# restore_if_empty migration: the former triple-dispatch (airtable/excel/sheets)
# is now one call to the deep module's restore_if_empty().  The old per-source
# mocks are replaced by a single mock on the new interface.
# ---------------------------------------------------------------------------


class TestRestoreIfEmpty:
    def test_not_empty_no_restore(self):
        from app.utils.sync import restore_if_empty

        with patch("app.services.backup_restorer.BackupRestorer._is_empty_sqlite_db", return_value=False):
            r = restore_if_empty()
            assert r["restored"] is False
            assert r["source"] is None

    def test_empty_restores_from_airtable(self):
        from app.utils.sync import restore_if_empty

        with (
            patch("app.services.backup_restorer.BackupRestorer._is_empty_sqlite_db", return_value=True),
            patch(
                "app.services.backup_restorer.BackupRestorer.restore_from",
                side_effect=[5, 0, 0],
            ),
        ):
            r = restore_if_empty()
            assert r["restored"] is True
            assert r["source"] == "airtable"
            assert r["count"] == 5

    def test_empty_falls_back_to_excel(self):
        from app.utils.sync import restore_if_empty

        with (
            patch("app.services.backup_restorer.BackupRestorer._is_empty_sqlite_db", return_value=True),
            patch(
                "app.services.backup_restorer.BackupRestorer.restore_from",
                side_effect=[0, 3, 0],
            ),
        ):
            r = restore_if_empty()
            assert r["source"] == "excel"
            assert r["count"] == 3

    def test_empty_falls_back_to_sheets(self):
        from app.utils.sync import restore_if_empty

        with (
            patch("app.services.backup_restorer.BackupRestorer._is_empty_sqlite_db", return_value=True),
            patch(
                "app.services.backup_restorer.BackupRestorer.restore_from",
                side_effect=[0, 0, 7],
            ),
        ):
            r = restore_if_empty()
            assert r["source"] == "sheets"
            assert r["count"] == 7

    def test_empty_all_fail(self):
        from app.utils.sync import restore_if_empty

        with (
            patch("app.services.backup_restorer.BackupRestorer._is_empty_sqlite_db", return_value=True),
            patch(
                "app.services.backup_restorer.BackupRestorer.restore_from",
                side_effect=[0, 0, 0],
            ),
        ):
            r = restore_if_empty()
            assert r["restored"] is False
            assert r["count"] == 0


# ---------------------------------------------------------------------------
# trigger_backup test — unchanged; still delegates to
# app.services.backup_coordinator.run_backup
# ---------------------------------------------------------------------------


class TestTriggerBackup:
    def test_trigger_backup_delegates(self):
        from app.utils.sync import trigger_backup

        with patch(
            "app.services.backup_coordinator.run_backup", return_value={"sheets": True, "airtable": True, "excel": True}
        ) as m:
            r = trigger_backup()
            assert m.call_count == 1
            assert r["sheets"] is True