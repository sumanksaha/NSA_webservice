"""Tests for the full-ZIP archive snapshot to R2 (Priority 7 extension).

Covers ``export_full_archive_to_r2()``, ``_prune_old_archives()``, and the
``run_backup()`` integration (flag gate + failure isolation).
"""

from __future__ import annotations

import contextlib
import io
import re
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def _app():
    """Create the test app once per module."""
    import os

    os.environ["SKIP_FSO_STARTUP_SYNC"] = "1"
    from app import create_app
    from app.extensions import db

    app = create_app()
    app.config["TESTING"] = True
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    db.session.remove()
    with contextlib.suppress(Exception):
        ctx.pop()
    return app


@pytest.fixture
def app_ctx(_app):
    """Push a fresh app context per test.

    ``tests/conftest.py::_pop_leaked_flask_app_context`` force-pops every
    Flask app context after each test, so a module-scoped pushed context
    cannot survive between tests — ``cfg`` would silently fall back to
    env/defaults. Pushing per test keeps Pattern A config resolution working
    inside every test body.
    """
    from app.extensions import db

    ctx = _app.app_context()
    ctx.push()
    try:
        yield _app
    finally:
        db.session.remove()
        with contextlib.suppress(Exception):
            ctx.pop()


class FakePaginator:
    """Minimal stand-in for boto3's list_objects_v2 paginator."""

    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects

    def paginate(self, Bucket, Prefix):  # noqa: N803
        keys = sorted(k for k in self._objects if k.startswith(Prefix))
        yield {"Contents": [{"Key": k} for k in keys]}


