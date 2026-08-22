"""Tests for Priority 7 - Multi-Target Sheets Redundancy."""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest


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
    """Push a fresh app context per test.

    ``tests/conftest.py::_pop_leaked_flask_app_context`` force-pops every
    Flask app context after each test, so a module-scoped pushed context
    cannot survive between tests — later tests silently lose the context
    (``_is_empty_sqlite_db`` swallows the resulting errors and reports an
    empty DB; route fixtures fail at setup). Pushing per test keeps DB
    access and Pattern A config resolution working in every test body.

    ``db.session.remove()`` detaches the scoped session from whatever
    (now-popped) context owned it — a stale session here is the source of
    intermittent "Working outside of application context" setup errors
    under load.
    """
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


def _sample_csv_for_module(module, rows=1):
    import csv as csv_mod
    import io

    fieldnames = ["module", "base_id", "field_0", "field_1", "field_2"]
    buf = io.StringIO()
    writer = csv_mod.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for i in range(rows):
        row = {"module": module, "base_id": "appTest123"}
        row.update({f"field_{j}": f"v{i}{j}" for j in range(3)})
        writer.writerow(row)
    return buf.getvalue()


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


class TestBackupCoordinator:
    def test_run_backup_all_succeed(self):
        from app.services.backup_coordinator import run_backup

        with (
            patch("app.services.sheets_sync.export_sheets_to_r2", return_value="r2:s.csv"),
            patch("app.services.airtable_sync.export_airtable_all_bases_to_r2", return_value="r2:a.csv"),
            patch("app.services.excel_sync.export_excel_to_r2", return_value="r2:e.csv"),
        ):
            r = run_backup()
        assert r["sheets"] is True and r["airtable"] is True and r["excel"] is True
        assert len(r["r2_keys"]) == 3

    def test_run_backup_partial_failure(self):
        from app.services.backup_coordinator import run_backup

        with (
            patch("app.services.sheets_sync.export_sheets_to_r2", return_value="r2:s.csv"),
            patch("app.services.airtable_sync.export_airtable_all_bases_to_r2", side_effect=Exception("down")),
            patch("app.services.excel_sync.export_excel_to_r2", return_value="r2:e.csv"),
        ):
            r = run_backup()
        assert r["sheets"] is True and r["airtable"] is False and r["excel"] is True

    def test_run_backup_all_fail(self):
        from app.services.backup_coordinator import run_backup

        with (
            patch("app.services.sheets_sync.export_sheets_to_r2", return_value=None),
            patch("app.services.airtable_sync.export_airtable_all_bases_to_r2", return_value=None),
            patch("app.services.excel_sync.export_excel_to_r2", return_value=None),
        ):
            r = run_backup()
        assert r["sheets"] is False and r["airtable"] is False and r["excel"] is False

    def test_run_backup_isolation(self):
        from app.services.backup_coordinator import run_backup

        c = {"e": 0}
        with (
            patch("app.services.sheets_sync.export_sheets_to_r2", return_value="r2:s.csv"),
            patch("app.services.airtable_sync.export_airtable_all_bases_to_r2", side_effect=Exception("down")),
            patch(
                "app.services.excel_sync.export_excel_to_r2",
                side_effect=lambda: (c.__setitem__("e", c["e"] + 1), "r2:e.csv")[1],
            ),
        ):
            r = run_backup()
        assert r["sheets"] is True and r["excel"] is True and c["e"] == 1


class TestRestoreFromCsv:
    def test_no_backups_returns_zero(self):
        with patch("app.utils.sync._list_r2_csv_backups", return_value=[]):
            from app.utils.sync import restore_from_airtable_csv

            assert restore_from_airtable_csv() == 0

    def test_download_failure_returns_zero(self):
        with (
            patch("app.utils.sync._list_r2_csv_backups", return_value=["r2:ab.csv"]),
            patch("app.utils.sync._download_r2_csv", return_value=None),
        ):
            from app.utils.sync import restore_from_airtable_csv

            assert restore_from_airtable_csv() == 0

    def test_empty_csv_returns_zero(self):
        with (
            patch("app.utils.sync._list_r2_csv_backups", return_value=["r2:ab.csv"]),
            patch("app.utils.sync._download_r2_csv", return_value="module,base_id\n"),
        ):
            from app.utils.sync import restore_from_airtable_csv

            assert restore_from_airtable_csv() == 0

    def test_excel_no_backups_returns_zero(self):
        with patch("app.utils.sync._list_r2_csv_backups", return_value=[]):
            from app.utils.sync import restore_from_excel_csv

            assert restore_from_excel_csv() == 0

    def test_excel_csv_success(self):
        csv_content = _sample_csv_for_module("sample", rows=2)
        with (
            patch("app.utils.sync._list_r2_csv_backups", return_value=["r2:eb.csv"]),
            patch("app.utils.sync._download_r2_csv", return_value=csv_content),
            patch("app.utils.sync._restore_module", return_value=2),
        ):
            from app.utils.sync import restore_from_excel_csv

            assert restore_from_excel_csv() == 2

    def test_filters_by_known_module(self):
        csv_content = "module,base_id,v\nunknown_mod,app123,hello\nnon_sample,app123,val"
        with (
            patch("app.utils.sync._list_r2_csv_backups", return_value=["r2:ab.csv"]),
            patch("app.utils.sync._download_r2_csv", return_value=csv_content),
            patch("app.utils.sync._restore_from_records", return_value=1) as m,
        ):
            from app.utils.sync import restore_from_airtable_csv

            restore_from_airtable_csv()
            records = m.call_args[0][0]
            assert len(records) == 1
            assert records[0]["module"] == "non_sample"


