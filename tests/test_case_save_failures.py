"""Regression tests for case/adjudication save failures.

Covers the production report: saving a case showed
``JSON.parse: unexpected character at line 1 column 1 of the JSON data —
the case file may still have been created`` and the entered data
(including dates) was lost.

Root causes fixed alongside these tests:

1. ``generate_case_file_route`` / adjudication ``generate_all`` only caught
   ``StaleDataError`` around ``db.session.commit()``. Any other persistence
   failure (e.g. an ``IntegrityError`` from a stale sample link on
   Postgres) escaped as an HTML 500 page, and the fetch-driven forms parse
   every response as JSON — hence the raw ``JSON.parse`` SyntaxError. Both
   routes now return a JSON 500 for any commit failure.
2. ``apply_case_file_update`` never wrote back the retailer identity,
   sample quantity, or packet count edited on the edit page (the form
   sent them and PUT validation required them, but the row kept stale
   values).
3. The adjudication create form rendered the KMC trade-license details
   without ``name`` attributes, so ``ce_*`` values were never submitted.
"""

from __future__ import annotations

from unittest import mock

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Adjudication, CaseFile, User
from tests.test_preview_adjudication import VALID_FORM as ADJ_VALID_FORM
from tests.test_sync_fallback_fix import _VALID_FORM_DATA as CASE_VALID_FORM


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


def _stub_case_file_pdf(monkeypatch):
    monkeypatch.setattr(
        "app.case_file_generator.tasks.generate_case_file_pdf",
        lambda case_file_id, case_data: {"status": "ok", "file_path": "x.zip"},
    )
    monkeypatch.setattr("app.case_file_generator.routes.sync_row", mock.Mock())


def _stub_adjudication_pdf(monkeypatch):
    monkeypatch.setattr(
        "app.adjudication.routes.generate_pdf_from_html",
        lambda html: (b"%PDF-1.4 fake", None),
    )
    monkeypatch.setattr("app.adjudication.routes.post_process_pdf_html", lambda html, adjudication_id=None: html)
    monkeypatch.setattr("app.adjudication.routes.embed_photos_as_base64", lambda paths: {})
    monkeypatch.setattr("app.adjudication.routes.sync_row", mock.Mock())


class TestSaveCommitFailuresReturnJson:
    def test_case_file_commit_integrity_error_is_json_500(self, client, monkeypatch):
        """A DB failure on save must be JSON, never an HTML 500 page.

        The create form parses the body as JSON unconditionally; an HTML
        error page is what produced the reported
        ``JSON.parse: unexpected character`` message.
        """
        _login(client)
        _stub_case_file_pdf(monkeypatch)
        boom = IntegrityError("INSERT INTO case_files", {}, Exception("FOREIGN KEY constraint failed"))
        monkeypatch.setattr(db.session, "commit", mock.Mock(side_effect=boom))

        resp = client.post("/case_file_generator/generate_case_file", data=dict(CASE_VALID_FORM))

        assert resp.status_code == 500
        assert resp.content_type.startswith("application/json")
        body = resp.get_json()
        assert "Could not save the case file" in body["error"]

    def test_adjudication_commit_integrity_error_is_json_500(self, client, monkeypatch):
        _login(client)
        _stub_adjudication_pdf(monkeypatch)
        boom = IntegrityError("INSERT INTO adjudications", {}, Exception("constraint failed"))
        monkeypatch.setattr(db.session, "commit", mock.Mock(side_effect=boom))

        resp = client.post("/adjudication/generate_all", data=dict(ADJ_VALID_FORM))

        assert resp.status_code == 500
        assert resp.content_type.startswith("application/json")
        body = resp.get_json()
        assert "Could not save the adjudication" in body["error"]


class TestCaseFileEditPersistsAllFields:
    def test_put_persists_retailer_quantity_and_packet_count(self, client, monkeypatch):
        """Retailer identity, sample quantity, and packet count edited on
        the edit page must be written back (previously silently dropped)."""
        _login(client)
        _stub_case_file_pdf(monkeypatch)
        client.post("/case_file_generator/generate_case_file", data=dict(CASE_VALID_FORM))
        with client.application.app_context():
            case_id = CaseFile.query.one().id

        updated = dict(
            CASE_VALID_FORM,
            retailer_fssai="20099999999999",
            retailer_name="New Retailer",
            retailer_fbo_name="New Retailer FBO",
            retailer_address="New Retailer Addr",
            sample_quantity="2 L",
            packet_count="8",
        )
        resp = client.put(f"/case_file_generator/case/{case_id}", data=updated)
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with client.application.app_context():
            case = db.session.get(CaseFile, case_id)
            assert case.retailer_fssai == "20099999999999"
            assert case.retailer_name == "New Retailer"
            assert case.retailer_fbo_name == "New Retailer FBO"
            assert case.retailer_address == "New Retailer Addr"
            assert case.sample_quantity == "2 L"
            assert case.packet_count == 8


class TestAdjudicationCreatePersistsCeFields:
    def test_generate_all_persists_trade_license_details(self, client, monkeypatch):
        """KMC trade-license details entered on the create form must reach
        the record (the inputs previously lacked ``name`` attributes)."""
        _login(client)
        _stub_adjudication_pdf(monkeypatch)
        form = dict(
            ADJ_VALID_FORM,
            ce_license_no="KMC-12345",
            ce_trade_name="Trade Name",
            ce_proprietor="Proprietor",
            ce_address="Trade Addr",
            ce_status="Active",
        )
        resp = client.post("/adjudication/generate_all", data=form)
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with client.application.app_context():
            adj = Adjudication.query.one()
            assert adj.ce_license_no == "KMC-12345"
            assert adj.ce_trade_name == "Trade Name"
            assert adj.ce_proprietor == "Proprietor"
            assert adj.ce_address == "Trade Addr"
            assert adj.ce_status == "Active"

    def test_create_page_ce_inputs_are_submitted(self, client):
        """Pin the template contract: the ce_* inputs carry names so the
        browser includes them in the POST."""
        _login(client)
        html = client.get("/adjudication/").get_data(as_text=True)
        for field in ("ce_license_no", "ce_trade_name", "ce_proprietor", "ce_address", "ce_status"):
            assert f'name="{field}"' in html, f"{field} must be a named (submitted) input"
