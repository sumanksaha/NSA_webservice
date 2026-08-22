"""Tests for autopopulation (Phase C).

Covers: verified-record construction (Sample + reviewed OCR), prefill bundle
projection through MAPPINGS, non-conforming lab-parameter detection, and
idempotent FBO-issue auto-drafting.
"""

import json
import os
import sys
import uuid
from datetime import UTC, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from app import create_app
from app.extensions import db
from app.models import FboIssue, LabTestParameter, OCRDocument, Sample


def _clear_pipeline_tables():
    """Delete pipeline rows so tests are order-independent (session-wide DB)."""
    from sqlalchemy import text

    from app.extensions import db

    for table in (
        "ocr_correction",
        "conflict_log",
        "lab_test_parameter",
        "ocr_document",
        "fbo_issue",
        "sample",
    ):
        db.session.execute(text(f"DELETE FROM {table}"))  # noqa: S608 - hardcoded test tables
    db.session.commit()


@pytest.fixture(scope="module")
def app():
    import contextlib

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    # Manual push/pop (NOT `with`): conftest force-pops every app context after
    # each test, so a `with`-held context would raise LookupError at module
    # teardown — the source of phantom "ERROR" entries.
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    from app.models import User

    if not User.query.filter_by(username="ocrtester").first():
        db.session.add(User(username="ocrtester", password_hash="pbkdf2:sha256$test$dummy"))
        db.session.commit()
    db.session.remove()
    with contextlib.suppress(Exception):
        ctx.pop()
    yield app


@pytest.fixture()
def client(app):
    import contextlib

    from app.models import User

    c = app.test_client()
    ctx = app.app_context()
    ctx.push()
    try:
        user = User.query.filter_by(username="ocrtester").first()
        with c.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
    finally:
        with contextlib.suppress(Exception):
            ctx.pop()
    return c


@pytest.fixture()
def db_session(app):
    import contextlib

    from flask import has_app_context

    # Push a fresh context per test: conftest force-pops every app context
    # after each test, so the module fixture's context never survives here.
    if not has_app_context():
        ctx = app.app_context()
        ctx.push()
    else:
        ctx = None
    _clear_pipeline_tables()  # setup isolation too — first test inherits nothing
    db.session.remove()
    yield db
    db.session.rollback()
    db.session.remove()
    from sqlalchemy import text

    for table in (
        "ocr_correction",
        "conflict_log",
        "lab_test_parameter",
        "ocr_document",
        "fbo_issue",
        "sample",
    ):
        db.session.execute(text(f"DELETE FROM {table}"))  # noqa: S608 - hardcoded test tables
    db.session.commit()
    if ctx is not None:
        with contextlib.suppress(Exception):
            ctx.pop()


def _make_sample(**overrides) -> Sample:
    sample = Sample(
        sample_code=overrides.get("sample_code", f"S-{uuid.uuid4().hex[:8]}"),
        sample_name=overrides.get("sample_name", "Milk Pack"),
        sample_type="enforcement",
        fso_name=overrides.get("fso_name", "FSO Verma"),
        collection_date=datetime.now(UTC),
        retailer_name=overrides.get("retailer_name", "Acme Stores"),
        retailer_fssai=overrides.get("retailer_fssai", "12345678901234"),
        nature_of_food=overrides.get("nature_of_food", "Dairy"),
        billed=False,
    )
    db.session.add(sample)
    db.session.commit()
    return sample


def _make_ocr_doc(sample_id: int) -> OCRDocument:
    doc = OCRDocument(
        file_name="lab.pdf",
        file_hash=f"hash-{sample_id}",
        extracted_json=json.dumps({
            "fields": {"title": "Lab Analysis Report", "authority": "FSSAI"},
            "page_count": 1,
        }),
        status="completed",
        sample_id=sample_id,
    )
    db.session.add(doc)
    db.session.commit()
    return doc


def _make_param(sample_id: int, name: str, observed: str, standard: str | None) -> LabTestParameter:
    param = LabTestParameter(
        ocr_document_id="doc-x",
        sample_id=sample_id,
        parameter_name=name,
        observed_value=observed,
        standard_value=standard,
        source_authority="zonal_ocr",
    )
    db.session.add(param)
    db.session.commit()
    return param


