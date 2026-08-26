"""Tests for S10c backup monitoring + S2 CSP-report collector.

Covers:
- ``/health/backups`` dead-man's-switch: never / ok / degraded / stale states
- ``record_backup_result`` bookkeeping written by ``run_backup()``
- ``POST /csp-report`` collector: valid reports, garbage bodies, public
  access, CSRF exemption
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture()
def env():
    """App + client + clean backup-bookkeeping keys (anonymous client)."""
    from app import create_app
    from app.extensions import db

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()

    yield app, app.test_client()

    db.session.remove()
    db.drop_all()
    ctx.pop()


def _set_setting(key: str, value: str, value_type: str = "string") -> None:
    from app.extensions import db
    from app.models.config import Settings

    db.session.merge(Settings(key=key, value=value, value_type=value_type))
    db.session.commit()


def json_dumps(payload: dict) -> bytes:
    import json

    return json.dumps(payload).encode()


class TestHealthBackups:
    def test_never_state_returns_503(self, env):
        _, client = env
        resp = client.get("/health/backups")
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["status"] == "never"
        assert body["last_backup_at"] is None

    def test_ok_state_returns_200(self, env):
        from app.services.backup_coordinator import record_backup_result

        record_backup_result({"sheets": True, "airtable": True, "excel": True, "full_archive": True})
        _, client = env
        resp = client.get("/health/backups")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "ok"
        assert body["targets"]["sheets"] is True
        assert body["age_hours"] is not None and body["age_hours"] < 26

    def test_degraded_when_a_target_failed(self, env):
        from app.services.backup_coordinator import record_backup_result

        record_backup_result({"sheets": True, "airtable": False})
        _, client = env
        resp = client.get("/health/backups")
        assert resp.status_code == 503
        assert resp.get_json()["status"] == "degraded"

    def test_stale_when_last_run_too_old(self, env):
        old = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
        _set_setting("last_backup_at", old)
        _set_setting("last_backup_results", '{"sheets": true}', value_type="json")
        _, client = env
        resp = client.get("/health/backups")
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["status"] == "stale"
        assert body["age_hours"] > 26

    def test_corrupt_timestamp_treated_as_never(self, env):
        _set_setting("last_backup_at", "not-a-date")
        _, client = env
        resp = client.get("/health/backups")
        assert resp.status_code == 503
        assert resp.get_json()["status"] == "never"

    def test_run_backup_records_bookkeeping(self, env, monkeypatch):
        """run_backup() persists its per-target outcome for the monitor."""
        from app.services.backup_coordinator import BackupTarget, last_backup_status, run_backup

        monkeypatch.setattr(BackupTarget, "export", lambda self: f"key-{self.name}")
        results = run_backup()

        assert results["sheets"] is True
        assert results["full_archive"] is True
        assert last_backup_status()["status"] == "ok"

    def test_anonymous_access_allowed(self, env):
        """/health/backups must be reachable without login (uptime probes)."""
        _, client = env
        # The fixture client never logs in — any non-302 proves the gate is open.
        assert client.get("/health/backups").status_code != 302


class TestCspReportCollector:
    @staticmethod
    def _capture_records(app):
        """Attach a capture handler to the collector's module logger.

        NOTE: pytest sessions can leave loggers ``disabled`` (observed: every
        ``logging.getLogger(name)`` reports ``disabled=True`` mid-session even
        though nothing in the app does this — production is unaffected), so
        re-enable the target logger explicitly while capturing.
        """
        import logging

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        log = logging.getLogger("app.health.routes")
        was_disabled = log.disabled
        log.disabled = False
        handler = _Capture()
        log.addHandler(handler)
        return records, handler, log, was_disabled

    @staticmethod
    def _release(log, handler, was_disabled):
        log.removeHandler(handler)
        log.disabled = was_disabled

    def test_valid_report_logged_and_204(self, env):
        """The collector logs directive/blocked-uri details."""
        _, client = env
        report = {
            "csp-report": {
                "document-uri": "https://example.test/",
                "violated-directive": "script-src",
                "blocked-uri": "https://evil.example/x.js",
            }
        }
        records, handler, log, was_disabled = self._capture_records(None)
        try:
            resp = client.post("/csp-report", data=json_dumps(report), content_type="application/csp-report")
        finally:
            self._release(log, handler, was_disabled)
        assert resp.status_code == 204
        assert any("script-src" in rec.getMessage() for rec in records)
        assert any("evil.example" in rec.getMessage() for rec in records)

    def test_report_to_style_report_accepted(self, env):
        _, client = env
        report = {"csp-violation-report": {"document-uri": "https://example.test/"}}
        resp = client.post("/csp-report", data=json_dumps(report), content_type="application/reports+json")
        assert resp.status_code == 204

    def test_garbage_body_still_204(self, env):
        _, client = env
        resp = client.post("/csp-report", data=b"\x00\xffnot-json", content_type="application/csp-report")
        assert resp.status_code == 204

    def test_get_not_allowed(self, env):
        _, client = env
        assert client.get("/csp-report").status_code == 405

    def test_public_and_csrf_exempt(self):
        """Anonymous POST passes even with CSRF enforcement enabled."""
        from app import create_app
        from app.extensions import db

        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = True  # enforce CSRF for this test

        ctx = app.app_context()
        ctx.push()
        try:
            db.drop_all()
            db.create_all()
            anon = app.test_client()
            resp = anon.post("/csp-report", data=b"{}", content_type="application/csp-report")
            assert resp.status_code == 204  # no 302 login redirect, no 400 CSRF
        finally:
            db.session.remove()
            db.drop_all()
            ctx.pop()
