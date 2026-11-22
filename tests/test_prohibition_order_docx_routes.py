"""TDD tests for the prohibition-order DOCX download endpoint.

Seam: /case_file_generator/case/<id>/docx/prohibition_order

The Prohibition_order.adoc template is the SECOND document for an unsafe sample.
It is generated for EVERY unsafe case, including retailer-cum-manufacturer
(prepared/loose food) cases — no RCM exclusion applies. For RCM cases the letter
omits batch/manufacturing/expiry details because there is no separate
manufacturer. The letter repeats the first paragraph of the Prayer (the sample
description) so that it is self-contained.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from typing import ClassVar

import pytest
from test_case_file_docx_routes import _extract_docx_text

from app import create_app
from app.extensions import db
from app.models import CaseFile


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with app.app_context():
            from werkzeug.security import generate_password_hash

            from app.extensions import db
            from app.models.auth import User

            existing = User.query.filter_by(username="tdduser").first()
            if existing:
                db.session.delete(existing)
                db.session.commit()

            user = User(
                username="tdduser",
                password_hash=generate_password_hash("tddpass"),
                is_admin=True,
            )
            db.session.add(user)
            db.session.commit()
            c.post(
                "/auth/login",
                data={"username": "tdduser", "password": "tddpass"},
                follow_redirects=True,
            )
        yield c


def _make_unsafe_case(client=None, is_rcm=False, packet_count=4, sample_qty="1000g"):
    """Create one unsafe CaseFile row and return it (caller deletes it)."""
    from app.extensions import db

    case = CaseFile(
        case_number="PROH-TEST-001",
        food_safety_officer_name="Prohibition Test Officer",
        authorization_date=date(2023, 4, 1),
        inspection_date=date(2023, 3, 1),
        inspection_time="12:40",
        manufacturer_fssai="10012345678901",
        manufacturer_name="Prohibition Test Manufacturer",
        manufacturer_fbo_name="Prohibition Mfg FBO",
        manufacturer_address="123 Industrial Area, Food City, FC-400001",
        retailer_fssai="20012345678901" if is_rcm else "",
        retailer_name="Prohibition Test Retailer" if is_rcm else "",
        retailer_fbo_name="Prohibition Ret FBO" if is_rcm else "",
        retailer_address="456 Market Street, Retail Town, RT-300000" if is_rcm else "",
        product_name="Prohibition Test Product",
        batch_no="PROH-BATCH-99",
        sample_quantity=sample_qty,
        packet_count=packet_count,
        mfg_date=date(2023, 1, 1),
        expiry_date=date(2024, 1, 1),
        other_food_articles="Packaged Snacks",
        total_cost="250",
        cost_in_words="Two Hundred Fifty Rupees Only",
        sample_code="PROH-SL-001",
        sample_submission_date=date(2023, 3, 2),
        Lab_Registration_No="WB/FOOD/2023/009",
        do_receipt_date=date(2023, 3, 4),
        analyst_report_no="PROH/AR/1",
        analyst_report_date=date(2023, 3, 10),
        directive_letter_no="PROH/DL/1",
        directive_letter_date=date(2023, 3, 12),
        retailer_report_receive_date=date(2023, 3, 14),
        manufacturer_report_receive_date=date(2023, 3, 15),

        is_misbranded=False,
        is_substandard=False,
        is_unsafe=True,
        retailer_cum_manufacturer=is_rcm,
    )
    db.session.add(case)
    db.session.commit()
    return case


class TestProhibitionOrderDocxDownload:
    """Seam: GET /case_file_generator/case/<id>/docx/prohibition_order"""

    def _make_unsafe_case(self, client, is_rcm=False, packet_count=4, sample_qty="1000g"):
        return _make_unsafe_case(client, is_rcm=is_rcm, packet_count=packet_count, sample_qty=sample_qty)

    def test_returns_200_non_rcm_unsafe(self, client):
        case = self._make_unsafe_case(client, is_rcm=False)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            assert response.content_type == (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            assert response.data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_returns_200_rcm_unsafe(self, client):
        """RCM (prepared/loose food) unsafe cases also get a prohibition order."""
        case = self._make_unsafe_case(client, is_rcm=True)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 200, f"Expected 200 for RCM, got {response.status_code}"
            assert response.content_type == (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            assert response.data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_adoc_download_rcm_unsafe(self, client):
        """The raw .adoc download also works for RCM unsafe cases."""
        case = self._make_unsafe_case(client, is_rcm=True)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/adoc/prohibition_order",
            )
            assert response.status_code == 200, f"Expected 200 for RCM adoc, got {response.status_code}"
            body = response.data.decode("utf-8", errors="replace")
            assert "Prohibition of Sale of Unsafe Food" in body
            assert "{{" not in body and "}}" not in body, (
                "Prohibition Order .adoc must not contain unsubstituted Jinja placeholders"
            )
            # RCM adoc must address the retailer, not a blank manufacturer.
            assert "Prohibition Test Retailer" in body
            assert "Prohibition Test Manufacturer" not in body
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_adoc_safe_case_returns_403(self, client):
        """Non-unsafe cases must not get a prohibition order .adoc."""
        case = CaseFile(
            case_number="SAFE-ADOC-001",
            food_safety_officer_name="Test Officer",
            authorization_date=date(2023, 4, 1),
            inspection_date=date(2023, 3, 1),
            inspection_time="12:40",
            manufacturer_fssai="10012345678901",
            manufacturer_name="Test Manufacturer",
            manufacturer_fbo_name="Test MFG FBO",
            manufacturer_address="123 Industrial Area",
            retailer_fssai="20012345678901",
            retailer_name="Test Retailer",
            retailer_fbo_name="Test Ret FBO",
            retailer_address="456 Market Street",
            product_name="Safe Product",
            batch_no="SAFE-BATCH-99",
            sample_quantity="1000g",
            packet_count=4,
            mfg_date=date(2023, 1, 1),
            expiry_date=date(2024, 1, 1),
            sample_code="SAFE-ADOC-001",
            sample_submission_date=date(2023, 3, 2),
            Lab_Registration_No="WB/FOOD/2023/009",
            do_receipt_date=date(2023, 3, 4),
            analyst_report_no="SAFE/AR/1",
            analyst_report_date=date(2023, 3, 10),
            directive_letter_no="SAFE/DL/1",
            directive_letter_date=date(2023, 3, 12),
            retailer_report_receive_date=date(2023, 3, 14),
            manufacturer_report_receive_date=date(2023, 3, 15),
            is_misbranded=False,
            is_substandard=False,
            is_unsafe=False,
            retailer_cum_manufacturer=False,
        )
        db.session.add(case)
        db.session.commit()
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/adoc/prohibition_order",
            )
            assert response.status_code == 403, f"Expected 403 for safe case adoc, got {response.status_code}"
            json = response.get_json()
            assert json is not None
            assert "only available for unsafe cases" in json["error"]
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_rcm_order_omits_batch_mfg_expiry(self, client):
        """RCM orders drop batch/manufacturing/expiry clauses but keep the product."""
        case = self._make_unsafe_case(client, is_rcm=True)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 200
            text = _extract_docx_text(response.data)
            assert "Prohibition Test Product" in text
            assert "PROH-BATCH-99" not in text
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_rcm_order_addressed_to_retailor(self, client):
        """RCM orders must address the retailer, not a blank manufacturer."""
        case = self._make_unsafe_case(client, is_rcm=True)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 200
            text = _extract_docx_text(response.data)
            # Retailer identity must be present.
            assert "Prohibition Test Retailer" in text
            assert "456 Market Street, Retail Town, RT-300000" in text
            # Manufacturer identity must NOT be present (it is blanked for RCM).
            assert "Prohibition Test Manufacturer" not in text
            assert "123 Industrial Area, Food City, FC-400001" not in text
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_safe_case_returns_403(self, client):
        """Non-unsafe cases must not get a prohibition order."""
        case = CaseFile(
            case_number="SAFE-TEST-001",
            food_safety_officer_name="Test Officer",
            authorization_date=date(2023, 4, 1),
            inspection_date=date(2023, 3, 1),
            inspection_time="12:40",
            manufacturer_fssai="10012345678901",
            manufacturer_name="Test Manufacturer",
            manufacturer_fbo_name="Test MFG FBO",
            manufacturer_address="123 Industrial Area",
            retailer_fssai="20012345678901",
            retailer_name="Test Retailer",
            retailer_fbo_name="Test Ret FBO",
            retailer_address="456 Market Street",
            product_name="Safe Product",
            batch_no="SAFE-BATCH-99",
            sample_quantity="1000g",
            packet_count=4,
            mfg_date=date(2023, 1, 1),
            expiry_date=date(2024, 1, 1),
            sample_code="SAFE-SL-001",
            sample_submission_date=date(2023, 3, 2),
            Lab_Registration_No="WB/FOOD/2023/009",
            do_receipt_date=date(2023, 3, 4),
            analyst_report_no="SAFE/AR/1",
            analyst_report_date=date(2023, 3, 10),
            directive_letter_no="SAFE/DL/1",
            directive_letter_date=date(2023, 3, 12),
            retailer_report_receive_date=date(2023, 3, 14),
            manufacturer_report_receive_date=date(2023, 3, 15),
            is_misbranded=False,
            is_substandard=False,
            is_unsafe=False,
            retailer_cum_manufacturer=False,
        )
        db.session.add(case)
        db.session.commit()
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 403, f"Expected 403 for safe case, got {response.status_code}"
            json = response.get_json()
            assert json is not None
            assert "only available for unsafe cases" in json["error"]
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_docx_has_prayer_first_paragraph(self, client):
        """The prohibition order is self-contained: it repeats the Prayer's first paragraph."""
        case = self._make_unsafe_case(client, is_rcm=False)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 200
            text = _extract_docx_text(response.data)
            assert "Whereas, a sample namely" in text
            assert "Prohibition Test Product" in text
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_docx_has_fbo_address(self, client):
        """The prohibition order is addressed to the FBO manufacturer and includes its address."""
        case = self._make_unsafe_case(client, is_rcm=False)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 200
            text = _extract_docx_text(response.data)
            assert "Prohibition Test Manufacturer" in text
            assert "123 Industrial Area, Food City, FC-400001" in text
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_docx_has_per_packet_equation(self, client):
        """The letter renders the computed per-packet equation: 250g X 4 = 1000g."""
        case = self._make_unsafe_case(client, is_rcm=False)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 200
            text = _extract_docx_text(response.data)
            assert "250g X 4 = 1000g" in text, "Computed per-packet equation must render"
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_non_rcm_order_exposes_batch_mfg_expiry(self, client):
        """Only non-RCM orders expose batch/manufacturing/expiry fields."""
        case = self._make_unsafe_case(client, is_rcm=False)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 200
            text = _extract_docx_text(response.data)
            assert "PROH-BATCH-99" in text
            assert "10012345678901" in text
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_unknown_case_returns_404(self, client):
        response = client.get("/case_file_generator/case/999999/docx/prohibition_order")
        assert response.status_code == 404

    def test_docx_no_unsubstituted_placeholders(self, client):
        case = self._make_unsafe_case(client, is_rcm=False)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 200
            with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
            assert "{{" not in xml and "}}" not in xml, (
                "Prohibition Order DOCX must not contain unsubstituted Jinja {{ }} placeholders"
            )
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_ui_shows_prohibition_order_button_non_rcm(self, client):
        """The index page offers the Prohibition Order button for non-RCM unsafe cases."""
        case = self._make_unsafe_case(client, is_rcm=False)
        try:
            response = client.get("/case_file_generator/")
            assert response.status_code == 200
            html = response.data.decode("utf-8", errors="replace")
            assert "/docx/prohibition_order" in html
            assert "Prohibition Order" in html
        finally:
            db.session.delete(case)
            db.session.commit()

    def test_ui_shows_prohibition_order_button_rcm(self, client):
        """The Prohibition Order button is shown for retailer-cum-manufacturer cases too."""
        case = self._make_unsafe_case(client, is_rcm=True)
        try:
            response = client.get("/case_file_generator/")
            assert response.status_code == 200
            html = response.data.decode("utf-8", errors="replace")
            assert "/docx/prohibition_order" in html, (
                "Prohibition Order button must be visible for RCM (prepared/loose food)"
            )
            assert "Prohibition Order" in html
            # Prayer button still available for RCM cases.
            assert "/docx/unsafe_file" in html
            assert "> Prayer" in html
        finally:
            db.session.delete(case)
            db.session.commit()


