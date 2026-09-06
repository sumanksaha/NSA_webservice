"""TDD: Red → Green for adjudication DOCX download.

Seam: routes.py download endpoints (/docx/petition, /docx/permission, /docx/zip).

Red: DOCX download must not contain unsubstituted literal placeholders
     and must contain the same field values that preview renders.
Green: routes call pandoc-based adoc_renderer instead of broken word_converter.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date

import pytest

from app import create_app
from app.models import Adjudication


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with app.app_context():
            from app.extensions import db
            from app.models.auth import User
            from werkzeug.security import generate_password_hash

            # Clean up any pre-existing test user (module-scoped DB persists)
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
def adjudication(client):
    """Create and return a test Adjudication record."""
    from app.extensions import db

    adj = Adjudication(
        case_number="TDD-CASE-001",
        food_safety_officer="TDD Officer",
        non_license="no",
        pre_authorization="no",
        complaint_lodged="no",
        ce_license_no="CE-TDD-001",
        ce_trade_name="TDD Trade Name",
        ce_proprietor="TDD Proprietor",
        ce_address="TDD Address",
        ce_status="Active",
        fbo_owner="TDD Owner",
        fbo_name="TDD FBO",
        fbo_address="TDD FBO Address",
        fssai_license="999999999999999999",
        concerned_food="TDD Food",
        problem="TDD Problem",
        First_inspection_date=date(2023, 1, 1),
        compliance_deadline=date(2023, 6, 1),
        Complaint_date=date(2023, 2, 1),
        inspection_date=date(2023, 3, 1),
        authorization_date=date(2023, 4, 1),
        clean_premise="yes",
        refrigerator_clean="yes",
        proper_attire="yes",
        proper_covered_utensil="yes",
        date_tag="yes",
        veg_nonveg_separation="yes",
        food_segregation="yes",
        license_display="yes",
        artificial_colour="no",
        Expired_item="yes",
        Pest_report="yes",
        Water_report="yes",
        section_55="no",
        section_56="no",
        section_58="no",
        section_63="no",
        section_64="no",
    )
    db.session.add(adj)
    db.session.commit()
    yield adj
    db.session.delete(adj)
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
    """Seam: GET /adjudication/case/<id>/docx/petition"""

    def test_returns_200(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/petition")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_docx_mimetype(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/petition")
        assert response.content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document", (
            f"Expected .docx mimetype, got {response.content_type}"
        )

    def test_docx_is_valid_zip(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/petition")
        data = response.data
        assert data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"

    def test_docx_has_word_document_xml(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/petition")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        assert "word/document.xml" in names, f"Missing word/document.xml in DOCX. Contents: {names}"

    def test_docx_contains_case_number(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "TDD-CASE-001" in text, "Petition DOCX must contain the case_number value"

    def test_docx_contains_officer_name(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "TDD Officer" in text, "Petition DOCX must contain the food_safety_officer_name value"

    def test_docx_contains_fbo_name(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "TDD FBO" in text, "Petition DOCX must contain the fbo_name value"

    def test_docx_no_literal_brace_placeholders(self, client, adjudication):
        """REGRESSION: docx must not contain unsubstituted {field_name} tokens."""
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/petition")
        text = _extract_docx_text(response.data)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"Petition DOCX contains unsubstituted placeholders: {placeholders}"


# ── Permission Letter DOCX ────────────────────────────────────────────────


class TestPermissionDocxDownload:
    """Seam: GET /adjudication/case/<id>/docx/permission"""

    def test_returns_200(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/permission")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_docx_mimetype(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/permission")
        assert response.content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document", (
            f"Expected .docx mimetype, got {response.content_type}"
        )

    def test_docx_is_valid_zip(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/permission")
        data = response.data
        assert data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"

    def test_docx_has_word_document_xml(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/permission")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        assert "word/document.xml" in names, f"Missing word/document.xml in DOCX. Contents: {names}"

    def test_docx_contains_case_number(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/permission")
        text = _extract_docx_text(response.data)
        assert "TDD-CASE-001" in text, "Permission DOCX must contain the case_number value"

    def test_docx_contains_officer_name(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/permission")
        text = _extract_docx_text(response.data)
        assert "TDD Officer" in text, "Permission DOCX must contain the food_safety_officer_name value"

    def test_docx_no_literal_brace_placeholders(self, client, adjudication):
        """REGRESSION: docx must not contain unsubstituted {field_name} tokens."""
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/permission")
        text = _extract_docx_text(response.data)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"Permission DOCX contains unsubstituted placeholders: {placeholders}"


# ── ZIP with both DOCX ───────────────────────────────────────────────────


class TestBothDocxZip:
    """Seam: GET /adjudication/case/<id>/docx/zip"""

    def test_returns_200(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/zip")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_zip_mimetype(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/zip")
        assert response.content_type == "application/zip", f"Expected application/zip, got {response.content_type}"

    def test_zip_contains_petition_docx(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        petition_files = [n for n in names if "Petition" in n and n.endswith(".docx")]
        assert petition_files, f"No Petition .docx in ZIP. Contents: {names}"

    def test_zip_contains_permission_docx(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        permission_files = [n for n in names if "Permission" in n and n.endswith(".docx")]
        assert permission_files, f"No Permission .docx in ZIP. Contents: {names}"

    def test_zip_petition_docx_no_placeholders(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = [n for n in zf.namelist() if "Petition" in n and n.endswith(".docx")]
            assert names, "No petition docx found"
            with zf.open(names[0]) as f:
                docx_bytes = f.read()
        text = _extract_docx_text(docx_bytes)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"ZIP petition DOCX has unsubstituted placeholders: {placeholders}"

    def test_zip_permission_docx_no_placeholders(self, client, adjudication):
        response = client.get(f"/adjudication/case/{adjudication.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = [n for n in zf.namelist() if "Permission" in n and n.endswith(".docx")]
            assert names, "No permission docx found"
            with zf.open(names[0]) as f:
                docx_bytes = f.read()
        text = _extract_docx_text(docx_bytes)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"ZIP permission DOCX has unsubstituted placeholders: {placeholders}"
