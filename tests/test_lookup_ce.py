"""Regression tests for the KMC trade-license (CE) lookup.

Production report (2026-09-26): every trade-license lookup failed with
"Could not reach KMC portal. Try again." Root cause: ``lookup_ce`` only
caught ``httpx.HTTPError``, but the ``RateLimiter`` died first with
``FileNotFoundError`` — the ``db/`` lock dir is not tracked in git and
exists on no fresh checkout/deploy. The raw exception escaped to the
route's blanket ``except`` → HTTP 502.

Fixed alongside these tests:

1. ``RateLimiter.acquire()`` creates its parent dir (``mkdir parents``),
   so a missing ``db/`` can never fail a lookup.
2. ``lookup_ce`` honors its documented never-raises contract: any
   non-HTTP failure (limiter I/O, TLS setup, ...) is captured as
   ``LookupResult.error`` instead of propagating.
3. The unlock now happens before the lock file is closed (previously the
   ``finally`` ran ``flock`` on an already-closed fd and logged a
   spurious "I/O operation on closed file" warning on every lookup).
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from app.extensions import db
from app.models import User
from app.utils.lookup import LookupResult, RateLimiter, _NoopRateLimiter, lookup_ce


def _stub_http(text: str):
    client = mock.Mock()
    client.warm.return_value = None
    resp = mock.Mock()
    resp.text = text
    client.post.return_value = resp
    return client


class TestRateLimiterMissingDir:
    def test_acquire_creates_parent_dir(self, tmp_path):
        """A missing lock dir must not fail acquisition (the prod bug)."""
        lock = tmp_path / "nope" / "still-nope" / ".kmc_lookup_lock"
        ts = tmp_path / "nope" / "still-nope" / ".kmc_last_request_time"
        RateLimiter(lock_path=lock, timestamp_path=ts, min_gap_seconds=0).acquire()
        assert lock.exists()
        assert ts.exists()

    def test_lookup_ce_with_real_limiter_missing_dir(self, tmp_path):
        """End of the prod path: real limiter + absent dir + stub HTTP."""
        lock = tmp_path / "missing" / ".kmc_lookup_lock"
        ts = tmp_path / "missing" / ".kmc_last_request_time"
        limiter = RateLimiter(lock_path=lock, timestamp_path=ts, min_gap_seconds=0)
        out = lookup_ce("12345", rate_limiter=limiter, http_client=_stub_http('{"success": false}'))
        assert isinstance(out, LookupResult)
        assert out.found is False and out.error is None  # clean not-found, no raise


class TestLookupCeNeverRaises:
    def test_exploding_limiter_becomes_error_result(self):
        limiter = mock.Mock()
        limiter.acquire.side_effect = OSError("disk read-only")
        out = lookup_ce("12345", rate_limiter=limiter, http_client=_stub_http("{}"))
        assert out.found is False
        assert "disk read-only" in (out.error or "")

    def test_exploding_http_client_becomes_error_result(self):
        client = mock.Mock()
        client.warm.side_effect = RuntimeError("TLS boom")
        out = lookup_ce("12345", rate_limiter=_NoopRateLimiter(), http_client=client)
        assert out.found is False
        assert "TLS boom" in (out.error or "")

    def test_success_path_unchanged(self):
        out = lookup_ce(
            "K1",
            rate_limiter=_NoopRateLimiter(),
            http_client=_stub_http('{"success": true, licenseNo: [[{licNo: "K1", licClosingDate: ""}]]}'),
        )
        assert out.found is True and out.error is None
        assert out.data["identity"]["licNo"] == "K1"


@pytest.fixture()
def client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["DISABLE_RBAC"] = True

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            db.session.add(User(username="testuser", password_hash="pbkdf2:sha256$test$dummy", is_admin=True))
            db.session.commit()
        yield client
        with app.app_context():
            db.drop_all()


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


class TestLookupCeRoute:
    def test_error_result_is_json_404_not_502(self, client, monkeypatch):
        """A failed lookup must surface the message, not the 502 page."""
        _login(client)
        monkeypatch.setattr(
            "app.adjudication.routes.lookup_ce",
            lambda license_no: LookupResult(found=False, error="KMC lookup failed: down"),
        )
        resp = client.post("/adjudication/lookup_ce", json={"license_no": "K1"})
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "KMC lookup failed: down"

    def test_unexpected_raise_still_502s_as_json(self, client, monkeypatch):
        _login(client)

        def boom(license_no):
            raise RuntimeError("totally unexpected")

        monkeypatch.setattr("app.adjudication.routes.lookup_ce", boom)
        resp = client.post("/adjudication/lookup_ce", json={"license_no": "K1"})
        assert resp.status_code == 502
        assert resp.content_type.startswith("application/json")


def test_lock_paths_default_to_repo_db_dir():
    """Pin where the lock files live (regression anchor for the fix)."""
    from app.utils.lookup import _KMC_LAST_REQUEST_TIME_PATH, _KMC_LOCK_PATH

    assert _KMC_LOCK_PATH.name == ".kmc_lookup_lock"
    assert _KMC_LAST_REQUEST_TIME_PATH.name == ".kmc_last_request_time"
    assert _KMC_LOCK_PATH.parent == _KMC_LAST_REQUEST_TIME_PATH.parent
    assert isinstance(_KMC_LOCK_PATH.parent, Path)
