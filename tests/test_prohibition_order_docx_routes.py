"""TDD tests for the prohibition-order DOCX download endpoint.

Seam: /case_file_generator/case/<id>/docx/prohibition_order

The Prohibition_order.adoc template is the SECOND document for an unsafe sample.
It is rendered only for non-RCM unsafe cases (a separate manufacturer exists and
batch/manufacturing/expiry details are present). RCM (prepared/loose food) cases
cannot have a prohibition order — the endpoint returns 403 with a user-facing
message that directs the user to the Prayer instead. The letter repeats the
first paragraph of the Prayer (the sample description) so that it is
self-contained.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

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


class TestProhibitionOrderDocxDownload:
    """Seam: GET /case_file_generator/case/<id>/docx/prohibition_order"""

    def _make_unsafe_case(self, client, is_rcm=False, packet_count=4, sample_qty="1000g"):
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

    def test_returns_403_rcm_unsafe(self, client):
        """RCM (prepared/loose food) unsafe cases cannot have a prohibition order."""
        case = self._make_unsafe_case(client, is_rcm=True)
        try:
            response = client.get(
                f"/case_file_generator/case/{case.id}/docx/prohibition_order",
            )
            assert response.status_code == 403, f"Expected 403 for RCM, got {response.status_code}"
            json = response.get_json()
            assert json is not None
            assert "error" in json
            assert "cannot be issued for prepared/loose" in json["error"]
            assert "retailer-cum-manufacturer" in json["error"]
            assert "Download the Prayer" in json["error"]
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

    def test_ui_hides_prohibition_order_button_rcm(self, client):
        """The Prohibition Order button is hidden for retailer-cum-manufacturer cases."""
        case = self._make_unsafe_case(client, is_rcm=True)
        try:
            response = client.get("/case_file_generator/")
            assert response.status_code == 200
            html = response.data.decode("utf-8", errors="replace")
            assert "/docx/prohibition_order" not in html, (
                "Prohibition Order button must be hidden for RCM (prepared/loose food)"
            )
            # Prayer button still available for RCM cases.
            assert "/docx/unsafe_file" in html
            assert "> Prayer" in html
        finally:
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