class TestUnsafeCaseAllFormats:
    """Every unsafe case must expose Prayer, Advocate Engagement and Prohibition
    Order in BOTH Word (.docx) and AsciiDoc (.adoc) formats."""

    DOCX_ROUTES: ClassVar[dict[str, str]] = {
        "prayer": "/case_file_generator/case/{id}/docx/unsafe_file",
        "advocate_engagement": "/case_file_generator/case/{id}/docx/advocate-engagement",
        "prohibition_order": "/case_file_generator/case/{id}/docx/prohibition_order",
    }
    ADOC_ROUTES: ClassVar[dict[str, str]] = {
        "prayer": "/case_file_generator/case/{id}/adoc/unsafe_file",
        "advocate_engagement": "/case_file_generator/case/{id}/adoc/advocate_engagement",
        "prohibition_order": "/case_file_generator/case/{id}/adoc/prohibition_order",
    }

    def test_all_docx_and_adoc_download_for_unsafe_case(self, client):
        case = _make_unsafe_case(client, is_rcm=True)
        case_id = case.id
        try:
            for label, route in self.DOCX_ROUTES.items():
                response = client.get(route.format(id=case_id))
                assert response.status_code == 200, f"{label} docx: {response.status_code}"
                assert response.data[:4] == b"PK\x03\x04", f"{label} docx must be a ZIP archive"

            for label, route in self.ADOC_ROUTES.items():
                response = client.get(route.format(id=case_id))
                assert response.status_code == 200, f"{label} adoc: {response.status_code}"
                body = response.data.decode("utf-8", errors="replace")
                assert "{{" not in body and "}}" not in body, (
                    f"{label} adoc must not contain unsubstituted Jinja placeholders"
                )
        finally:
            from app.extensions import db

            db.session.delete(case)
            db.session.commit()

    def test_ui_offers_docx_and_adoc_for_all_three(self, client):
        case = _make_unsafe_case(client, is_rcm=True)
        case_id = case.id
        try:
            response = client.get("/case_file_generator/")
            assert response.status_code == 200
            html = response.data.decode("utf-8", errors="replace")
            for route in self.DOCX_ROUTES.values():
                path = route.format(id=case_id)
                assert path in html, f"Missing docx link: {path}"
            for route in self.ADOC_ROUTES.values():
                path = route.format(id=case_id)
                assert path in html, f"Missing adoc link: {path}"
        finally:
            from app.extensions import db

            db.session.delete(case)
            db.session.commit()


