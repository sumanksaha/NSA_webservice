"""Tests for validated petition-PDF download.

Seams:
  GET /case_file_generator/case/<id>/pdf/petition
  GET /adjudication/case/<id>/pdf/petition

Both endpoints validate that every required field made it into the
rendered petition (400 + missing_fields) before serving the PDF.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import create_app
from app.models import Adjudication, CaseFile


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with app.app_context():
            from werkzeug.security import generate_password_hash

            from app.extensions import db
            from app.models.auth import User

            existing = User.query.filter_by(username="pdfuser").first()
            if existing:
                db.session.delete(existing)
                db.session.commit()

            user = User(username="pdfuser", password_hash=generate_password_hash("pdfpass"), is_admin=True)
            db.session.add(user)
            db.session.commit()
            c.post("/auth/login", data={"username": "pdfuser", "password": "pdfpass"}, follow_redirects=True)
            yield c


def _make_case_file(db, **overrides):
    fields = {
        "case_number": "PDF-SAMPLE-001",
        "food_safety_officer_name": "PDF Officer",
        "authorization_date": date(2023, 4, 1),
        "inspection_date": date(2023, 3, 1),
        "inspection_time": "12:40",
        "manufacturer_fssai": "10012345678901",
        "manufacturer_name": "PDF Manufacturer",
        "manufacturer_fbo_name": "PDF Mfg FBO",
        "manufacturer_address": "PDF Mfg Address",
        "retailer_fssai": "20012345678901",
        "retailer_name": "PDF Retailer",
        "retailer_fbo_name": "PDF Ret FBO",
        "retailer_address": "PDF Ret Address",
        "product_name": "PDF Product",
        "batch_no": "PDF-BATCH-1",
        "sample_quantity": "1000g",
        "packet_count": 4,
        "mfg_date": date(2023, 1, 1),
        "expiry_date": date(2024, 1, 1),
        "sample_code": "PDF-SL-001",
        "sample_submission_date": date(2023, 3, 2),
        "Lab_Registration_No": "WB/FOOD/2023/001",
        "do_receipt_date": date(2023, 3, 4),
        "is_misbranded": True,
        "is_substandard": False,
        "analyst_report_no": "PDF/AR/1",
        "analyst_report_date": date(2023, 3, 10),
        "directive_letter_no": "PDF/DL/1",
        "directive_letter_date": date(2023, 3, 12),
        "retailer_report_receive_date": date(2023, 3, 14),
        "manufacturer_report_receive_date": date(2023, 3, 15),
    }
    fields.update(overrides)
    case = CaseFile(**fields)
    db.session.add(case)
    db.session.commit()
    return case


def _make_adjudication(db, **overrides):
    fields = {
        "case_number": "PDF-ADJ-001",
        "food_safety_officer": "PDF Officer",
        "non_license": "no",
        "pre_authorization": "no",
        "complaint_lodged": "no",
        "fbo_owner": "PDF Owner",
        "fbo_name": "PDF FBO",
        "fbo_address": "PDF Address",
        "fssai_license": "12345678901234",
        "First_inspection_date": date(2023, 1, 15),
        "compliance_deadline": date(2023, 2, 15),
        "inspection_date": date(2023, 1, 20),
        "authorization_date": date(2023, 1, 25),
    }
    fields.update(overrides)
    adj = Adjudication(**fields)
    db.session.add(adj)
    db.session.commit()
    return adj


# ── Case-file petition PDF ─────────────────────────────────────────────


class TestCaseFilePetitionPdf:
    """Seam: GET /case_file_generator/case/<id>/pdf/petition"""

    def test_valid_case_returns_pdf(self, client):
        from app.extensions import db

        with client.application.app_context():
            case = _make_case_file(db)
            case_id = case.id
        try:
            resp = client.get(f"/case_file_generator/case/{case_id}/pdf/petition")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.get_data(as_text=True)[:300]}"
            assert resp.content_type == "application/pdf", f"Expected PDF, got {resp.content_type}"
            assert resp.data[:4] == b"%PDF", "Response body must be a PDF document"
        finally:
            with client.application.app_context():
                db.session.delete(db.session.get(CaseFile, case_id))
                db.session.commit()

    def test_blank_field_returns_400_with_missing_fields(self, client):
        from app.extensions import db

        with client.application.app_context():
            case = _make_case_file(db, product_name="")
            case_id = case.id
        try:
            resp = client.get(f"/case_file_generator/case/{case_id}/pdf/petition")
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
            body = resp.get_json()
            assert "product_name" in body["missing_fields"], f"Must flag product_name: {body}"
        finally:
            with client.application.app_context():
                db.session.delete(db.session.get(CaseFile, case_id))
                db.session.commit()

    def test_zero_packet_count_flagged(self, client):
        from app.extensions import db

        with client.application.app_context():
            case = _make_case_file(db, packet_count=0)
            case_id = case.id
        try:
            resp = client.get(f"/case_file_generator/case/{case_id}/pdf/petition")
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
            body = resp.get_json()
            assert "packet_count" in body["missing_fields"], f"Must flag packet_count: {body}"
        finally:
            with client.application.app_context():
                db.session.delete(db.session.get(CaseFile, case_id))
                db.session.commit()

    def test_unknown_case_returns_json_404(self, client):
        resp = client.get("/case_file_generator/case/999999/pdf/petition")
        assert resp.status_code == 404
        assert resp.content_type == "application/json", f"Expected JSON 404, got {resp.content_type}"
        assert resp.get_json()["error"] == "Case not found"


# ── Adjudication petition PDF ──────────────────────────────────────────


class TestAdjudicationPetitionPdf:
    """Seam: GET /adjudication/case/<id>/pdf/petition"""

    def test_valid_case_returns_pdf(self, client):
        from app.extensions import db

        with client.application.app_context():
            adj = _make_adjudication(db)
            adj_id = adj.id
        try:
            resp = client.get(f"/adjudication/case/{adj_id}/pdf/petition")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.get_data(as_text=True)[:300]}"
            assert resp.content_type == "application/pdf", f"Expected PDF, got {resp.content_type}"
            assert resp.data[:4] == b"%PDF", "Response body must be a PDF document"
        finally:
            with client.application.app_context():
                db.session.delete(db.session.get(Adjudication, adj_id))
                db.session.commit()

    def test_blank_field_returns_400_with_missing_fields(self, client):
        from app.extensions import db

        with client.application.app_context():
            adj = _make_adjudication(db, fbo_name="")
            adj_id = adj.id
        try:
            resp = client.get(f"/adjudication/case/{adj_id}/pdf/petition")
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
            body = resp.get_json()
            assert "fbo_name" in body["missing_fields"], f"Must flag fbo_name: {body}"
        finally:
            with client.application.app_context():
                db.session.delete(db.session.get(Adjudication, adj_id))
                db.session.commit()

    def test_pre_authorization_has_no_petition(self, client):
        from app.extensions import db

        with client.application.app_context():
            adj = _make_adjudication(db, pre_authorization="yes", authorization_date=None)
            adj_id = adj.id
        try:
            resp = client.get(f"/adjudication/case/{adj_id}/pdf/petition")
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
        finally:
            with client.application.app_context():
                db.session.delete(db.session.get(Adjudication, adj_id))
                db.session.commit()

    def test_section_63_uses_trade_license(self, client):
        """Section-63 case renders Trade License No even when non_license=no."""
        from app.extensions import db

        with client.application.app_context():
            adj = _make_adjudication(
                db, section_63="yes", ce_license_no="KMC-123", fssai_license=""
            )
            adj_id = adj.id
        try:
            resp = client.get(f"/adjudication/case/{adj_id}/pdf/petition")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.get_data(as_text=True)[:300]}"
        finally:
            with client.application.app_context():
                db.session.delete(db.session.get(Adjudication, adj_id))
                db.session.commit()

    def test_section_63_missing_trade_license_flagged(self, client):
        from app.extensions import db

        with client.application.app_context():
            adj = _make_adjudication(
                db, section_63="yes", ce_license_no="", fssai_license="12345678901234"
            )
            adj_id = adj.id
        try:
            resp = client.get(f"/adjudication/case/{adj_id}/pdf/petition")
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
            body = resp.get_json()
            assert "ce_license_no" in body["missing_fields"], f"Must flag ce_license_no: {body}"
            assert "fssai_license" not in body.get("missing_fields", {}), (
                f"fssai_license is not rendered, must not be required: {body}"
            )
        finally:
            with client.application.app_context():
                db.session.delete(db.session.get(Adjudication, adj_id))
                db.session.commit()

    def test_unknown_case_returns_json_404(self, client):
        resp = client.get("/adjudication/case/999999/pdf/petition")
        assert resp.status_code == 404
        assert resp.content_type == "application/json", f"Expected JSON 404, got {resp.content_type}"
        assert resp.get_json()["error"] == "Case not found"
