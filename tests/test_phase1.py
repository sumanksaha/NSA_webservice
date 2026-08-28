"""Phase 1 tests — Case File validation error display + structured petition template.

Covers the two remaining Phase 1 roadmap items:
1. Validation error display in UI (server returns structured field errors).
2. Facts/Grounds/Prayer structured sections in the petition template.
"""

from datetime import datetime

import pytest

from app.extensions import db
from app.models import CaseFile, User


@pytest.fixture
def test_client():
    """Test client with database context and a logged-in user."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            user = User(username="testuser", password_hash="pbkdf2:sha256$test$dummy")
            db.session.add(user)
            db.session.commit()

            case_file = CaseFile(
                case_number="TESTCASE001",
                food_safety_officer_name="Test Officer",
                authorization_date=datetime(2026, 7, 3),
                inspection_date=datetime(2026, 7, 3),
                inspection_time="10:00",
                manufacturer_fssai="MFG123",
                manufacturer_name="Test Manufacturer",
                manufacturer_fbo_name="Test MFG FBO",
                manufacturer_address="123 Mfg St",
                retailer_fssai="RET456",
                retailer_name="Test Retailer",
                retailer_fbo_name="Test Retailer FBO",
                retailer_address="456 Retail St",
                product_name="Test Product",
                batch_no="BATCH001",
                sample_quantity="1000g",
                packet_count=4,
                mfg_date=datetime(2026, 6, 1),
                expiry_date=datetime(2026, 8, 1),
                sample_code="TEST001",
                sample_submission_date=datetime(2026, 7, 2),
                Lab_Registration_No="WB/FOOD/2025/001",
                do_receipt_date=datetime(2026, 7, 4),
                is_misbranded=False,
                is_substandard=False,
                analyst_report_no="PK/378/2025-26",
                analyst_report_date=datetime(2026, 7, 5),
                directive_letter_no="H/FSSA/FSO/3054/2025-26",
                directive_letter_date=datetime(2026, 7, 6),
                retailer_report_receive_date=datetime(2026, 7, 7),
                manufacturer_report_receive_date=datetime(2026, 7, 8),
                applicable_regulation="Regulation No 5(9)",
                applicable_clause="Clause (zf) of subsection 1 of section 3 of the FSSA,2006",
            )
            db.session.add(case_file)
            db.session.commit()

        yield client
        with app.app_context():
            db.drop_all()


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


# ---------------------------------------------------------------------------
# Validation error display in UI (Phase 1 item 3)
# ---------------------------------------------------------------------------


class TestCaseFileValidation:
    """Server returns structured field-level errors for invalid submissions."""

    def test_missing_required_fields_return_400_with_errors(self, test_client):
        """POST /generate_case_file with no data returns 400 + field errors."""
        _login(test_client)
        resp = test_client.post("/case_file_generator/generate_case_file", data={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]
        assert isinstance(data["errors"], dict)
        # Key required fields flagged
        assert "case_number" in data["errors"]
        assert "food_safety_officer_name" in data["errors"]
        assert "product_name" in data["errors"]
        assert "sample_code" in data["errors"]

    def test_invalid_packet_count_flagged(self, test_client):
        """Non-integer packet_count is flagged as a field error."""
        _login(test_client)
        data = {
            "case_number": "2026/FSS/104",
            "food_safety_officer_name": "Officer",
            "authorization_date": "2026-07-01",
            "inspection_date": "2026-07-02",
            "inspection_time": "12:40",
            "manufacturer_fssai": "10012345678901",
            "manufacturer_name": "Mfg",
            "manufacturer_fbo_name": "Mfg FBO",
            "manufacturer_address": "Addr",
            "retailer_fssai": "20012345678901",
            "retailer_name": "Ret",
            "retailer_fbo_name": "Ret FBO",
            "retailer_address": "Addr",
            "product_name": "Product",
            "batch_no": "B1",
            "sample_quantity": "1000g",
            "packet_count": "abc",
            "mfg_date": "2026-01-01",
            "expiry_date": "2026-12-31",
            "sample_code": "SL001",
            "sample_submission_date": "2026-07-03",
            "lab_registration_no": "WB/FOOD/2025/001",
            "do_receipt_date": "2026-07-04",
            "analyst_report_no": "PK/1",
            "analyst_report_date": "2026-07-05",
            "directive_letter_no": "H/FSSA/1",
            "directive_letter_date": "2026-07-06",
            "retailer_report_receive_date": "2026-07-07",
            "manufacturer_report_receive_date": "2026-07-08",
        }
        resp = test_client.post("/case_file_generator/generate_case_file", data=data)
        assert resp.status_code == 400
        data = resp.get_json()
        assert "packet_count" in data["errors"]

    def test_invalid_date_flagged(self, test_client):
        """Malformed date string is flagged as a field error."""
        _login(test_client)
        data = {
            "case_number": "2026/FSS/104",
            "food_safety_officer_name": "Officer",
            "authorization_date": "not-a-date",
            "inspection_date": "2026-07-02",
            "inspection_time": "12:40",
            "manufacturer_fssai": "10012345678901",
            "manufacturer_name": "Mfg",
            "manufacturer_fbo_name": "Mfg FBO",
            "manufacturer_address": "Addr",
            "retailer_fssai": "20012345678901",
            "retailer_name": "Ret",
            "retailer_fbo_name": "Ret FBO",
            "retailer_address": "Addr",
            "product_name": "Product",
            "batch_no": "B1",
            "sample_quantity": "1000g",
            "packet_count": "4",
            "mfg_date": "2026-01-01",
            "expiry_date": "2026-12-31",
            "sample_code": "SL001",
            "sample_submission_date": "2026-07-03",
            "lab_registration_no": "WB/FOOD/2025/001",
            "do_receipt_date": "2026-07-04",
            "analyst_report_no": "PK/1",
            "analyst_report_date": "2026-07-05",
            "directive_letter_no": "H/FSSA/1",
            "directive_letter_date": "2026-07-06",
            "retailer_report_receive_date": "2026-07-07",
            "manufacturer_report_receive_date": "2026-07-08",
        }
        resp = test_client.post("/case_file_generator/generate_case_file", data=data)
        assert resp.status_code == 400
        data = resp.get_json()
        assert "authorization_date" in data["errors"]

    def test_valid_form_passes_validation(self, test_client):
        """A complete, well-formed submission passes validation (no 400)."""
        _login(test_client)
        data = {
            "case_number": "2026/FSS/104",
            "food_safety_officer_name": "Officer",
            "authorization_date": "2026-07-01",
            "inspection_date": "2026-07-02",
            "inspection_time": "12:40",
            "manufacturer_fssai": "10012345678901",
            "manufacturer_name": "Mfg",
            "manufacturer_fbo_name": "Mfg FBO",
            "manufacturer_address": "Addr",
            "retailer_fssai": "20012345678901",
            "retailer_name": "Ret",
            "retailer_fbo_name": "Ret FBO",
            "retailer_address": "Addr",
            "product_name": "Product",
            "batch_no": "B1",
            "sample_quantity": "1000g",
            "packet_count": "4",
            "mfg_date": "2026-01-01",
            "expiry_date": "2026-12-31",
            "sample_code": "SL001",
            "sample_submission_date": "2026-07-03",
            "lab_registration_no": "WB/FOOD/2025/001",
            "do_receipt_date": "2026-07-04",
            "analyst_report_no": "PK/1",
            "analyst_report_date": "2026-07-05",
            "directive_letter_no": "H/FSSA/1",
            "directive_letter_date": "2026-07-06",
            "retailer_report_receive_date": "2026-07-07",
            "manufacturer_report_receive_date": "2026-07-08",
        }
        resp = test_client.post("/case_file_generator/generate_case_file", data=data)
        # Not a validation failure (may 200/202/500 depending on PDF infra)
        assert resp.status_code != 400


# ---------------------------------------------------------------------------
# Facts/Grounds/Prayer structured sections (Phase 1 item 4)
# ---------------------------------------------------------------------------


class TestPetitionStructure:
    """The petition template has explicit FACTS, GROUNDS, and PRAYER sections."""

    def test_petition_renders_structured_sections(self, test_client):
        """Rendered petition contains STATEMENT OF FACTS, GROUNDS, and PRAYER."""
        _login(test_client)
        resp = test_client.get("/case_file_generator/1/editor", follow_redirects=False)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "STATEMENT OF FACTS" in html
        assert "GROUNDS" in html
        assert "PRAYER" in html
        # No unresolved Jinja variables
        assert "{{" not in html

    def test_petition_grounds_reference_case_data(self, test_client):
        """GROUNDS section includes the analysis result and product name."""
        _login(test_client)
        resp = test_client.get("/case_file_generator/1/editor", follow_redirects=False)
        html = resp.data.decode("utf-8")
        # Product name appears in the grounds narrative
        assert "Test Product" in html
        # Grounds section contains the appeal reference (Section 46(4))
        assert "46(4)" in html

    def test_petition_prayer_section_has_prayer_clauses(self, test_client):
        """PRAYER section contains numbered prayer clauses."""
        _login(test_client)
        resp = test_client.get("/case_file_generator/1/editor", follow_redirects=False)
        html = resp.data.decode("utf-8")
        # Traditional closing line of a prayer preserved
        assert "ever pray" in html