# ── _compute_per_packet_sample_quantity helpers ───────────────────────────────


def _call_compute_per_packet(sample_quantity, packet_count):
    from app.case_file_generator.routes import _compute_per_packet_sample_quantity as f

    return f(sample_quantity, packet_count)


class TestComputePerPacketSampleQuantity:
    """Unit tests for the derived per-packet sample quantity used in the per-packet
    equation (per_packet X packet_count = sample_quantity) of both documents.
    """

    def test_integer_division_clean_result(self):
        assert _call_compute_per_packet("1000g", 4) == "250g"
        assert _call_compute_per_packet("2000ml", 5) == "400ml"

    def test_non_integer_division(self):
        # 500/3 = 166.666... -> formats with :g -> 166.667
        assert _call_compute_per_packet("500g", 3) == "166.667g"
        assert _call_compute_per_packet("1000g", 3) == "333.333g"

    def test_unit_with_space(self):
        assert _call_compute_per_packet("1000 g", 4) == "250g"
        assert _call_compute_per_packet("1000 ml", 2) == "500ml"

    def test_no_unit_suffix(self):
        # Returns the computed number; no unit to append.
        assert _call_compute_per_packet("1000", 4) == "250"
        assert _call_compute_per_packet("1000", 3) == "333.333"

    def test_division_by_zero_returns_original(self):
        # Packet count of zero is invalid; fall back to the raw sample quantity.
        assert _call_compute_per_packet("1000g", 0) == "1000g"
        assert _call_compute_per_packet("1000g", -1) == "1000g"

    def test_missing_packet_count_returns_original(self):
        # packet_count None -> return the raw sample_quantity (template renders as-is).
        assert _call_compute_per_packet("1000g", None) == "1000g"

    def test_missing_sample_quantity_returns_empty(self):
        assert _call_compute_per_packet(None, 4) == ""
        assert _call_compute_per_packet("", 4) == ""

    def test_non_numeric_sample_quantity_returns_original(self):
        # Malformed quantity passes through untouched rather than leaking placeholders.
        assert _call_compute_per_packet("abcg", 4) == "abcg"
        assert _call_compute_per_packet("thousand grams", 4) == "thousand grams"

    def test_string_packet_count_parses(self):
        assert _call_compute_per_packet("1000g", "4") == "250g"

    def test_float_packet_count_parses(self):
        assert _call_compute_per_packet("1000g", 4.0) == "250g"
