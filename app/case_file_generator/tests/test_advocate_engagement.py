"""Integration tests for the Advocate Engagement document.

Seam: GET /case_file_generator/case/<id>/docx/advocate-engagement

Tests that:
1. The endpoint returns 200 and a valid DOCX for unsafe cases.
2. The endpoint returns 403 for non-unsafe (substandard/misbranded) cases.
3. The rendered DOCX contains all case data placeholders substituted.
4. No unsubstituted Jinja placeholders leak into the DOCX.
5. The index UI button shows for unsafe cases and hides for non-unsafe cases.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date

import pytest

from app import create_app
from app.models import CaseFile

PAST = date(2023, 1, 1)  # well over 30 days ago → generation gate passes


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c, app.app_context():
        from werkzeug.security import generate_password_hash

        from app.extensions import db
        from app.models.auth import User

        existing = User.query.filter_by(username="advuser").first()
        if existing:
            db.session.delete(existing)
            db.session.commit()

        user = User(
            username="advuser",
            password_hash=generate_password_hash("advpass"),
            is_admin=True,
        )
        db.session.add(user)
        db.session.commit()
        c.post(
            "/auth/login",
            data={"username": "advuser", "password": "advpass"},
            follow_redirects=True,
        )
        yield c


@pytest.fixture
def unsafe_case(client):
    """Create an unsafe CaseFile (the advocate-engagement target)."""
    from app.extensions import db

    case = CaseFile(
        case_number="ADV-UNSAFE-001",
        food_safety_officer_name="Adv Officer",
        authorization_date=PAST,
        inspection_date=PAST,
        inspection_time="12:40",
        manufacturer_fssai="10012345678901",
        manufacturer_name="Adv Manufacturer",
        manufacturer_fbo_name="Adv Mfg FBO",
        manufacturer_address="Adv Mfg Address",
        retailer_fssai="20012345678901",
        retailer_name="Adv Retailer",
        retailer_fbo_name="Adv Ret FBO",
        retailer_address="Adv Ret Address",
        product_name="Adv Product",
        batch_no="ADV-BATCH-1",
        sample_quantity="1000g",
        packet_count=4,
        mfg_date=PAST,
        expiry_date=date(2024, 1, 1),
        other_food_articles="Biscuits, Chips",
        total_cost="250",
        cost_in_words="Two Hundred Fifty Rupees Only",
        sample_code="ADV-SL-001",
        witness_name="Adv Witness",
        sample_submission_date=PAST,
        Lab_Registration_No="WB/FOOD/2023/001",
        do_receipt_date=PAST,
        is_unsafe=True,
        is_substandard=False,
        is_misbranded=False,
        analyst_report_no="ADV/AR/1",
        analyst_report_date=PAST,
        directive_letter_no="ADV/DL/1",
        directive_letter_date=PAST,
        retailer_report_receive_date=PAST,
        manufacturer_report_receive_date=PAST,
    )
    db.session.add(case)
    db.session.commit()
    yield case
    db.session.delete(case)
    db.session.commit()


@pytest.fixture
def safe_case(client):
    """Create a non-unsafe (misbranded) CaseFile."""
    from app.extensions import db

    case = CaseFile(
        case_number="ADV-SAFE-001",
        food_safety_officer_name="Safe Officer",
        authorization_date=PAST,
        inspection_date=PAST,
        inspection_time="12:40",
        manufacturer_fssai="10012345678902",
        manufacturer_name="Safe Manufacturer",
        manufacturer_fbo_name="Safe Mfg FBO",
        manufacturer_address="Safe Mfg Address",
        retailer_fssai="20012345678902",
        retailer_name="Safe Retailer",
        retailer_fbo_name="Safe Ret FBO",
        retailer_address="Safe Ret Address",
        product_name="Safe Product",
        batch_no="SAFE-BATCH-1",
        sample_quantity="1000g",
        packet_count=4,
        mfg_date=PAST,
        expiry_date=date(2024, 1, 1),
        other_food_articles="Biscuits, Chips",
        total_cost="250",
        cost_in_words="Two Hundred Fifty Rupees Only",
        sample_code="SAFE-SL-001",
        witness_name="Safe Witness",
        sample_submission_date=PAST,
        Lab_Registration_No="WB/FOOD/2023/002",
        do_receipt_date=PAST,
        is_unsafe=False,
        is_substandard=False,
        is_misbranded=True,
        analyst_report_no="SAFE/AR/1",
        analyst_report_date=PAST,
        directive_letter_no="SAFE/DL/1",
        directive_letter_date=PAST,
        retailer_report_receive_date=PAST,
        manufacturer_report_receive_date=PAST,
    )
    db.session.add(case)
    db.session.commit()
    yield case
    db.session.delete(case)
    db.session.commit()


# ── Helpers ──────────────────────────────────────────────────────────────


def _extract_docx_text(docx_bytes: bytes) -> str:
    """Plain text from DOCX bytes (strip XML, collapse whitespace)."""
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf, zf.open(
        "word/document.xml"
    ) as f:
        xml = f.read().decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", " ", xml)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _brace_placeholders(text: str) -> list[str]:
    """Return all {word} or {{word}} tokens in text."""
    return re.findall(r"\{[\w\s]+\}|\{\{[\w\s]+\}\}", text)


# ── Advocate Engagement DOCX ────────────────────────────────────────────


class TestAdvocateEngagementDocxDownload:
    """Seam: GET /case_file_generator/case/<id>/docx/advocate-engagement"""

    def test_returns_200_for_unsafe_case(self, client, unsafe_case):
        response = client.get(
            f"/case_file_generator/case/{unsafe_case.id}/docx/advocate-engagement"
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.get_data(as_text=True)}"
        )

    def test_returns_403_for_non_unsafe_case(self, client, safe_case):
        response = client.get(
            f"/case_file_generator/case/{safe_case.id}/docx/advocate-engagement"
        )
        assert response.status_code == 403, (
            f"Expected 403, got {response.status_code}"
        )
        assert b"only available for unsafe cases" in response.data

    def test_returns_docx_mimetype(self, client, unsafe_case):
        response = client.get(
            f"/case_file_generator/case/{unsafe_case.id}/docx/advocate-engagement"
        )
        assert response.content_type == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ), f"Expected .docx mimetype, got {response.content_type}"

    def test_docx_is_valid_zip(self, client, unsafe_case):
        response = client.get(
            f"/case_file_generator/case/{unsafe_case.id}/docx/advocate-engagement"
        )
        assert response.data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"

    def test_docx_has_word_document_xml(self, client, unsafe_case):
        response = client.get(
            f"/case_file_generator/case/{unsafe_case.id}/docx/advocate-engagement"
        )
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        assert "word/document.xml" in names, (
            f"Missing word/document.xml in DOCX. Contents: {names}"
        )

    def test_docx_contains_case_data(self, client, unsafe_case):
        """The DOCX must contain case data values entered on the case file."""
        response = client.get(
            f"/case_file_generator/case/{unsafe_case.id}/docx/advocate-engagement"
        )
        text = _extract_docx_text(response.data)
        assert "Adv Product" in text, "DOCX must contain the product_name value"
        assert "ADV-SL-001" in text, "DOCX must contain the sample_code value"
        assert "Adv Officer" in text, "DOCX must contain the food_safety_officer_name value"

    def test_docx_contains_sample_code(self, client, unsafe_case):
        response = client.get(
            f"/case_file_generator/case/{unsafe_case.id}/docx/advocate-engagement"
        )
        text = _extract_docx_text(response.data)
        assert "ADV-SL-001" in text, "DOCX must contain the sample_code value"

    def test_docx_contains_officer_name(self, client, unsafe_case):
        response = client.get(
            f"/case_file_generator/case/{unsafe_case.id}/docx/advocate-engagement"
        )
        text = _extract_docx_text(response.data)
        assert "Adv Officer" in text, "DOCX must contain the food_safety_officer_name value"

    def test_docx_no_literal_brace_placeholders(self, client, unsafe_case):
        """REGRESSION: docx must not contain unsubstituted {{field}} tokens."""
        response = client.get(
            f"/case_file_generator/case/{unsafe_case.id}/docx/advocate-engagement"
        )
        text = _extract_docx_text(response.data)
        placeholders = _brace_placeholders(text)
        assert not placeholders, (
            f"Advocate Engagement DOCX contains unsubstituted placeholders: {placeholders}"
        )

    def test_docx_renders_prohibition_and_sanction_fallbacks(self, client, unsafe_case):
        """prohibition_order / sanction_date are not in context → '—' fallback."""
        response = client.get(
            f"/case_file_generator/case/{unsafe_case.id}/docx/advocate-engagement"
        )
        text = _extract_docx_text(response.data)
        assert "Prohibition Order" in text
        assert "Sanction of Prosecution" in text


# ── UI button gating ─────────────────────────────────────────────────────


class TestAdvocateEngagementButtonGating:
    """The Advocate Engagement button appears only for unsafe cases."""

    def test_button_hidden_for_non_unsafe_case(self, client, safe_case):
        response = client.get("/case_file_generator/")
        assert response.status_code == 200
        html = response.data.decode("utf-8", errors="replace")
        assert "ADV-SAFE-001" in html, "case row must be listed"
        assert "advocate-engagement" not in html, (
            "Advocate Engagement button must be hidden for non-unsafe cases"
        )

    def test_button_shown_for_unsafe_case(self, client, unsafe_case):
        response = client.get("/case_file_generator/")
        assert response.status_code == 200
        html = response.data.decode("utf-8", errors="replace")
        assert "ADV-UNSAFE-001" in html, "case row must be listed"
        assert "advocate-engagement" in html, (
            "Advocate Engagement button must appear for unsafe cases"
        )
