"""Regression tests for sample-adjudication form visibility.

Covers the 2026-09 RCM-widget breakage (commit 796808d):

- The ``Product & Sample Details`` group (``rcm_details_wrap``) was hidden
  by default and the toggle only ever showed it when RCM was *checked*
  (inverted) — product fields were invisible for normal packaged-food
  cases, although the server requires them.
- The edit page carried a literal ``\\</script`` that never closed the
  inline RCM script, so the Save button and the submit wiring were
  swallowed as script raw-text.

Both are render-level faults, so these tests assert on the rendered HTML
of the same routes the browser hits.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.extensions import db
from app.models import CaseFile, User


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
            dt = datetime(2026, 1, 10)
            db.session.add(
                CaseFile(
                    case_number="2026/FSS/700",
                    food_safety_officer_name="FSO Seven",
                    inspection_date=dt,
                    inspection_time="10:30",
                    manufacturer_fssai="10012043001234",
                    manufacturer_name="Mfr",
                    manufacturer_fbo_name="Mfr FBO",
                    manufacturer_address="Mfr Addr",
                    retailer_fssai="10012043005678",
                    retailer_name="Ret",
                    retailer_fbo_name="Ret FBO",
                    retailer_address="Ret Addr",
                    product_name="Mustard Oil",
                    sample_quantity="1 L",
                    packet_count=4,
                    sample_code="SC-7",
                    Lab_Registration_No="LAB-7",
                    sample_submission_date=dt,
                    do_receipt_date=dt,
                    analyst_report_no="AR-7",
                    analyst_report_date=dt,
                    directive_letter_no="DL-7",
                    directive_letter_date=dt,
                    retailer_report_receive_date=dt,
                )
            )
            db.session.commit()
        yield client
        with app.app_context():
            db.drop_all()


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


def _case_id(client) -> int:
    with client.application.app_context():
        return db.session.query(CaseFile).first().id


class TestProductFieldsVisible:
    def test_create_form_shows_product_group_by_default(self, client):
        _login(client)
        html = client.get("/case_file_generator/").get_data(as_text=True)
        assert 'name="product_name"' in html
        assert 'id="rcm_details_wrap" style="display: none;"' not in html

    def test_edit_form_shows_product_group_by_default(self, client):
        _login(client)
        html = client.get(f"/case_file_generator/case/{_case_id(client)}/edit").get_data(as_text=True)
        assert 'name="product_name"' in html
        assert 'id="rcm_details_wrap" style="display: none;"' not in html


class TestEditSavePresent:
    def test_edit_page_has_no_unclosed_script_and_renders_save(self, client):
        _login(client)
        html = client.get(f"/case_file_generator/case/{_case_id(client)}/edit").get_data(as_text=True)
        assert "\\</script" not in html
        assert 'type="submit"' in html
