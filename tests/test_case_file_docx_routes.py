"""TDD tests for case-file (sample adjudication) DOCX download.

Seam: routes.py download endpoints (/docx/petition, /docx/permission, /docx/zip).

Mirrors tests/test_adjudication_docx_routes.py: the sample-track routes must
render the ADR-001 .adoc templates (not the old boilerplate word_converter)
and must not leak unsubstituted placeholders.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date

import pytest

from app import create_app
from app.models import CaseFile


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with app.app_context():
            from app.extensions import db
            from app.models.auth import User
            from werkzeug.security import generate_password_hash

            existing = User.query.filter_by(username="tdduser").first()
            if existing:
                db.session.delete(existing)
                db.session.commit()

            user = User(username="tdduser", password_hash=generate_password_hash("tddpass"), is_admin=True)
            db.session.add(user)
            db.session.commit()
            c.post("/auth/login", data={"username": "tdduser", "password": "tddpass"}, follow_redirects=True)
            yield c


@pytest.fixture
def case_file(client):
    """Create and return a test CaseFile (sample track) record."""
    from app.extensions import db

    case = CaseFile(
        case_number="TDD-SAMPLE-001",
        food_safety_officer_name="TDD Officer",
        authorization_date=date(2023, 4, 1),
        inspection_date=date(2023, 3, 1),
        inspection_time="12:40",
        manufacturer_fssai="10012345678901",
        manufacturer_name="TDD Manufacturer",
        manufacturer_fbo_name="TDD Mfg FBO",
        manufacturer_address="TDD Mfg Address",
        retailer_fssai="20012345678901",
        retailer_name="TDD Retailer",
        retailer_fbo_name="TDD Ret FBO",
        retailer_address="TDD Ret Address",
        product_name="TDD Product",
        batch_no="TDD-BATCH-1",
        sample_quantity="1000g",
        packet_count=4,
        mfg_date=date(2023, 1, 1),
        expiry_date=date(2024, 1, 1),
        other_food_articles="Biscuits, Chips",
        total_cost="250",
        cost_in_words="Two Hundred Fifty Rupees Only",
        sample_code="TDD-SL-001",
        sample_submission_date=date(2023, 3, 2),
        Lab_Registration_No="WB/FOOD/2023/001",
        do_receipt_date=date(2023, 3, 4),
        is_misbranded=True,
        is_substandard=False,
        analyst_report_no="TDD/AR/1",
        analyst_report_date=date(2023, 3, 10),
        directive_letter_no="TDD/DL/1",
        directive_letter_date=date(2023, 3, 12),
        retailer_report_receive_date=date(2023, 3, 14),
        manufacturer_report_receive_date=date(2023, 3, 15),
    )
    db.session.add(case)
    db.session.commit()
    yield case
    db.session.delete(case)
    db.session.commit()


# ── Helpers ──────────────────────────────────────────────────────────────


def _extract_docx_text(docx_bytes: bytes) -> str:
    """Plain text from DOCX bytes (strip XML, collapse whitespace)."""
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf, zf.open("word/document.xml") as f:
        xml = f.read().decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", " ", xml)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _brace_placeholders(text: str) -> list[str]:
    """Return all {word} or {{word}} tokens in text."""
    return re.findall(r"\{[\w\s]+\}|\{\{[\w\s]+\}\}", text)


# ── Petition DOCX ────────────────────────────────────────────────────────


class TestPetitionDocxDownload:
    """Seam: GET /case_file_generator/case/<id>/docx/petition"""

    def test_returns_200(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_docx_mimetype(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        assert response.content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document", (
            f"Expected .docx mimetype, got {response.content_type}"
        )

    def test_docx_is_valid_zip(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        assert response.data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"

    def test_docx_has_word_document_xml(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        assert "word/document.xml" in names, f"Missing word/document.xml in DOCX. Contents: {names}"

    def test_docx_contains_case_number(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "TDD-SAMPLE-001" in text, "Petition DOCX must contain the case_number value"

    def test_docx_contains_officer_name(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "TDD Officer" in text, "Petition DOCX must contain the food_safety_officer_name value"

    def test_docx_contains_product_name(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "TDD Product" in text, "Petition DOCX must contain the product_name value"

    def test_docx_contains_repaired_sections(self, client, case_file):
        """REGRESSION: petition.adoc was truncated — GROUNDS/PRAYER must render."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "GROUNDS" in text, "Petition DOCX must contain the GROUNDS section"
        assert "PRAYER" in text, "Petition DOCX must contain the PRAYER section"

    def test_docx_no_literal_brace_placeholders(self, client, case_file):
        """REGRESSION: docx must not contain unsubstituted {field_name} tokens."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"Petition DOCX contains unsubstituted placeholders: {placeholders}"


# ── Permission Letter DOCX ────────────────────────────────────────────────


class TestPermissionDocxDownload:
    """Seam: GET /case_file_generator/case/<id>/docx/permission"""

    def test_returns_200(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_docx_mimetype(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        assert response.content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document", (
            f"Expected .docx mimetype, got {response.content_type}"
        )

    def test_docx_is_valid_zip(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        assert response.data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"

    def test_docx_has_word_document_xml(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        assert "word/document.xml" in names, f"Missing word/document.xml in DOCX. Contents: {names}"

    def test_docx_contains_case_number(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        text = _extract_docx_text(response.data)
        assert "TDD-SAMPLE-001" in text, "Permission DOCX must contain the case_number value"

    def test_docx_contains_officer_name(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        text = _extract_docx_text(response.data)
        assert "TDD Officer" in text, "Permission DOCX must contain the food_safety_officer_name value"

    def test_docx_no_literal_brace_placeholders(self, client, case_file):
        """REGRESSION: docx must not contain unsubstituted {field_name} tokens."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        text = _extract_docx_text(response.data)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"Permission DOCX contains unsubstituted placeholders: {placeholders}"


# ── ZIP with both DOCX ───────────────────────────────────────────────────


class TestBothDocxZip:
    """Seam: GET /case_file_generator/case/<id>/docx/zip"""

    def test_returns_200(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_zip_mimetype(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        assert response.content_type == "application/zip", f"Expected application/zip, got {response.content_type}"

    def test_zip_contains_petition_docx(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        petition_files = [n for n in names if "Petition" in n and n.endswith(".docx")]
        assert petition_files, f"No Petition .docx in ZIP. Contents: {names}"

    def test_zip_contains_permission_docx(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        permission_files = [n for n in names if "Permission" in n and n.endswith(".docx")]
        assert permission_files, f"No Permission .docx in ZIP. Contents: {names}"

    def test_zip_petition_docx_no_placeholders(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = [n for n in zf.namelist() if "Petition" in n and n.endswith(".docx")]
            assert names, "No petition docx found"
            with zf.open(names[0]) as f:
                docx_bytes = f.read()
        text = _extract_docx_text(docx_bytes)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"ZIP petition DOCX has unsubstituted placeholders: {placeholders}"

    def test_zip_permission_docx_no_placeholders(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = [n for n in zf.namelist() if "Permission" in n and n.endswith(".docx")]
            assert names, "No permission docx found"
            with zf.open(names[0]) as f:
                docx_bytes = f.read()
        text = _extract_docx_text(docx_bytes)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"ZIP permission DOCX has unsubstituted placeholders: {placeholders}"