class TestBuildVerifiedRecord:
    def test_merges_sample_and_ocr_fields(self, db_session):
        sample = _make_sample()
        _make_ocr_doc(sample.id)
        from app.autopopulation.service import build_verified_record

        record = build_verified_record(sample.id)
        assert record["sample"]["sample_code"] == sample.sample_code
        assert record["ocr"]["fields"]["authority"] == "FSSAI"

    def test_missing_sample_returns_none(self, db_session):
        from app.autopopulation.service import build_verified_record

        assert build_verified_record(999999) is None

    def test_no_extraction_omits_ocr_section(self, db_session):
        sample = _make_sample()
        from app.autopopulation.service import build_verified_record

        record = build_verified_record(sample.id)
        assert "ocr" not in record


class TestPrefill:
    def test_bundles_project_mapped_fields(self, db_session):
        sample = _make_sample()
        _make_ocr_doc(sample.id)
        from app.autopopulation.service import prefill

        result = prefill(sample.id)
        case_file = result["prefill"]["case_file"]
        assert case_file["sample_code"] == sample.sample_code
        assert case_file["document_title"] == "Lab Analysis Report"
        assert "retailer_name" in result["prefill"]["case_file"]

    def test_empty_values_are_omitted(self, db_session):
        sample = _make_sample(retailer_name="")
        _make_ocr_doc(sample.id)
        from app.autopopulation.service import prefill

        result = prefill(sample.id)
        assert "retailer_name" not in result["prefill"]["case_file"]

    def test_prefill_route_404_for_unknown_sample(self, client, db_session):
        resp = client.get("/autopopulation/prefill/999999")
        assert resp.status_code == 404

    def test_prefill_route_returns_json(self, client, db_session):
        sample = _make_sample()
        _make_ocr_doc(sample.id)
        resp = client.get(f"/autopopulation/prefill/{sample.id}")
        assert resp.status_code == 200
        assert "case_file" in resp.json["prefill"]


class TestNonConformingAndFboDraft:
    def test_non_conforming_params_detected(self, db_session):
        sample = _make_sample()
        _make_param(sample.id, "Lead", observed="0.5", standard="0.1")
        _make_param(sample.id, "Fat", observed="4.0", standard="4.0")  # conforming
        from app.autopopulation.service import non_conforming_params

        failures = non_conforming_params(sample.id)
        assert [p.parameter_name for p in failures] == ["Lead"]

    def test_conforming_sample_has_no_failures(self, db_session):
        sample = _make_sample()
        _make_param(sample.id, "Fat", observed=" 4.0 ", standard="4.0")  # whitespace-tolerant
        from app.autopopulation.service import non_conforming_params

        assert non_conforming_params(sample.id) == []

    def test_draft_fbo_issue_created_for_non_conforming(self, db_session):
        sample = _make_sample()
        _make_param(sample.id, "Lead", observed="0.5", standard="0.1")
        from app.autopopulation.service import draft_fbo_issue_for_sample

        issue = draft_fbo_issue_for_sample(sample.id)
        assert issue is not None
        assert issue.state == "open"
        assert issue.source_type == "sample"
        assert issue.fso_name == "FSO Verma"
        detail = json.loads(issue.detail_json)
        assert detail["sample_id"] == sample.id
        assert detail["non_conforming"][0]["parameter"] == "Lead"

    def test_draft_is_idempotent(self, db_session):
        sample = _make_sample()
        _make_param(sample.id, "Lead", observed="0.5", standard="0.1")
        from app.autopopulation.service import draft_fbo_issue_for_sample

        first = draft_fbo_issue_for_sample(sample.id)
        second = draft_fbo_issue_for_sample(sample.id)
        assert first.id == second.id
        assert FboIssue.query.count() == 1

    def test_conforming_sample_returns_none(self, db_session):
        sample = _make_sample()
        _make_param(sample.id, "Fat", observed="4.0", standard="4.0")
        from app.autopopulation.service import draft_fbo_issue_for_sample

        assert draft_fbo_issue_for_sample(sample.id) is None

    def test_draft_route_returns_json(self, client, db_session):
        sample = _make_sample()
        _make_param(sample.id, "Lead", observed="0.5", standard="0.1")
        resp = client.post(f"/autopopulation/draft-fbo-issue/{sample.id}")
        assert resp.status_code == 200
        assert resp.json["status"] in ("drafted", "existing")
        assert resp.json["state"] == "open"

    def test_draft_route_unknown_sample_404(self, client, db_session):
        resp = client.post("/autopopulation/draft-fbo-issue/999999")
        assert resp.status_code == 404
