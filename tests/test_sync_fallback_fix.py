"""Regression tests for the sync-fallback / Redis-backend fix (2026-08-26).

Covers three interlocking bugs that caused "generate case files" to silently
fail with a 500:

1. ``_run_task_inline`` must call ``Task.run()`` directly, NOT ``.apply()``,
   because ``.apply()`` stores results in Celery's Redis backend, which
   crashes on ``rediss://`` URLs lacking ``ssl_cert_reqs``.

2. ``_normalize_redis_url`` — unit tests for the URL normalizer that adds
   ``ssl_cert_reqs=CERT_REQUIRED`` to ``rediss://`` URLs.

3. ``generate_case_file_route`` sync path — an end-to-end test that forces
   the sync fallback (QStash not configured) and verifies the route returns
   a proper HTTP response (200 or 500 with a *PDF* error, never the Redis
   ``ssl_cert_reqs`` ValueError).
"""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.qstash_client import _run_task_inline
from celery_app import _normalize_redis_url

# --------------------------------------------------------------------------- #
# 1. _normalize_redis_url
# --------------------------------------------------------------------------- #


class TestNormalizeRedisUrl:
    """Unit tests for the rediss:// URL normalizer."""

    def test_rediss_without_ssl_cert_reqs_gets_ssl_cert_reqs(self):
        url = "rediss://default:secret@host:6379/0"
        result = _normalize_redis_url(url)
        assert "ssl_cert_reqs" in result
        assert "2" in result  # ssl.CERT_REQUIRED == 2

    def test_rediss_with_existing_ssl_cert_reqs_is_preserved(self):
        url = "rediss://default:secret@host:6379/0?ssl_cert_reqs=CERT_REQUIRED"
        result = _normalize_redis_url(url)
        assert result == url

    def test_plain_redis_url_unchanged(self):
        url = "redis://localhost:6379/0"
        assert _normalize_redis_url(url) == url

    def test_empty_url_unchanged(self):
        assert _normalize_redis_url("") == ""

    def test_rediss_with_existing_query_param(self):
        url = "rediss://default:secret@host:6379/0?ssl_cert_reqs=CERT_NONE"
        result = _normalize_redis_url(url)
        assert result == url  # already has ssl_cert_reqs

    def test_rediss_without_existing_query_appends(self):
        url = "rediss://default:secret@host:6379/0"
        result = _normalize_redis_url(url)
        assert "?ssl_cert_reqs=" in result


# --------------------------------------------------------------------------- #
# 2. _run_task_inline
# --------------------------------------------------------------------------- #


class TestRunTaskInline:
    """_run_task_inline must use Task.run(), not Task.apply()."""

    def test_calls_run_not_apply_on_celery_task(self):
        """When the resolved task has a .run() method, it must be called
        directly — NOT .apply() — to avoid the Celery result-backend
        rediss:// ssl_cert_reqs crash."""
        mock_task = MagicMock()
        mock_task.run = MagicMock(return_value={"status": "ok", "file_path": "/tmp/x.zip"})
        # Ensure .apply is NOT called
        mock_task.apply = MagicMock(return_value=MagicMock(result={"status": "error"}))

        with patch("app.utils.qstash_client.resolve_task", return_value=mock_task):
            result = _run_task_inline("test_task", {"foo": "bar"})

        mock_task.run.assert_called_once_with(foo="bar")
        mock_task.apply.assert_not_called()
        assert result == {"status": "ok", "file_path": "/tmp/x.zip"}

    def test_falls_back_to_apply_for_non_celery_shim(self):
        """When the task has no .run() (e.g. _SyncFunc), use .apply()."""
        mock_task = MagicMock()
        # Remove .run so hasattr check fails
        del mock_task.run
        mock_task.apply = MagicMock(return_value=MagicMock(result={"status": "ok"}))

        with patch("app.utils.qstash_client.resolve_task", return_value=mock_task):
            result = _run_task_inline("test_task", {"foo": "bar"})

        mock_task.apply.assert_called_once_with(kwargs={"foo": "bar"})
        assert result == {"status": "ok"}


# --------------------------------------------------------------------------- #
# 3. End-to-end: sync fallback of generate_case_file_route
# --------------------------------------------------------------------------- #