class FakeR2:
    """Minimal S3-compatible client capturing put/get/delete/paginate."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects: dict[str, bytes] = objects or {}
        self.fail_delete_for: set[str] = set()

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket, Key):  # noqa: N803
        body = self.objects[Key]

        class _Body:
            def read(self):
                return body

        return {"Body": _Body()}

    def delete_object(self, Bucket, Key):  # noqa: N803
        if Key in self.fail_delete_for:
            raise RuntimeError("simulated delete failure")
        self.objects.pop(Key, None)

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return FakePaginator(self.objects)


@pytest.fixture
def r2(app_ctx, monkeypatch):
    """Fake R2 client + bucket, injectable via app.utils.storage."""
    client = FakeR2()
    monkeypatch.setattr("app.utils.storage._get_client", lambda: client)
    monkeypatch.setattr("app.utils.storage._get_bucket", lambda: "test-bucket")
    return client


@pytest.fixture
def archive_enabled(app_ctx, monkeypatch):
    """Force the snapshot flag on / retention via Flask config (Pattern A)."""
    monkeypatch.setitem(app_ctx.config, "BACKUP_FULL_ARCHIVE_ENABLED", True)
    monkeypatch.setitem(app_ctx.config, "BACKUP_ARCHIVE_RETENTION", 30)


def _patch_archive_bytes(monkeypatch, payload: bytes = b"PK\x03\x04fake-zip"):
    monkeypatch.setattr("app.utils.backup.build_backup_archive", lambda: io.BytesIO(payload))
    return payload


class TestExportFullArchive:
    def test_uploads_zip_and_returns_key(self, app_ctx, r2, archive_enabled, monkeypatch):
        from app.services.backup_coordinator import ARCHIVE_PREFIX, export_full_archive_to_r2

        payload = _patch_archive_bytes(monkeypatch)
        key = export_full_archive_to_r2()

        assert key is not None
        assert key.startswith(ARCHIVE_PREFIX)
        assert key.endswith(".zip")
        assert r2.objects[key] == payload

    def test_upload_content_type_is_zip(self, app_ctx, r2, archive_enabled, monkeypatch):
        from app.services.backup_coordinator import export_full_archive_to_r2

        captured = {}

        def fake_put(Bucket, Key, Body, ContentType=None):  # noqa: N803
            captured["content_type"] = ContentType
            r2.objects[Key] = bytes(Body)

        monkeypatch.setattr(r2, "put_object", fake_put)
        _patch_archive_bytes(monkeypatch)
        export_full_archive_to_r2()
        assert captured["content_type"] == "application/zip"

    def test_disabled_returns_none_and_never_touches_r2(self, app_ctx, r2, monkeypatch):
        from app.services.backup_coordinator import export_full_archive_to_r2

        monkeypatch.setitem(app_ctx.config, "BACKUP_FULL_ARCHIVE_ENABLED", False)
        # Any accidental R2 access fails the test loudly.
        monkeypatch.setattr(
            "app.utils.storage._get_client", lambda: (_ for _ in ()).throw(AssertionError("R2 touched"))
        )

        assert export_full_archive_to_r2() is None
        assert r2.objects == {}

    def test_build_error_propagates(self, app_ctx, r2, archive_enabled, monkeypatch):
        from app.services import backup_coordinator

        def boom():
            raise RuntimeError("archive build failed")

        monkeypatch.setattr("app.utils.backup.build_backup_archive", boom)
        with pytest.raises(RuntimeError, match="archive build failed"):
            backup_coordinator.export_full_archive_to_r2()

    def test_key_is_chronologically_sortable(self, app_ctx, r2, archive_enabled, monkeypatch):
        from app.services.backup_coordinator import _archive_key

        _patch_archive_bytes(monkeypatch)
        key_a = _archive_key()
        key_b = _archive_key()
        assert key_a <= key_b  # same second → equal; never goes backwards
        assert re.fullmatch(r"nsa_backups/full_archives/nsa_backup_\d{8}_\d{6}\.zip", key_a)


class TestRetentionPruning:
    def _seed(self, r2, count):
        for i in range(count):
            r2.objects[f"nsa_backups/full_archives/nsa_backup_2026010{i}_000000.zip"] = b"old"

    def test_prunes_beyond_retention(self, app_ctx, r2, archive_enabled, monkeypatch):
        from app.services.backup_coordinator import export_full_archive_to_r2

        monkeypatch.setitem(app_ctx.config, "BACKUP_ARCHIVE_RETENTION", 2)
        self._seed(r2, 5)
        _patch_archive_bytes(monkeypatch)

        key = export_full_archive_to_r2()

        # Newest 2 survive: the fresh upload + the newest seeded archive.
        assert len(r2.objects) == 2
        assert key in r2.objects

    def test_no_pruning_under_retention(self, app_ctx, r2, archive_enabled, monkeypatch):
        from app.services.backup_coordinator import export_full_archive_to_r2

        self._seed(r2, 3)
        _patch_archive_bytes(monkeypatch)

        export_full_archive_to_r2()
        assert len(r2.objects) == 4  # 3 seeded + 1 new

    def test_delete_failure_is_isolated(self, app_ctx, r2, archive_enabled, monkeypatch):
        from app.services.backup_coordinator import export_full_archive_to_r2

        monkeypatch.setitem(app_ctx.config, "BACKUP_ARCHIVE_RETENTION", 1)
        self._seed(r2, 2)
        r2.fail_delete_for = {"nsa_backups/full_archives/nsa_backup_20260100_000000.zip"}
        _patch_archive_bytes(monkeypatch)

        # Must not raise despite the simulated delete failure.
        key = export_full_archive_to_r2()
        assert key in r2.objects


def _patch_sync_targets(monkeypatch, key: str | None = "nsa_backups/sheets_csv/x.csv"):
    monkeypatch.setattr("app.services.sheets_sync.export_sheets_to_r2", lambda: key)
    monkeypatch.setattr("app.services.airtable_sync.export_airtable_all_bases_to_r2", lambda: None)
    monkeypatch.setattr("app.services.excel_sync.export_excel_to_r2", lambda: None)


class TestRunBackupIntegration:
    def test_run_backup_includes_full_archive(self, app_ctx, r2, archive_enabled, monkeypatch):
        from app.services.backup_coordinator import run_backup

        _patch_sync_targets(monkeypatch)
        _patch_archive_bytes(monkeypatch)

        results = run_backup()
        assert results["sheets"] is True
        assert results["full_archive"] is True
        archive_keys = [k for k in results["r2_keys"] if "full_archives" in k]
        assert len(archive_keys) == 1

    def test_run_backup_archive_failure_does_not_block_targets(self, app_ctx, r2, archive_enabled, monkeypatch):
        from app.services.backup_coordinator import run_backup

        _patch_sync_targets(monkeypatch)
        monkeypatch.setattr(
            "app.services.backup_coordinator.export_full_archive_to_r2",
            lambda: (_ for _ in ()).throw(RuntimeError("r2 down")),
        )

        results = run_backup()
        assert results["sheets"] is True
        assert results["full_archive"] is False
        assert "full_archives" not in "".join(results["r2_keys"])

    def test_run_backup_disabled_archive_still_backs_up_targets(self, app_ctx, r2, monkeypatch):
        from app.services.backup_coordinator import run_backup

        monkeypatch.setitem(app_ctx.config, "BACKUP_FULL_ARCHIVE_ENABLED", False)
        _patch_sync_targets(monkeypatch)

        results = run_backup()
        assert results["sheets"] is True
        assert results["full_archive"] is False


class TestRestoreLatestArchive:
    def _seed_real_archive(self, r2, monkeypatch) -> str:
        """Build a genuine backup ZIP and place it in the fake bucket."""
        from app.utils.backup import build_backup_archive

        newest_key = "nsa_backups/full_archives/nsa_backup_20260102_000000.zip"
        r2.objects[newest_key] = build_backup_archive().getvalue()
        r2.objects["nsa_backups/full_archives/nsa_backup_20260101_000000.zip"] = b"older-junk"
        return newest_key

    def test_no_archives_returns_none(self, app_ctx, r2):
        from app.services.backup_coordinator import restore_latest_full_archive_from_r2

        assert restore_latest_full_archive_from_r2() is None

    def test_downloads_and_restores_newest(self, app_ctx, r2, monkeypatch):
        from app.services.backup_coordinator import restore_latest_full_archive_from_r2

        newest_key = self._seed_real_archive(r2, monkeypatch)
        result = restore_latest_full_archive_from_r2()

        assert result is not None
        assert result["key"] == newest_key
        # restore_from_archive stats are merged in
        assert "dialect" in result
        assert "files_restored" in result


class TestAutoRestoreIfEmpty:
    def test_not_empty_skips(self, monkeypatch):
        from app.utils.sync import auto_restore_if_empty

        with patch("app.utils.sync._is_empty_sqlite_db", return_value=False):
            r = auto_restore_if_empty()
        assert r == {"restored": False, "reason": "not-empty"}

    def test_empty_prefers_full_archive(self, monkeypatch):
        from app.utils.sync import auto_restore_if_empty

        with (
            patch("app.utils.sync._is_empty_sqlite_db", return_value=True),
            patch(
                "app.services.backup_coordinator.restore_latest_full_archive_from_r2",
                return_value={"key": "nsa_backups/full_archives/new.zip"},
            ),
        ):
            r = auto_restore_if_empty()
        assert r["restored"] is True
        assert r["source"] == "full_archive"
        assert r["key"] == "nsa_backups/full_archives/new.zip"

    def test_archive_failure_falls_back_to_csv_chain(self, monkeypatch):
        from app.utils.sync import auto_restore_if_empty

        with (
            patch("app.utils.sync._is_empty_sqlite_db", return_value=True),
            patch(
                "app.services.backup_coordinator.restore_latest_full_archive_from_r2",
                side_effect=RuntimeError("r2 down"),
            ),
            patch(
                "app.utils.sync.restore_if_empty",
                return_value={"restored": True, "source": "airtable", "count": 5},
            ),
        ):
            r = auto_restore_if_empty()
        assert r["restored"] is True
        assert r["source"] == "airtable"
        assert r["count"] == 5

    def test_no_backups_anywhere(self, monkeypatch):
        from app.utils.sync import auto_restore_if_empty

        with (
            patch("app.utils.sync._is_empty_sqlite_db", return_value=True),
            patch(
                "app.services.backup_coordinator.restore_latest_full_archive_from_r2",
                return_value=None,
            ),
            patch(
                "app.utils.sync.restore_if_empty",
                return_value={"restored": False, "source": None, "count": 0},
            ),
        ):
            r = auto_restore_if_empty()
        assert r["restored"] is False
        assert r["reason"] == "no-backups-found"

    def test_archive_none_falls_to_csv(self, monkeypatch):
        """A clean 'no archives' answer (not an error) also falls through."""
        from app.utils.sync import auto_restore_if_empty

        with (
            patch("app.utils.sync._is_empty_sqlite_db", return_value=True),
            patch(
                "app.services.backup_coordinator.restore_latest_full_archive_from_r2",
                return_value=None,
            ),
            patch(
                "app.utils.sync.restore_if_empty",
                return_value={"restored": True, "source": "sheets", "count": 9},
            ),
        ):
            r = auto_restore_if_empty()
        assert r["source"] == "sheets"
        assert r["count"] == 9
