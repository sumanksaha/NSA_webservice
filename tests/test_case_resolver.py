"""Tests for the CaseResolver (D1 deepening task).

Verifies ID disambiguation between CaseFile and Adjudication, kind hints,
and case-number extraction.
"""

import pytest

from app.extensions import db
from app.models import Adjudication, CaseFile, FSO
from app.shared.case_resolver import CaseResolver, ResolvedCase


@pytest.fixture
def test_app():
    """Create a minimal Flask app with an in-memory SQLite database."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["DISABLE_PDF_GENERATION"] = "1"
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def sample_entities(test_app):
    """Create CaseFile(s) and Adjudication(s) with separate autoincrement IDs.

    CaseFile gets ID=1; two Adjudications get IDs 1 and 2, so ID 2 exists
    only in the Adjudication table (testing the ``kind`` disambiguation path).
    """
    from datetime import datetime

    fso = FSO(fso_name="Test FSO")
    db.session.add(fso)
    db.session.commit()

    case_file = CaseFile(
        case_number="CF-001",
        food_safety_officer_name="Test FSO",
        authorization_date=datetime(2026, 1, 1),
        inspection_date=datetime(2026, 1, 1),
        inspection_time="10:00",
        manufacturer_fssai="MFG123",
        manufacturer_name="Test Mfg",
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
        sample_code="SMP001",
        sample_submission_date=datetime(2026, 7, 2),
        Lab_Registration_No="WB/FOOD/2025/001",
        do_receipt_date=datetime(2026, 7, 4),
        analyst_report_no="AR-001",
        analyst_report_date=datetime(2026, 7, 5),
        directive_letter_no="DL-001",
        directive_letter_date=datetime(2026, 7, 6),
        retailer_report_receive_date=datetime(2026, 7, 7),
        manufacturer_report_receive_date=datetime(2026, 7, 8),
    )
    adjudication_1 = Adjudication(
        case_number="ADJ-001",
        food_safety_officer="Test FSO",
        non_license="no",
        pre_authorization="no",
        complaint_lodged="no",
        fbo_owner="Test Owner",
        fbo_name="Test FBO",
        fbo_address="123 FBO St",
        fssai_license="FSSAI123",
        concerned_food="Test Food",
        problem="Contamination",
        First_inspection_date=datetime(2026, 1, 1),
        compliance_deadline=datetime(2026, 2, 1),
        inspection_date=datetime(2026, 1, 15),
    )
    adjudication_2 = Adjudication(
        case_number="ADJ-002",
        food_safety_officer="Test FSO",
        non_license="no",
        pre_authorization="no",
        complaint_lodged="no",
        fbo_owner="Test Owner 2",
        fbo_name="Test FBO 2",
        fbo_address="456 FBO St",
        fssai_license="FSSAI456",
        concerned_food="Test Food 2",
        problem="Contamination 2",
        First_inspection_date=datetime(2026, 2, 1),
        compliance_deadline=datetime(2026, 3, 1),
        inspection_date=datetime(2026, 2, 15),
    )
    db.session.add(case_file)
    db.session.add(adjudication_1)
    db.session.add(adjudication_2)
    db.session.commit()
    return case_file, adjudication_1, adjudication_2


class TestCaseResolver:
    def test_resolve_case_file(self, test_app, sample_entities):
        """Resolving a CaseFile ID returns case_type='case_file'."""
        case_file, _, _ = sample_entities
        resolved = CaseResolver().resolve(case_file.id)
        assert resolved is not None
        assert resolved.case_type == "case_file"
        assert resolved.case_id == case_file.id
        assert resolved.adjudication_id is None
        assert resolved.case_number == "CF-001"
        assert resolved.record is case_file

    def test_resolve_adjudication(self, test_app, sample_entities):
        """Resolving an Adjudication ID (unique to adjudications table) returns case_type='adjudication'."""
        _, _, adjudication_2 = sample_entities
        resolved = CaseResolver().resolve(adjudication_2.id)
        assert resolved is not None
        assert resolved.case_type == "adjudication"
        assert resolved.adjudication_id == adjudication_2.id
        assert resolved.case_id is None
        assert resolved.case_number == "ADJ-002"
        assert resolved.record is adjudication_2

    def test_resolve_case_file_first(self, test_app, sample_entities):
        """When kind=None and both tables share an ID, CaseFile wins."""
        case_file, adjudication_1, _ = sample_entities
        assert case_file.id == adjudication_1.id  # Both are ID 1
        resolved = CaseResolver().resolve(case_file.id)
        assert resolved is not None
        assert resolved.case_type == "case_file"

    def test_resolve_with_kind_hint(self, test_app, sample_entities):
        """When kind='adjudication', only the Adjudication table is checked."""
        case_file, _, _ = sample_entities
        # case_file.id exists in CaseFile; kind=adjudication should NOT find it
        # as a CaseFile but the same ID may exist in Adjudication table.
        # With kind=adjudication, we only look in Adjudication table.
        resolved = CaseResolver().resolve(case_file.id, kind="adjudication")
        # case_file.id (1) also exists as Adjudication ID 1, so it resolves
        assert resolved is not None
        assert resolved.case_type == "adjudication"

        # ID 99999 doesn't exist in either table
        resolved = CaseResolver().resolve(99999, kind="adjudication")
        assert resolved is None

    def test_resolve_with_kind_case_file(self, test_app, sample_entities):
        """When kind='case_file', only the CaseFile table is checked."""
        _, _, adjudication_2 = sample_entities
        # adjudication_2.id (2) does NOT exist in CaseFile table
        resolved = CaseResolver().resolve(adjudication_2.id, kind="case_file")
        assert resolved is None

        # case_file.id exists in CaseFile table
        case_file, _, _ = sample_entities
        resolved = CaseResolver().resolve(case_file.id, kind="case_file")
        assert resolved is not None
        assert resolved.case_type == "case_file"

    def test_resolve_missing_id(self, test_app):
        """A non-existent ID returns None."""
        resolved = CaseResolver().resolve(99999)
        assert resolved is None

    def test_resolve_missing_id_with_kind(self, test_app):
        """A non-existent ID with a kind hint also returns None."""
        resolved = CaseResolver().resolve(99999, kind="case_file")
        assert resolved is None
        resolved = CaseResolver().resolve(99999, kind="adjudication")
        assert resolved is None

    def test_resolved_case_dataclass_fields(self, test_app, sample_entities):
        """ResolvedCase has all expected fields."""
        case_file, _, _ = sample_entities
        resolved = CaseResolver().resolve(case_file.id)
        assert isinstance(resolved, ResolvedCase)
        assert hasattr(resolved, "case_id")
        assert hasattr(resolved, "adjudication_id")
        assert hasattr(resolved, "case_type")
        assert hasattr(resolved, "case_number")
        assert hasattr(resolved, "record")