# Reusable valid form data for case-file generation
_VALID_FORM_DATA = {
    "case_number": "2026/FSS/999",
    "food_safety_officer_name": "Test Officer",
    "authorization_date": "2026-07-01",
    "sample_draw_date": "2026-07-02",
    "sample_draw_time": "12:40",
    "manufacturer_fssai_license": "10012345678901",
    "manufacturer_person_name": "Mfg",
    "manufacturer_trade_name": "Mfg FBO",
    "manufacturer_address": "Addr",
    "retailer_fssai_license": "20012345678901",
    "retailer_person_name": "Ret",
    "retailer_trade_name": "Ret FBO",
    "retailer_address": "Addr",
    "product_name": "Product",
    "batch_no": "B1",
    "sample_quantity": "1000g",
    "packet_count": "4",
    "mfg_date": "2026-01-01",
    "expiry_date": "2026-12-31",
    "sample_code": "SL003",
    "sample_submission_date": "2026-07-03",
    "lab_registration_no": "WB/FOOD/2025/001",
    "do_receipt_date": "2026-07-04",
    "analyst_report_no": "PK/1",
    "analyst_report_date": "2026-07-05",
    "directive_letter_no": "H/FSSA/1",
    "directive_letter_date": "2026-07-06",
    "retailer_report_receive_date": "2026-07-07",
    "manufacturer_report_receive_date": "2026-07-08",
}


class TestCaseFileSyncFallback:
    """When QStash is not configured, generate_case_file_route must still
    return a proper HTTP response — not a 500 caused by the Redis backend
    ssl_cert_reqs ValueError crashing Celery's .apply()."""

    @pytest.fixture
    def app_client(self):
        from werkzeug.security import generate_password_hash

        from app import create_app
        from app.extensions import db
        from app.models import User

        app = create_app()
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["WTF_CSRF_ENABLED"] = False

        with app.app_context():
            db.create_all()
            db.session.add(User(username="synctest", password_hash=generate_password_hash("x")))
            db.session.commit()

        with app.test_client() as client:
            yield client

        with app.app_context():
            db.drop_all()

    def test_sync_mode_returns_200_not_redis_error(self, app_client):
        """Force sync fallback and verify the response is NOT the Redis
        ssl_cert_reqs ValueError.  We stub the PDF task to return success
        so we can assert on the 200 path."""
        from app.case_file_generator import routes as cfr

        fake_result = {
            "case_file_id": 1,
            "file_path": "pdfs/case_files/2026/08/case_1.zip",
            "status": "ok",
            "error": None,
            "generated_at": "2026-08-26T14:00:00+00:00",
        }

        # Stub publish_task to return sync mode (bypasses QStash entirely)
        with patch.object(cfr, "publish_task", return_value={"mode": "sync", "result": fake_result}):
            with app_client.session_transaction() as sess:
                sess["_user_id"] = "1"
                sess["_fresh"] = True

            resp = app_client.post("/case_file_generator/generate_case_file", data=_VALID_FORM_DATA)

        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["message"] == "Case file created; PDF generated"
        assert body["case_file_id"] == 1
        assert body["pdf_result"]["status"] == "ok"

    def test_sync_mode_task_error_returns_500_not_redis_error(self, app_client):
        """When the task returns an error status, the route must return 500
        with the task's error — NOT the Redis ssl_cert_reqs crash."""
        from app.case_file_generator import routes as cfr

        error_result = {
            "case_file_id": 1,
            "file_path": None,
            "status": "error",
            "error": "PDF assembly failed: WeasyPrint not available",
            "generated_at": "2026-08-26T14:00:00+00:00",
        }

        with patch.object(cfr, "publish_task", return_value={"mode": "sync", "result": error_result}):
            with app_client.session_transaction() as sess:
                sess["_user_id"] = "1"
                sess["_fresh"] = True

            resp = app_client.post("/case_file_generator/generate_case_file", data=_VALID_FORM_DATA)

        assert resp.status_code == 500
        body = resp.get_json()
        assert "ssl_cert_reqs" not in body["error"]
        assert "rediss" not in body["error"]
        assert "WeasyPrint" in body["error"]

    def test_actual_sync_fallback_does_not_crash_on_redis(self, app_client):
        """Force the real sync fallback (qstash_configured=False) and verify
        that _run_task_inline does not crash with the Redis ssl_cert_reqs
        ValueError.  The task may return an error (e.g. WeasyPrint missing)
        but the route must still return a proper JSON response."""

        with patch("app.utils.qstash_client.qstash_configured", return_value=False):
            with app_client.session_transaction() as sess:
                sess["_user_id"] = "1"
                sess["_fresh"] = True

            resp = app_client.post("/case_file_generator/generate_case_file", data=_VALID_FORM_DATA)

        # Must NOT be a 500 with the Redis ssl_cert_reqs error
        body_text = resp.get_data(as_text=True)
        assert "ssl_cert_reqs" not in body_text, f"Redis ssl_cert_reqs error leaked into response: {body_text[:300]}"
        assert "rediss://" not in body_text, f"rediss:// error leaked into response: {body_text[:300]}"

        # Should be 200 (success) or 500 (PDF error) — either is acceptable
        # as long as it's not the Redis crash
        assert resp.status_code in (200, 500), f"Unexpected status {resp.status_code}: {body_text[:300]}"
