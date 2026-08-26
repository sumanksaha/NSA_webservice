"""TDD tests for the case-file preview feature.

POST /case_file_generator/preview renders Petition + Permission Letter HTML
from form data WITHOUT creating a CaseFile record or dispatching a PDF task.
The user reviews the rendered HTML in the Quill editor before committing.
"""

import pytest

from app.extensions import db
from app.models import CaseFile, User


@pytest.fixture
def test_client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            user = User(username="testuser", password_hash="pbkdf2:sha256$test$dummy")
            db.session.add(user)
            db.session.commit()
        yield client
        with app.app_context():
            db.drop_all()


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


_VALID_FORM = {
    "case_number": "2026/FSS/104",
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
    "product_name": "Test Product",
    "batch_no": "B1",
    "sample_quantity": "1000g",
    "packet_count": "4",
    "mfg_date": "2026-01-01",
    "expiry_date": "2026-12-31",
    "sample_code": "SL001",
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


class TestPreviewCaseFile:
    """POST /case_file_generator/preview — render HTML for review."""

    def test_valid_form_returns_both_documents(self, test_client):
        """Valid form → 200 with petition_html + permission_html."""
        _login(test_client)
        resp = test_client.post("/case_file_generator/preview", data=_VALID_FORM)
        assert resp.status_code == 200
        body = resp.get_json()
        assert "petition_html" in body
        assert "permission_html" in body
        assert body["petition_html"].strip() != ""
        assert body["permission_html"].strip() != ""

    def test_preview_html_contains_case_data(self, test_client):
        """Rendered HTML reflects submitted form values."""
        _login(test_client)
        resp = test_client.post("/case_file_generator/preview", data=_VALID_FORM)
        html = resp.get_json()
        assert "2026/FSS/104" in html["petition_html"]
        assert "Test Product" in html["petition_html"]

    def test_preview_html_no_unresolved_jinja(self, test_client):
        """No leftover Jinja placeholders in rendered HTML."""
        _login(test_client)
        resp = test_client.post("/case_file_generator/preview", data=_VALID_FORM)
        html = resp.get_json()
        assert "{{" not in html["petition_html"]
        assert "{{" not in html["permission_html"]

    def test_preview_does_not_create_case_file(self, test_client):
        """Preview must NOT persist a CaseFile record."""
        _login(test_client)
        test_client.post("/case_file_generator/preview", data=_VALID_FORM)
        with test_client.application.app_context():
            assert CaseFile.query.count() == 0

    def test_preview_dates_are_formatted_indian(self, test_client):
        """Dates should be rendered in Indian format (DD-MM-YYYY), not ISO."""
        _login(test_client)
        resp = test_client.post("/case_file_generator/preview", data=_VALID_FORM)
        html = resp.get_json()
        # authorization_date is in _DATE_FIELDS → format_date_indian
        assert "01-07-2026" in html["petition_html"]
        # mfg_date is also in _DATE_FIELDS → format_date_indian
        assert "01-01-2026" in html["petition_html"]

    def test_missing_required_fields_return_400(self, test_client):
        """Empty form → 400 with structured field errors."""
        _login(test_client)
        resp = test_client.post("/case_file_generator/preview", data={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]
        assert isinstance(data["errors"], dict)
        assert "case_number" in data["errors"]
        assert "food_safety_officer_name" in data["errors"]
        assert "product_name" in data["errors"]

    def test_invalid_packet_count_flagged(self, test_client):
        """Non-integer packet_count is flagged."""
        _login(test_client)
        form = dict(_VALID_FORM, packet_count="abc")
        resp = test_client.post("/case_file_generator/preview", data=form)
        assert resp.status_code == 400
        assert "packet_count" in resp.get_json()["errors"]

    def test_invalid_date_flagged(self, test_client):
        """Malformed date is flagged."""
        _login(test_client)
        form = dict(_VALID_FORM, authorization_date="not-a-date")
        resp = test_client.post("/case_file_generator/preview", data=form)
        assert resp.status_code == 400
        assert "authorization_date" in resp.get_json()["errors"]

    def test_preview_without_login_redirects(self, test_client):
        """Unauthenticated access → redirect (302)."""
        resp = test_client.post("/case_file_generator/preview", data=_VALID_FORM)
        assert resp.status_code == 302
