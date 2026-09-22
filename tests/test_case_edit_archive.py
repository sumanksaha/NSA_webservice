"""Tests for case-file + adjudication field edit and archive/unarchive.

Covers the shared routes registered by
``app.shared.document_routes.register_document_routes`` for both blueprints:

- ``GET /case/<id>/edit`` renders the prefilled edit form.
- ``PUT /case/<id>`` updates entered data (``case_number`` immutable,
  full-form validation, archived cases rejected with 409).
- ``POST /case/<id>/archive`` hides the case from ``GET /`` and
  ``GET /cases`` while keeping the row (and ``GET /case/<id>``) intact.
- ``POST /case/<id>/unarchive`` restores the case to the lists.
- Edits/archives null ``synced_at`` so Supabase ``push()`` re-upserts the
  row (``is_archived`` flows through the generic payload builder).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.extensions import db
from app.models import Adjudication, CaseFile, User


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


def _seed_case_file() -> int:
    dt = datetime(2026, 1, 10)
    case = CaseFile(
        case_number="2026/FSS/900",
        food_safety_officer_name="FSO Edit",
        authorization_date=dt,
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
        batch_no="B1",
        sample_quantity="1 L",
        packet_count=4,
        mfg_date=datetime(2025, 12, 1),
        expiry_date=datetime(2026, 12, 1),
        sample_code="SC-1",
        Lab_Registration_No="LAB-1",
        sample_submission_date=dt,
        do_receipt_date=dt,
        is_misbranded=False,
        is_substandard=True,
        analyst_report_no="AR-1",
        analyst_report_date=dt,
        directive_letter_no="DL-1",
        directive_letter_date=dt,
        retailer_report_receive_date=dt,
        manufacturer_report_receive_date=dt,
    )
    db.session.add(case)
    db.session.commit()
    return case.id


def _seed_adjudication() -> int:
    dt = datetime(2026, 1, 15)
    adj = Adjudication(
        case_number="2026/ADJ/900",
        food_safety_officer="FSO Edit",
        fbo_owner="Owner",
        fbo_name="FBO",
        fbo_address="Addr",
        fssai_license="10012043001234",
        First_inspection_date=dt,
        compliance_deadline=datetime(2026, 2, 15),
        inspection_date=datetime(2026, 1, 20),
        authorization_date=datetime(2026, 1, 25),
    )
    db.session.add(adj)
    db.session.commit()
    return adj.id


CASE_FILE_FORM = {
    "case_number": "2026/FSS/900",
    "food_safety_officer_name": "FSO Edit",
    "authorization_date": "2026-01-10",
    "inspection_date": "2026-01-10",
    "inspection_time": "10:30",
    "manufacturer_fssai": "10012043001234",
    "manufacturer_name": "Mfr Updated",
    "manufacturer_fbo_name": "Mfr FBO",
    "manufacturer_address": "Mfr Addr",
    "retailer_fssai": "10012043005678",
    "retailer_name": "Ret",
    "retailer_fbo_name": "Ret FBO",
    "retailer_address": "Ret Addr",
    "product_name": "Mustard Oil",
    "batch_no": "B1",
    "sample_quantity": "1 L",
    "packet_count": "4",
    "mfg_date": "2025-12-01",
    "expiry_date": "2026-12-01",
    "sample_code": "SC-1",
    "lab_registration_no": "LAB-1",
    "do_receipt_date": "2026-01-10",
    "analyst_report_no": "AR-1",
    "analyst_report_date": "2026-01-10",
    "directive_letter_no": "DL-1",
    "directive_letter_date": "2026-01-10",
    "retailer_report_receive_date": "2026-01-10",
    "manufacturer_report_receive_date": "2026-01-10",
}

ADJUDICATION_FORM = {
    "case_number": "2026/ADJ/900",
    "food_safety_officer_name": "FSO Edit",
    "fbo_owner": "Owner Updated",
    "fbo_name": "FBO",
    "fbo_address": "Addr",
    "fssai_license": "10012043001234",
    "first_inspection_date": "2026-01-15",
    "compliance_deadline": "2026-02-15",
    "followup_inspection_date": "2026-01-20",
    "authorization_date": "2026-01-25",
}


class TestCaseFileEditArchive:
    def test_edit_page_renders_prefilled(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_case_file()
        resp = client.get(f"/case_file_generator/case/{case_id}/edit")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "2026/FSS/900" in html
        assert "Mustard Oil" in html

    def test_put_updates_fields(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_case_file()
        resp = client.put(f"/case_file_generator/case/{case_id}", data=CASE_FILE_FORM)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        with client.application.app_context():
            case = db.session.get(CaseFile, case_id)
            assert case.manufacturer_name == "Mfr Updated"
            assert case.synced_at is None  # marked Supabase-dirty

    def test_put_rejects_case_number_change(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_case_file()
        data = dict(CASE_FILE_FORM, case_number="2026/FSS/999")
        resp = client.put(f"/case_file_generator/case/{case_id}", data=data)
        assert resp.status_code == 400

    def test_put_validates_required_fields(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_case_file()
        data = dict(CASE_FILE_FORM, product_name="")
        resp = client.put(f"/case_file_generator/case/{case_id}", data=data)
        assert resp.status_code == 400
        assert "product_name" in resp.get_json()["errors"]

    def test_put_rcm_blanks_manufacturer_fields(self, client):
        """Switching a case to RCM must blank manufacturer identity, batch,
        mfg/expiry, and the manufacturer report date — even when the form
        posts stale values for them.

        Regression: apply_case_file_update blanked the report date but an
        unconditional overwrite below restored the stale value, which then
        fed generation_gate's max() and could wrongly extend the 30-day
        embargo.
        """
        _login(client)
        with client.application.app_context():
            case_id = _seed_case_file()
        data = dict(
            CASE_FILE_FORM,
            retailer_cum_manufacturer="on",
            manufacturer_report_receive_date="2026-02-01",
            batch_no="STALE",
        )
        resp = client.put(f"/case_file_generator/case/{case_id}", data=data)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        with client.application.app_context():
            case = db.session.get(CaseFile, case_id)
            assert case.retailer_cum_manufacturer is True
            assert case.manufacturer_report_receive_date is None
            assert case.manufacturer_fssai == ""
            assert case.batch_no == ""
            assert case.mfg_date is None
            # Always-required fields survive the switch.
            assert case.product_name == "Mustard Oil"

    def test_archive_hides_from_lists_but_keeps_data(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_case_file()
        resp = client.post(f"/case_file_generator/case/{case_id}/archive")
        assert resp.status_code == 200

        # Row kept: direct fetch still works, flag set, Supabase-dirty.
        with client.application.app_context():
            case = db.session.get(CaseFile, case_id)
            assert case.is_archived is True
            assert case.archived_at is not None
            assert case.synced_at is None
        assert client.get(f"/case_file_generator/case/{case_id}").status_code == 200

        # Hidden from UI feeds by default...
        assert client.get("/case_file_generator/cases").get_json() == []
        assert b"2026/FSS/900" not in client.get("/case_file_generator/").data
        # ...but visible with the toggle.
        flagged = client.get("/case_file_generator/cases?include_archived=1").get_json()
        assert len(flagged) == 1 and flagged[0]["is_archived"] is True
        assert b"2026/FSS/900" in client.get("/case_file_generator/?include_archived=1").data

    def test_archived_case_cannot_be_edited(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_case_file()
        client.post(f"/case_file_generator/case/{case_id}/archive")
        resp = client.put(f"/case_file_generator/case/{case_id}", data=CASE_FILE_FORM)
        assert resp.status_code == 409

    def test_unarchive_restores_to_lists(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_case_file()
        client.post(f"/case_file_generator/case/{case_id}/archive")
        resp = client.post(f"/case_file_generator/case/{case_id}/unarchive")
        assert resp.status_code == 200
        assert len(client.get("/case_file_generator/cases").get_json()) == 1
        with client.application.app_context():
            case = db.session.get(CaseFile, case_id)
            assert case.is_archived is False
            assert case.archived_at is None

    def test_unknown_case_404s(self, client):
        _login(client)
        assert client.put("/case_file_generator/case/424242", data=CASE_FILE_FORM).status_code == 404
        assert client.post("/case_file_generator/case/424242/archive").status_code == 404
        assert client.post("/case_file_generator/case/424242/unarchive").status_code == 404


class TestAdjudicationEditArchive:
    def test_edit_page_renders_prefilled(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_adjudication()
        resp = client.get(f"/adjudication/case/{case_id}/edit")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "2026/ADJ/900" in html
        assert "Owner" in html

    def test_put_updates_fields(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_adjudication()
        resp = client.put(f"/adjudication/case/{case_id}", data=ADJUDICATION_FORM)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        with client.application.app_context():
            adj = db.session.get(Adjudication, case_id)
            assert adj.fbo_owner == "Owner Updated"
            assert adj.synced_at is None

    def test_put_rejects_case_number_change(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_adjudication()
        data = dict(ADJUDICATION_FORM, case_number="2026/ADJ/999")
        resp = client.put(f"/adjudication/case/{case_id}", data=data)
        assert resp.status_code == 400

    def test_put_validates_required_fields(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_adjudication()
        data = dict(ADJUDICATION_FORM, fbo_name="")
        resp = client.put(f"/adjudication/case/{case_id}", data=data)
        assert resp.status_code == 400
        assert "fbo_name" in resp.get_json()["errors"]

    def test_archive_and_unarchive_roundtrip(self, client):
        _login(client)
        with client.application.app_context():
            case_id = _seed_adjudication()
        assert client.post(f"/adjudication/case/{case_id}/archive").status_code == 200
        with client.application.app_context():
            adj = db.session.get(Adjudication, case_id)
            assert adj.is_archived is True
            assert adj.synced_at is None
        assert client.get("/adjudication/cases").get_json() == []
        assert client.get(f"/adjudication/case/{case_id}").status_code == 200
        flagged = client.get("/adjudication/cases?include_archived=1").get_json()
        assert len(flagged) == 1 and flagged[0]["is_archived"] is True

        assert client.put(f"/adjudication/case/{case_id}", data=ADJUDICATION_FORM).status_code == 409

        assert client.post(f"/adjudication/case/{case_id}/unarchive").status_code == 200
        assert len(client.get("/adjudication/cases").get_json()) == 1
        with client.application.app_context():
            adj = db.session.get(Adjudication, case_id)
            assert adj.is_archived is False

    def test_supabase_payload_carries_archive_flag(self, client):
        """The generic Supabase payload builder must include the new columns."""
        _login(client)
        with client.application.app_context():
            case_id = _seed_case_file()
            from app.sync.supabase_sync import get_sync_service

            svc = get_sync_service()
            case = db.session.get(CaseFile, case_id)
            payload = svc._model_to_payload(case, CaseFile)
            assert "is_archived" in payload and payload["is_archived"] is False
            assert "archived_at" in payload
            case.is_archived = True
            case.archived_at = datetime.now(UTC)
            payload = svc._model_to_payload(case, CaseFile)
            assert payload["is_archived"] is True
