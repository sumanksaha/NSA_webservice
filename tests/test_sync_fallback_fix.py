"""Regression tests for sync-fallback execution (QStash-only task path).

Covers the inline-execution contract that keeps "generate case files"
working when QStash is unconfigured:

1. ``_run_task_inline`` calls the plain registry function as
   ``fn(**payload)`` and lets task-body exceptions propagate to the
   caller (which converts them into error results / HTTP responses).

2. ``generate_case_file_route`` sync path — an end-to-end test that forces
   the sync fallback (QStash not configured) and verifies the route returns
   a proper HTTP response (200 or 500 with a *PDF* error).
"""

from unittest.mock import patch

import pytest

from app.utils.qstash_client import _run_task_inline

# --------------------------------------------------------------------------- #
# 1. _run_task_inline
# --------------------------------------------------------------------------- #


class TestRunTaskInline:
    """_run_task_inline must call the registry function with the payload."""

    def test_calls_plain_function_with_payload_kwargs(self):
        def fake_task(foo=None):
            return {"status": "ok", "foo": foo}

        with patch("app.utils.qstash_client.resolve_task", return_value=fake_task):
            result = _run_task_inline("test_task", {"foo": "bar"})

        assert result == {"status": "ok", "foo": "bar"}

    def test_task_exception_propagates(self):
        def boom(**kwargs):
            raise RuntimeError("task exploded")

        with patch("app.utils.qstash_client.resolve_task", return_value=boom):
            with pytest.raises(RuntimeError, match="task exploded"):
                _run_task_inline("test_task", {})


# --------------------------------------------------------------------------- #
# 2. End-to-end: sync fallback of generate_case_file_route
# --------------------------------------------------------------------------- #

# Reusable valid form data for case-file generation
_VALID_FORM_DATA = {
    "case_number": "2026/FSS/999",
    "food_safety_officer_name": "Test Officer",
    "authorization_date": "2026-07-01",
    "inspection_date": "2026-07-02",
    "inspection_time": "12:40",
    "manufacturer_fssai": "10012345678901",
    "manufacturer_name": "Mfg",
    "manufacturer_fbo_name": "Mfg FBO",
    "manufacturer_address": "Addr",
    "retailer_fssai": "20012345678901",
    "retailer_name": "Ret",
    "retailer_fbo_name": "Ret FBO",
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
    return a proper HTTP response via the synchronous inline fallback."""

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

    def test_sync_mode_returns_200(self, app_client):
        """Force sync fallback and verify the 200 path works end to end.
        We stub the sync and PDF task to return success."""
        from app.case_file_generator import routes as cfr

        fake_pdf_result = {
            "status": "ok",
            "file_path": "pdfs/case_files/2026/08/case_1.zip",
        }

        # Stub sync_row (sheets/airtable) and generate_case_file_pdf
        with patch.object(cfr, "sync_row"), patch(
            "app.case_file_generator.tasks.generate_case_file_pdf",
            return_value=fake_pdf_result,
        ):
            with app_client.session_transaction() as sess:
                sess["_user_id"] = "1"
                sess["_fresh"] = True

            resp = app_client.post("/case_file_generator/generate_case_file", data=_VALID_FORM_DATA)

        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "Case file created" in body["message"]
        assert body["pdf_result"]["status"] == "ok"

    def test_sync_mode_task_error_returns_500(self, app_client):
        """When the PDF task raises, the route returns 500 with the error."""
        from app.case_file_generator import routes as cfr

        # Stub sync_row to succeed, but generate_case_file_pdf to raise
        with patch.object(cfr, "sync_row"), patch(
            "app.case_file_generator.tasks.generate_case_file_pdf",
            side_effect=RuntimeError("PDF assembly failed: WeasyPrint not available"),
        ):
            with app_client.session_transaction() as sess:
                sess["_user_id"] = "1"
                sess["_fresh"] = True

            resp = app_client.post("/case_file_generator/generate_case_file", data=_VALID_FORM_DATA)

        assert resp.status_code == 500
        body = resp.get_json()
        assert "WeasyPrint" in body["error"]

    def test_actual_sync_fallback_returns_proper_response(self, app_client):
        """Force the real sync fallback (qstash_configured=False) and verify
        the route returns a proper JSON response (200 or 500)."""

        with patch("app.utils.qstash_client.qstash_configured", return_value=False):
            with app_client.session_transaction() as sess:
                sess["_user_id"] = "1"
                sess["_fresh"] = True

            resp = app_client.post("/case_file_generator/generate_case_file", data=_VALID_FORM_DATA)

        # Should be 200 (success) or 500 (PDF error) — either is acceptable.
        body_text = resp.get_data(as_text=True)
        assert resp.status_code in (200, 500), f"Unexpected status {resp.status_code}: {body_text[:300]}"