def test_restore_from_records_strips_metadata():
    from app.utils.sync import _restore_from_records

    records = [
        {"module": "x", "base_id": "a", "id": "5", "f0": "v"},
        {"module": "x", "base_id": "b", "id": "6", "f0": "v2"},
    ]
    with patch("app.utils.sync._restore_module", return_value=2) as m:
        _restore_from_records(records, "airtable")
        rows = m.call_args[0][1]
        assert len(rows) == 2
        for row in rows:
            assert "module" not in row
            assert "base_id" not in row
            assert "id" not in row


def test_restore_module_unknown_returns_zero():
    from app.utils.sync import _restore_module

    assert _restore_module("nope", [{"f": "v"}]) == 0


def test_restore_module_empty_rows_returns_zero():
    from app.utils.sync import _restore_module

    assert _restore_module("non_sample", []) == 0


class TestIsEmptySqliteDb:
    def test_empty_db_true(self, app_ctx):
        """Empty DB check should return True when all counts are zero."""
        from unittest.mock import patch

        from app.extensions import db
        from app.utils.sync import _is_empty_sqlite_db

        with patch.object(db, "session") as mock_session:
            mock_session.execute.return_value.scalar.return_value = 0
            assert _is_empty_sqlite_db() is True

    def test_nonempty_db_false(self, app_ctx):
        """Non-empty DB (fixture already added User+FSO) should return False."""
        from app.utils.sync import _is_empty_sqlite_db

        assert _is_empty_sqlite_db() is False


