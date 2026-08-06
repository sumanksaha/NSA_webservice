"""Tests for the Phase 12 Legal Validation Engine (app/validation/).

Covers:
- Every rule individually, with valid and invalid ``case_data`` payloads
- The ValidationEngine orchestrator (scoring, grading, error payloads)
- HTTP endpoints: POST /validation/validate and GET /validation/case/<id>
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.validation.engine import ValidationEngine
from app.validation.rules import (
    DocumentCompletenessRule,
    DuplicateEvidenceRule,
    MandatorySectionsRule,
    NumberingFormatRule,
    SignaturePlaceholderRule,
    StatutoryReferenceRule,
    TimelineConsistencyRule,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _dt(day: int, month: int = 1, year: int = 2026, hour: int = 10) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _base_case_data(**overrides) -> dict:
    """A minimal case_data payload — rules are pure and need no DB."""
    data = {
        "case_id": 1,
        "adjudication_id": None,
        "case_type": "case_file",
        "case_number": "CF/2026/001",
        "fields": {},
        "annexures": [],
        "evidence": [],
        "sample": None,
        "document_html": "",
        "document_html_permission": "",
        "suggested_sections": {"sections": [], "reasoning": {}},
    }
    data.update(overrides)
    return data


def _setup_test_env():
    """Create a test app with in-memory SQLite, a user, and an FSO."""
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="validationuser", password_hash="pbkdf2:sha256$test$dummy")  # noqa: S106
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)  # Flask-Login key

    return app, client, app_context


def _teardown_test_env(app_context):
    from app.extensions import db

    db.session.remove()
    db.drop_all()
    app_context.pop()


def _make_case_file(db, **overrides):
    """Create a CaseFile with a valid, chronologically ordered date set."""
    from app.models import CaseFile

    defaults = dict(
        case_number="CF/VL/2026/001",
        food_safety_officer_name="Test Officer",
        authorization_date=_dt(5, 1),
        inspection_date=_dt(10, 1),
        inspection_time="10:30",
        manufacturer_fssai="MF-100",
        manufacturer_name="Acme Foods",
        manufacturer_fbo_name="Acme Foods Pvt Ltd",
        manufacturer_address="Kolkata",
        retailer_fssai="RT-200",
        retailer_name="Corner Store",
        retailer_fbo_name="Corner Store Pvt Ltd",
        retailer_address="Kolkata",
        product_name="Milk",
        batch_no="B-1",
        sample_quantity="500 ml",
        packet_count=10,
        mfg_date=_dt(1, 1),
        expiry_date=_dt(1, 3),
        sample_code="SMP-VL-001",
        sample_submission_date=_dt(15, 1),
        Lab_Registration_No="LAB-1",
        do_receipt_date=_dt(20, 1),
        analyst_report_no="AR-1",
        analyst_report_date=_dt(1, 2),
        directive_letter_no="DL-1",
        directive_letter_date=_dt(10, 2),
        retailer_report_receive_date=_dt(20, 2),
        manufacturer_report_receive_date=_dt(22, 2),
        applicable_sections="55",
    )
    defaults.update(overrides)
    case = CaseFile(**defaults)
    db.session.add(case)
    db.session.commit()
    return case


def _make_adjudication(db, **overrides):
    """Create an Adjudication with a valid date set + a selected section."""
    from app.models import Adjudication

    defaults = dict(
        case_number="ADJ/VL/2026/001",
        food_safety_officer="Test Officer",
        fbo_owner="Raj",
        fbo_name="Raj Traders",
        fbo_address="Kolkata",
        fssai_license="FSSAI-1",
        Complaint_date=_dt(3, 1),
        First_inspection_date=_dt(8, 1),
        inspection_date=_dt(10, 1),
        compliance_deadline=_dt(8, 2),
        authorization_date=_dt(5, 1),
        non_license="yes",
        section_63="yes",
    )
    defaults.update(overrides)
    adj = Adjudication(**defaults)
    db.session.add(adj)
    db.session.commit()
    return adj


def _make_evidence(db, case_id=None, adjudication_id=None, file_hash=None, filename="evidence.pdf"):
    from app.models import Evidence

    evidence = Evidence(
        evidence_type="report",
        filepath="/tmp/evidence.pdf",
        filename=filename,
        case_id=case_id,
        adjudication_id=adjudication_id,
        file_hash=file_hash,
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


# --------------------------------------------------------------------------- #
# MandatorySectionsRule
# --------------------------------------------------------------------------- #


class TestMandatorySectionsRule:
    def test_adjudication_with_no_sections_errors(self):
        case_data = _base_case_data(
            case_type="adjudication",
            fields={f"section_{s}": "no" for s in ("55", "56", "58", "63", "64")},
        )
        results = MandatorySectionsRule().evaluate(case_data)
        assert len(results) == 1
        assert results[0].severity == "ERROR"
        assert results[0].field_name == "section_55"

    def test_adjudication_with_selected_section_clean(self):
        fields = {f"section_{s}": "no" for s in ("55", "56", "58", "63", "64")}
        fields["section_63"] = "yes"
        results = MandatorySectionsRule().evaluate(_base_case_data(case_type="adjudication", fields=fields))
        assert results == []

    def test_case_file_without_sections_errors(self):
        results = MandatorySectionsRule().evaluate(
            _base_case_data(case_type="case_file", fields={"applicable_sections": None})
        )
        assert len(results) == 1
        assert results[0].severity == "ERROR"

    def test_case_file_with_sections_clean(self):
        results = MandatorySectionsRule().evaluate(
            _base_case_data(case_type="case_file", fields={"applicable_sections": "55, 56"})
        )
        assert results == []


# --------------------------------------------------------------------------- #
# SignaturePlaceholderRule
# --------------------------------------------------------------------------- #


class TestSignaturePlaceholderRule:
    def test_unrendered_documents_skip_with_info(self):
        results = SignaturePlaceholderRule().evaluate(_base_case_data())
        assert len(results) == 1
        assert results[0].severity == "INFO"

    def test_documents_without_signature_error(self):
        case_data = _base_case_data(
            document_html="<p>The petition body…</p>",
            document_html_permission="<p>The permission letter…</p>",
        )
        results = SignaturePlaceholderRule().evaluate(case_data)
        assert len(results) == 1
        assert results[0].severity == "ERROR"

    def test_petition_with_signature_marker_clean(self):
        case_data = _base_case_data(document_html="<div>Signature of the Food Safety Officer: ______</div>")
        assert SignaturePlaceholderRule().evaluate(case_data) == []

    def test_permission_marker_alone_clean(self):
        case_data = _base_case_data(
            document_html="<p>Body</p>",
            document_html_permission='<div class="signature-section">Signature of FSO</div>',
        )
        assert SignaturePlaceholderRule().evaluate(case_data) == []


# --------------------------------------------------------------------------- #
# NumberingFormatRule
# --------------------------------------------------------------------------- #


class TestNumberingFormatRule:
    def test_invalid_sample_code_errors(self):
        case_data = _base_case_data(
            case_type="case_file",
            fields={
                "case_number": "CF/VL/2026/001",
                "sample_code": "bad code!",
                "lab_registration_no": "LAB-1",
            },
        )
        results = NumberingFormatRule().evaluate(case_data)
        assert len(results) == 1
        assert results[0].severity == "ERROR"
        assert results[0].field_name == "sample_code"

    def test_valid_codes_clean(self):
        case_data = _base_case_data(
            case_type="case_file",
            fields={
                "case_number": "CF/VL/2026/001",
                "sample_code": "SMP-VL-001",
                "lab_registration_no": "LAB-1",
            },
        )
        assert NumberingFormatRule().evaluate(case_data) == []

    def test_adjudication_license_fields_warn_not_error(self):
        case_data = _base_case_data(
            case_type="adjudication",
            fields={"case_number": "ADJ/VL/2026/001", "fssai_license": "not a license!", "ce_license_no": None},
        )
        results = NumberingFormatRule().evaluate(case_data)
        assert len(results) == 1
        assert results[0].severity == "WARNING"
        assert results[0].field_name == "fssai_license"

    def test_missing_values_are_skipped(self):
        assert NumberingFormatRule().evaluate(_base_case_data(case_type="case_file", fields={})) == []


# --------------------------------------------------------------------------- #
# StatutoryReferenceRule
# --------------------------------------------------------------------------- #


class TestStatutoryReferenceRule:
    def test_suggested_but_unselected_section_warns(self):
        case_data = _base_case_data(
            case_type="adjudication",
            fields={"section_55": "no", "section_56": "no", "section_58": "no", "section_63": "no", "section_64": "no"},
            suggested_sections={"sections": ["55"], "reasoning": {"55": "Checklist shows direction non-compliance."}},
        )
        results = StatutoryReferenceRule().evaluate(case_data)
        assert any(r.severity == "WARNING" and "55" in r.message for r in results)

    def test_selected_without_checklist_support_warns(self):
        case_data = _base_case_data(
            case_type="adjudication",
            fields={"section_55": "yes", "section_56": "no", "section_58": "no", "section_63": "no", "section_64": "no"},
            suggested_sections={"sections": [], "reasoning": {}},
        )
        results = StatutoryReferenceRule().evaluate(case_data)
        assert any(r.severity == "WARNING" and "55" in r.message for r in results)

    def test_selected_matches_suggestion_clean(self):
        case_data = _base_case_data(
            case_type="adjudication",
            fields={"section_55": "no", "section_56": "no", "section_58": "no", "section_63": "yes", "section_64": "no"},
            suggested_sections={"sections": ["63"], "reasoning": {"63": "FBO is non-licensed."}},
        )
        results = StatutoryReferenceRule().evaluate(case_data)
        assert results == []

    def test_case_file_untracked_section_warns(self):
        case_data = _base_case_data(case_type="case_file", fields={"applicable_sections": "51"})
        results = StatutoryReferenceRule().evaluate(case_data)
        assert any(r.severity == "WARNING" and "51" in r.message for r in results)

    def test_case_file_tracked_section_clean(self):
        case_data = _base_case_data(case_type="case_file", fields={"applicable_sections": "55"})
        assert StatutoryReferenceRule().evaluate(case_data) == []


# --------------------------------------------------------------------------- #
# DuplicateEvidenceRule
# --------------------------------------------------------------------------- #


class TestDuplicateEvidenceRule:
    def test_duplicate_hash_warns(self):
        case_data = _base_case_data(
            evidence=[
                {"id": "a", "file_hash": "h" * 64, "filename": "photo.jpg"},
                {"id": "b", "file_hash": "h" * 64, "filename": "copy.jpg"},
            ]
        )
        results = DuplicateEvidenceRule().evaluate(case_data)
        assert len(results) == 1
        assert results[0].severity == "WARNING"

    def test_distinct_hashes_clean(self):
        case_data = _base_case_data(
            evidence=[
                {"id": "a", "file_hash": "a" * 64, "filename": "one.jpg"},
                {"id": "b", "file_hash": "b" * 64, "filename": "two.jpg"},
            ]
        )
        assert DuplicateEvidenceRule().evaluate(case_data) == []

    def test_null_hashes_ignored(self):
        case_data = _base_case_data(evidence=[{"id": "a", "file_hash": None, "filename": "x.jpg"}])
        assert DuplicateEvidenceRule().evaluate(case_data) == []


# --------------------------------------------------------------------------- #
# TimelineConsistencyRule
# --------------------------------------------------------------------------- #


class TestTimelineConsistencyRule:
    def test_inverted_case_file_dates_error(self):
        case_data = _base_case_data(
            case_type="case_file",
            fields={
                "sample_submission_date": _dt(15, 1).isoformat(),
                "do_receipt_date": _dt(20, 1).isoformat(),
                "analyst_report_date": _dt(5, 1).isoformat(),  # report before submission
            },
        )
        results = TimelineConsistencyRule().evaluate(case_data)
        assert any(r.severity == "ERROR" for r in results)
        assert any("Analyst report" in r.message for r in results)

    def test_ordered_case_file_dates_clean(self):
        case_data = _base_case_data(
            case_type="case_file",
            fields={
                "sample_submission_date": _dt(15, 1).isoformat(),
                "do_receipt_date": _dt(20, 1).isoformat(),
                "analyst_report_date": _dt(1, 2).isoformat(),
            },
        )
        assert TimelineConsistencyRule().evaluate(case_data) == []

    def test_inverted_adjudication_dates_error(self):
        case_data = _base_case_data(
            case_type="adjudication",
            fields={
                "complaint_date": _dt(3, 1).isoformat(),
                "first_inspection_date": _dt(8, 1).isoformat(),
                "followup_inspection_date": _dt(2, 1).isoformat(),  # before first inspection
                "compliance_deadline": _dt(8, 2).isoformat(),
            },
        )
        results = TimelineConsistencyRule().evaluate(case_data)
        assert any(r.severity == "ERROR" for r in results)

    def test_no_dates_info(self):
        results = TimelineConsistencyRule().evaluate(_base_case_data(case_type="case_file", fields={}))
        assert len(results) == 1
        assert results[0].severity == "INFO"


# --------------------------------------------------------------------------- #
# DocumentCompletenessRule
# --------------------------------------------------------------------------- #


class TestDocumentCompletenessRule:
    def test_unlinked_annexure_errors(self):
        case_data = _base_case_data(annexures=[{"id": "x", "caption": "Report", "case_id": None, "adjudication_id": None}])
        results = DocumentCompletenessRule().evaluate(case_data)
        assert any(r.severity == "ERROR" and "not linked" in r.message for r in results)

    def test_linked_annexure_clean(self):
        case_data = _base_case_data(annexures=[{"id": "x", "caption": "Report", "case_id": 1, "adjudication_id": None}])
        results = DocumentCompletenessRule().evaluate(case_data)
        # No ERROR/WARNING — the empty-evidence INFO advisory is expected.
        assert [r for r in results if r.severity != "INFO"] == []

    def test_empty_caption_warns(self):
        case_data = _base_case_data(annexures=[{"id": "x", "caption": "", "case_id": 1, "adjudication_id": None}])
        results = DocumentCompletenessRule().evaluate(case_data)
        assert any(r.severity == "WARNING" and "caption" in r.message for r in results)

    def test_no_evidence_informs(self):
        results = DocumentCompletenessRule().evaluate(_base_case_data())
        assert any(r.severity == "INFO" and "No evidence" in r.message for r in results)

    def test_evidence_missing_hash_warns(self):
        case_data = _base_case_data(evidence=[{"id": "a", "file_hash": None, "filename": "x.jpg"}])
        results = DocumentCompletenessRule().evaluate(case_data)
        assert any(r.severity == "WARNING" and "content hash" in r.message for r in results)


# --------------------------------------------------------------------------- #
# ValidationEngine
# --------------------------------------------------------------------------- #


class TestValidationEngine:
    def test_clean_case_file_scores_100(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)  # applicable_sections="55", ordered dates, valid codes
            result = ValidationEngine().validate_case(case.id, "case_file")

            assert result["score"] == 100
            assert result["grade"] == "Ready"
            assert result["errors"] == []
            assert result["warnings"] == []
            assert result["case_number"] == "CF/VL/2026/001"
            assert result["case_type"] == "case_file"
            assert result["rules_run"] == 7
        finally:
            _teardown_test_env(ctx)

    def test_incomplete_case_file_scores_lower(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            # No applicable sections + invalid sample code + inverted report date.
            case = _make_case_file(
                db,
                applicable_sections=None,
                sample_code="bad code!",
                analyst_report_date=_dt(5, 1),
            )
            result = ValidationEngine().validate_case(case.id, "case_file")

            assert result["errors"], "expected rule errors for an incomplete case"
            assert result["score"] <= 85
            error_ids = {e["rule_id"] for e in result["errors"]}
            assert {"mandatory_sections", "numbering_format", "timeline_consistency"} <= error_ids
        finally:
            _teardown_test_env(ctx)

    def test_clean_adjudication_scores_100(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)  # non_license + section_63 selected, ordered dates
            result = ValidationEngine().validate_case(adj.id, "adjudication")

            assert result["score"] == 100
            assert result["errors"] == []
            assert result["warnings"] == []
            assert result["grade"] == "Ready"
        finally:
            _teardown_test_env(ctx)

    def test_unknown_case_returns_error(self):
        _app, _client, ctx = _setup_test_env()
        try:
            result = ValidationEngine().validate_case(99999, "case_file")
            assert result == {"error": "Case not found"}
        finally:
            _teardown_test_env(ctx)

    def test_duplicate_evidence_detected_via_engine(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            digest = "d" * 64
            _make_evidence(db, case_id=case.id, file_hash=digest, filename="one.pdf")
            _make_evidence(db, case_id=case.id, file_hash=digest, filename="two.pdf")

            result = ValidationEngine().validate_case(case.id, "case_file")
            assert any(w["rule_id"] == "duplicate_evidence" for w in result["warnings"])
        finally:
            _teardown_test_env(ctx)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


class TestValidationRoutes:
    def test_post_validate_returns_report(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            resp = client.post(
                "/validation/validate",
                json={"case_id": case.id, "case_type": "case_file"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "score" in data and "errors" in data and "warnings" in data and "suggestions" in data
            assert data["case_number"] == "CF/VL/2026/001"
        finally:
            _teardown_test_env(ctx)

    def test_post_validate_bad_payload_400(self):
        _app, client, ctx = _setup_test_env()
        try:
            assert client.post("/validation/validate", json={}).status_code == 400
            assert client.post("/validation/validate", json={"case_id": "x", "case_type": "case_file"}).status_code == 400
            assert client.post("/validation/validate", json={"case_id": 1, "case_type": "bogus"}).status_code == 400
            assert client.post("/validation/validate", data="not json").status_code == 400
        finally:
            _teardown_test_env(ctx)

    def test_post_validate_unknown_case_404(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/validation/validate", json={"case_id": 99999, "case_type": "case_file"})
            assert resp.status_code == 404
            assert resp.get_json()["error"] == "Case not found"
        finally:
            _teardown_test_env(ctx)

    def test_get_case_summary_with_kind(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            resp = client.get(f"/validation/case/{case.id}?kind=case_file")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["case_type"] == "case_file"
            assert isinstance(data["score"], int)

            # A case_file id does not exist as an adjudication.
            resp = client.get(f"/validation/case/{case.id}?kind=adjudication")
            assert resp.status_code == 404

            # kind must be one of the two values.
            resp = client.get(f"/validation/case/{case.id}?kind=bogus")
            assert resp.status_code == 400
        finally:
            _teardown_test_env(ctx)

    def test_get_case_summary_unknown_404(self):
        _app, client, ctx = _setup_test_env()
        try:
            assert client.get("/validation/case/99999?kind=case_file").status_code == 404
        finally:
            _teardown_test_env(ctx)

    def test_adjudication_route(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)
            resp = client.post(
                "/validation/validate",
                json={"case_id": adj.id, "case_type": "adjudication"},
            )
            assert resp.status_code == 200
            assert resp.get_json()["case_type"] == "adjudication"
        finally:
            _teardown_test_env(ctx)
