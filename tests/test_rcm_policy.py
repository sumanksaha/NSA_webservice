"""Tests for the RCM case-field policy seam (app/shared/rcm_policy.py).

One domain flag, one home: which fields RCM exempts, how they are
blanked for save, and which input ids the JS visibility toggle owns.
Routes, templates, and the import path all read this seam, so a missing
carve-out fails here instead of on a live form.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.extensions import db
from app.models import CaseFile, User
from app.shared import rcm_policy


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


class TestIsRcm:
    def test_truthy_forms(self):
        for raw in ("on", "ON", " on ", "true", "True", "1", "yes", "Yes"):
            assert rcm_policy.is_rcm({"retailer_cum_manufacturer": raw}) is True

    def test_falsy_forms(self):
        for raw in ("", "off", "false", "0", "no", None):
            assert rcm_policy.is_rcm({"retailer_cum_manufacturer": raw}) is False
        assert rcm_policy.is_rcm({}) is False


class TestExemptSets:
    def test_exempt_fields_are_the_eight(self):
        assert frozenset({
            "manufacturer_fssai",
            "manufacturer_name",
            "manufacturer_fbo_name",
            "manufacturer_address",
            "manufacturer_report_receive_date",
            "batch_no",
            "mfg_date",
            "expiry_date",
        }) == rcm_policy.EXEMPT_FIELDS

    def test_exempt_date_fields_are_a_subset(self):
        assert rcm_policy.EXEMPT_DATE_FIELDS <= rcm_policy.EXEMPT_FIELDS
        assert frozenset({
            "mfg_date",
            "expiry_date",
            "manufacturer_report_receive_date",
        }) == rcm_policy.EXEMPT_DATE_FIELDS

    def test_toggle_ids_cover_exempt_fields(self):
        assert set(rcm_policy.TOGGLE_FIELD_IDS) == set(rcm_policy.EXEMPT_FIELDS)


class TestStrippedForSave:
    def test_rcm_blanks_exempt_fields_to_empty_string(self):
        form = {
            "retailer_cum_manufacturer": "on",
            "product_name": "Mustard Oil",
            "manufacturer_fssai": "10012043001234",
            "manufacturer_name": "Mfr",
            "manufacturer_fbo_name": "Mfr FBO",
            "manufacturer_address": "Mfr Addr",
            "manufacturer_report_receive_date": "2026-02-01",
            "batch_no": "B1",
            "mfg_date": "2025-12-01",
            "expiry_date": "2026-12-01",
        }
        stripped = rcm_policy.stripped_for_save(form)
        for field in rcm_policy.EXEMPT_FIELDS:
            assert stripped[field] == "", field
        # Untouched fields survive, including the flag itself.
        assert stripped["product_name"] == "Mustard Oil"
        assert stripped["retailer_cum_manufacturer"] == "on"

    def test_stripped_does_not_mutate_input(self):
        form = {"retailer_cum_manufacturer": "on", "batch_no": "B1"}
        rcm_policy.stripped_for_save(form)
        assert form["batch_no"] == "B1"

    def test_non_rcm_returns_equal_copy(self):
        form = {"batch_no": "B1", "product_name": "Oil"}
        stripped = rcm_policy.stripped_for_save(form)
        assert stripped == form and stripped is not form


class TestTemplateAgreement:
    """The JS visibility toggles must own exactly the exempt field ids."""

    def test_index_toggle_lists_every_exempt_id(self, client):
        _login(client)
        html = client.get("/case_file_generator/").get_data(as_text=True)
        for field in rcm_policy.TOGGLE_FIELD_IDS:
            assert f'"{field}"' in html, field

    def test_edit_toggle_lists_every_exempt_id(self, client):
        _login(client)
        dt = datetime(2026, 1, 10)
        with client.application.app_context():
            case = CaseFile(
                case_number="2026/FSS/920",
                food_safety_officer_name="FSO RCM",
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
                product_name="Oil",
                sample_quantity="1 L",
                packet_count=4,
                sample_code="SC-920",
                Lab_Registration_No="LAB-920",
                sample_submission_date=dt,
                do_receipt_date=dt,
                analyst_report_no="AR-920",
                analyst_report_date=dt,
                directive_letter_no="DL-920",
                directive_letter_date=dt,
                retailer_report_receive_date=dt,
            )
            db.session.add(case)
            db.session.commit()
            case_id = case.id
        html = client.get(f"/case_file_generator/case/{case_id}/edit").get_data(as_text=True)
        for field in rcm_policy.TOGGLE_FIELD_IDS:
            assert f'"{field}"' in html, field