class TestSettingsBackupRoutes:
    def test_backup_dashboard_renders(self, client):
        assert client.get("/settings/backup").status_code == 200

    def test_backup_redundant_post(self, client):
        with patch(
            "app.services.backup_coordinator.run_backup",
            return_value={"sheets": True, "airtable": True, "excel": True, "r2_keys": []},
        ):
            resp = client.post("/settings/backup-redundant-to-r2")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["sheets"] is True

    def test_backup_restore_get_status(self, client):
        resp = client.get("/settings/backup-redundant-to-r2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "description" in data


class TestQstashSchedule:
    def test_publish_recurring_disabled(self, app_ctx):
        app, _ = app_ctx
        from app.utils.qstash_client import publish_recurring

        with app.app_context():
            result = publish_recurring("backup_redundant_sheets", schedule="0 2 * * *", payload={})
            assert result["mode"] == "disabled"

    def test_publish_recurring_unknown_raises(self):
        from app.utils.qstash_client import publish_recurring

        with pytest.raises(ValueError, match="Unknown task"):
            publish_recurring("nope", schedule="0 2 * * *")

    def test_task_registry_has_backup(self):
        from app.utils.qstash_client import TASK_REGISTRY

        assert "backup_redundant_sheets" in TASK_REGISTRY
        assert TASK_REGISTRY["backup_redundant_sheets"] == ("app.services.backup_coordinator", "run_backup")


class TestPriority7Config:
    def test_airtable_keys(self, app_ctx):
        app, _ = app_ctx
        assert "AIRTABLE_API_KEY" in app.config
        assert "AIRTABLE_BASE_ID" in app.config
        assert "ENABLE_AIRTABLE_SYNC" in app.config

    def test_excel_keys(self, app_ctx):
        app, _ = app_ctx
        assert "MS_TENANT_ID" in app.config
        assert "MS_SPREADSHEET_ID" in app.config
        assert "ENABLE_EXCEL_SYNC" in app.config

    def test_flags_default_false(self, app_ctx):
        app, _ = app_ctx
        assert app.config["ENABLE_AIRTABLE_SYNC"] is False
        assert app.config["ENABLE_EXCEL_SYNC"] is False


class TestBackupScript:
    def test_script_exists(self):
        p = Path(__file__).resolve().parent.parent / "scripts" / "backup_redundant_sheets.py"
        assert p.exists()

    def test_script_delegates(self, app_ctx):
        import importlib
        import sys

        _app, _ = app_ctx
        script_dir = str(Path(__file__).resolve().parent.parent / "scripts")
        sys.path.insert(0, script_dir)
        try:
            if "backup_redundant_sheets" in sys.modules:
                del sys.modules["backup_redundant_sheets"]
            mod = importlib.import_module("backup_redundant_sheets")
            with patch(
                "app.services.backup_coordinator.run_backup",
                return_value={"sheets": True, "airtable": True, "excel": True},
            ):
                r = mod.run_backup()
                assert r["sheets"] is True
        finally:
            sys.path.pop(0)


class TestRestoreFromSheetsCsv:
    def test_sheets_no_backups_returns_zero(self):
        with patch("app.utils.sync._list_r2_csv_backups", return_value=[]):
            from app.utils.sync import restore_from_sheets_csv

            assert restore_from_sheets_csv() == 0

    def test_sheets_download_failure_returns_zero(self):
        with (
            patch("app.utils.sync._list_r2_csv_backups", return_value=["r2:s.csv"]),
            patch("app.utils.sync._download_r2_csv", return_value=None),
        ):
            from app.utils.sync import restore_from_sheets_csv

            assert restore_from_sheets_csv() == 0

    def test_sheets_csv_success(self):
        csv_content = _sample_csv_for_module("sample", rows=3)
        with (
            patch("app.utils.sync._list_r2_csv_backups", return_value=["r2:s.csv"]),
            patch("app.utils.sync._download_r2_csv", return_value=csv_content),
            patch("app.utils.sync._restore_module", return_value=3),
        ):
            from app.utils.sync import restore_from_sheets_csv

            assert restore_from_sheets_csv() == 3


class TestRestoreIfEmpty:
    def test_not_empty_no_restore(self):
        with patch("app.utils.sync._is_empty_sqlite_db", return_value=False):
            from app.utils.sync import restore_if_empty

            r = restore_if_empty()
            assert r["restored"] is False
            assert r["source"] is None

    def test_empty_restores_from_airtable(self):
        with (
            patch("app.utils.sync._is_empty_sqlite_db", return_value=True),
            patch("app.utils.sync.restore_from_airtable_csv", return_value=5),
            patch("app.utils.sync.restore_from_excel_csv", return_value=0),
            patch("app.utils.sync.restore_from_sheets_csv", return_value=0),
        ):
            from app.utils.sync import restore_if_empty

            r = restore_if_empty()
            assert r["restored"] is True
            assert r["source"] == "airtable"
            assert r["count"] == 5

    def test_empty_falls_back_to_excel(self):
        with (
            patch("app.utils.sync._is_empty_sqlite_db", return_value=True),
            patch("app.utils.sync.restore_from_airtable_csv", return_value=0),
            patch("app.utils.sync.restore_from_excel_csv", return_value=3),
            patch("app.utils.sync.restore_from_sheets_csv", return_value=0),
        ):
            from app.utils.sync import restore_if_empty

            r = restore_if_empty()
            assert r["source"] == "excel"
            assert r["count"] == 3

    def test_empty_falls_back_to_sheets(self):
        with (
            patch("app.utils.sync._is_empty_sqlite_db", return_value=True),
            patch("app.utils.sync.restore_from_airtable_csv", return_value=0),
            patch("app.utils.sync.restore_from_excel_csv", return_value=0),
            patch("app.utils.sync.restore_from_sheets_csv", return_value=7),
        ):
            from app.utils.sync import restore_if_empty

            r = restore_if_empty()
            assert r["source"] == "sheets"
            assert r["count"] == 7

    def test_empty_all_fail(self):
        with (
            patch("app.utils.sync._is_empty_sqlite_db", return_value=True),
            patch("app.utils.sync.restore_from_airtable_csv", return_value=0),
            patch("app.utils.sync.restore_from_excel_csv", return_value=0),
            patch("app.utils.sync.restore_from_sheets_csv", return_value=0),
        ):
            from app.utils.sync import restore_if_empty

            r = restore_if_empty()
            assert r["restored"] is False
            assert r["count"] == 0


class TestTriggerBackup:
    def test_trigger_backup_delegates(self):
        with patch(
            "app.services.backup_coordinator.run_backup", return_value={"sheets": True, "airtable": True, "excel": True}
        ) as m:
            from app.utils.sync import trigger_backup

            r = trigger_backup()
            assert m.call_count == 1
            assert r["sheets"] is True
