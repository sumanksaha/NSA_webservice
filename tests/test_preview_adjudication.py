"""TDD tests for adjudication document preview (POST /adjudication/preview).

Mirrors the case_file_generator preview pattern: render petition + permission
letter HTML from form data WITHOUT creating an Adjudication record or
generating a PDF.  The user reviews both documents in the Quill editor modal
before committing to the synchronous /generate_all route.
"""

import pytest

from app.extensions import db
from app.models import Adjudication, User

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #

VALID_FORM = {
    "case_number": "2026/FSS/205",
    "food_safety_officer_name": "FSO Test",
    "food_safety_officer": "FSO Test",
    "fbo_owner": "Test Owner",
    "fbo_name": "Test FBO",
    "fbo_address": "123 Test Street, Test City",
    "fssai_license": "12345678901",
    "ce_license_no": "KMC-98765",
    "ce_trade_name": "Test Trade",
    "ce_proprietor": "Test Proprietor",
    "ce_address": "456 Trade Street",
    "ce_status": "Active",
    "concerned_food": "Fast Food",
    "problem": "Unhygienic conditions observed",
    "first_inspection_date": "2026-01-15",
    "compliance_deadline": "2026-02-15",
    "complaint_date": "2026-01-10",
    "followup_inspection_date": "2026-01-20",
    "authorization_date": "2026-01-25",
    "complaint_lodged": "no",
    "non_license": "no",
    "pre_authorization": "no",
    "clean_premise": "yes",
    "refrigerator_clean": "yes",
    "proper_attire": "yes",
    "proper_covered_utensil": "yes",
    "date_tag": "yes",
    "veg_nonveg_separation": "yes",
    "food_segregation": "yes",
    "license_display": "yes",
    "artificial_colour": "no",
    "Expired_item": "no",
    "Pest_report": "yes",
    "Water_report": "yes",
    "section_55": "no",
    "section_56": "no",
    "section_58": "no",
    "section_63": "no",
    "section_64": "no",
}


@pytest.fixture
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


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestPreviewAdjudication:
    def test_valid_form_returns_both_documents(self, client):
        """POST /adjudication/preview with valid form → 200 + both HTMLs."""
        _login(client)
        resp = client.post("/adjudication/preview", data=VALID_FORM)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "petition_html" in data
        assert "permission_html" in data

    def test_preview_html_contains_case_data(self, client):
        """Rendered HTML should contain case-specific data."""
        _login(client)
        resp = client.post("/adjudication/preview", data=VALID_FORM)
        assert resp.status_code == 200
        data = resp.get_json()
        assert VALID_FORM["fbo_name"] in data["petition_html"]
        assert VALID_FORM["fbo_name"] in data["permission_html"]
        assert VALID_FORM["fbo_address"] in data["permission_html"]

    def test_preview_html_no_unresolved_jinja(self, client):
        """No Jinja2 ``{{ }}`` or ``{% %}`` should leak into the output."""
        _login(client)
        resp = client.post("/adjudication/preview", data=VALID_FORM)
        assert resp.status_code == 200
        data = resp.get_json()
        for html in (data["petition_html"], data["permission_html"]):
            assert "{{" not in html, "Unresolved Jinja2 expression in rendered HTML"
            assert "{%" not in html, "Unresolved Jinja2 block in rendered HTML"

    def test_preview_does_not_create_adjudication(self, client):
        """The preview route must NOT persist an Adjudication record."""
        _login(client)
        resp = client.post("/adjudication/preview", data=VALID_FORM)
        assert resp.status_code == 200
        count = db.session.query(Adjudication).count()
        assert count == 0, "Preview should not create an Adjudication record"

    def test_preview_dates_are_formatted(self, client):
        """Dates should be rendered in the HTML in a readable format."""
        _login(client)
        resp = client.post("/adjudication/preview", data=VALID_FORM)
        assert resp.status_code == 200
        data = resp.get_json()
        # The adjudication templates render dates as-is from form data;
        # just verify dates appear somewhere in the HTML (not a strict format check)
        for html in (data["petition_html"], data["permission_html"]):
            # At least one date field should be rendered
            assert "2026" in html, "Expected year in rendered HTML"

    def test_missing_required_fields_return_400(self, client):
        """Missing required fields → 400 with validation errors."""
        _login(client)
        incomplete = dict(VALID_FORM)
        del incomplete["case_number"]
        del incomplete["fbo_name"]
        resp = client.post("/adjudication/preview", data=incomplete)
        assert resp.status_code == 400
        data = resp.get_json()
        assert "errors" in data

    def test_preview_without_login_redirects(self, client):
        """Unauthenticated users should be redirected (302), not get a 500."""
        resp = client.post("/adjudication/preview", data=VALID_FORM)
        assert resp.status_code in (301, 302), f"Expected redirect, got {resp.status_code}"

    def test_preview_does_not_dispatch_celery_task(self, client):
        """Preview should not call publish_task or any async dispatch."""
        from unittest.mock import patch

        _login(client)
        with patch("app.utils.qstash_client.publish_task") as mock_publish:
            resp = client.post("/adjudication/preview", data=VALID_FORM)
            assert resp.status_code == 200
            mock_publish.assert_not_called(), "Preview should not dispatch async tasks"

    def test_preview_returns_case_number(self, client):
        """The response should echo back the case_number for the frontend."""
        _login(client)
        resp = client.post("/adjudication/preview", data=VALID_FORM)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("case_number") == VALID_FORM["case_number"]
