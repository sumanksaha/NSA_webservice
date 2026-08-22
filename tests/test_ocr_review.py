"""Tests for the OCR review workflow (Phase B).

Covers: correction logging (OCRCorrection), extracted_json updates, skip
semantics for unchanged values, lab-parameter corrections, conflict opening
when a manual value disagrees with a lab-report value, resolution flow, and
the conflict-resolution queue.
"""

import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from app import create_app
from app.extensions import db
from app.models import ConflictLog, LabTestParameter, OCRCorrection, OCRDocument


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
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    import contextlib

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


def _make_doc(fields: dict | None = None) -> OCRDocument:
    doc = OCRDocument(
        file_name="report.pdf",
        file_hash=uuid.uuid4().hex,
        extracted_json=json.dumps({
            "fields": fields or {"title": "Old Title", "fbo_name": "Acme Foods"},
            "confidence_scores": {"title": 0.9},
            "extracted_text": "raw text",
            "page_count": 1,
        }),
        status="completed",
    )
    db.session.add(doc)
    db.session.commit()
    return doc


def _make_lab_param(doc_id: str, name: str, observed: str, standard: str | None = None) -> LabTestParameter:
    param = LabTestParameter(
        ocr_document_id=doc_id,
        parameter_name=name,
        observed_value=observed,
        standard_value=standard,
        source_authority="zonal_ocr",
    )
    db.session.add(param)
    db.session.commit()
    return param


class TestApplyFieldCorrections:
    def test_correction_creates_ocr_correction_row(self, db_session):
        doc = _make_doc()
        from app.ocr_extraction.service import apply_field_corrections

        result = apply_field_corrections(doc.id, {"title": "New Title"})
        assert result.applied_count == 1
        assert result.applied[0] == {"field_name": "title", "old": "Old Title", "new": "New Title"}
        row = OCRCorrection.query.filter_by(ocr_document_id=doc.id).one()
        assert row.field_name == "title"
        assert row.old_value == "Old Title" and row.new_value == "New Title"

    def test_correction_updates_extracted_json(self, db_session):
        doc = _make_doc()
        from app.ocr_extraction.service import apply_field_corrections, load_payload

        apply_field_corrections(doc.id, {"title": "New Title"})
        assert load_payload(doc)["fields"]["title"] == "New Title"

    def test_unchanged_values_are_skipped(self, db_session):
        doc = _make_doc()
        from app.ocr_extraction.service import apply_field_corrections

        result = apply_field_corrections(doc.id, {"title": "Old Title", "fbo_name": "Acme Foods"})
        assert result.applied_count == 0
        assert result.skipped == 2
        assert OCRCorrection.query.filter_by(ocr_document_id=doc.id).count() == 0

    def test_unknown_document_raises_lookup_error(self, db_session):
        from app.ocr_extraction.service import apply_field_corrections

        with pytest.raises(LookupError):
            apply_field_corrections("missing-doc-id", {"title": "x"})

class TestConflictRule:
    def test_conflict_opened_when_lab_report_disagrees(self, db_session):
        doc = _make_doc(fields={"manufacturer": "Acme Corp"})
        _make_lab_param(doc.id, "manufacturer", observed="Acme Corporation")
        from app.ocr_extraction.service import apply_field_corrections

        result = apply_field_corrections(doc.id, {"manufacturer": "Acme Corp Ltd"})
        assert result.conflicts_opened == 1
        conflict = ConflictLog.query.filter_by(ocr_document_id=doc.id).one()
        assert conflict.resolved is False
        values = json.loads(conflict.values_json)
        assert {v["source"] for v in values} == {"manual", "lab_report"}

    def test_no_conflict_when_lab_report_agrees(self, db_session):
        doc = _make_doc(fields={"manufacturer": "Acme Corp"})
        _make_lab_param(doc.id, "manufacturer", observed="New Name")
        from app.ocr_extraction.service import apply_field_corrections

        result = apply_field_corrections(doc.id, {"manufacturer": "New Name"})
        assert result.conflicts_opened == 0
        assert ConflictLog.query.count() == 0

    def test_no_conflict_for_field_without_lab_param(self, db_session):
        doc = _make_doc()
        from app.ocr_extraction.service import apply_field_corrections

        result = apply_field_corrections(doc.id, {"fbo_name": "Other Foods"})
        assert result.conflicts_opened == 0


class TestLabParameterCorrection:
    def test_correction_logs_and_flags_manual(self, db_session):
        doc = _make_doc()
        param = _make_lab_param(doc.id, "Vitamin A", observed="120")
        from app.ocr_extraction.service import correct_lab_parameter

        corrected = correct_lab_parameter(param.id, "118")
        assert corrected.observed_value == "118"
        assert corrected.source_authority == "manual"
        row = OCRCorrection.query.filter_by(ocr_document_id=doc.id).one()
        assert row.field_name == "lab:Vitamin A"

    def test_unchanged_param_is_noop(self, db_session):
        doc = _make_doc()
        param = _make_lab_param(doc.id, "Vitamin A", observed="120")
        from app.ocr_extraction.service import correct_lab_parameter

        assert correct_lab_parameter(param.id, "120") is param
        assert OCRCorrection.query.count() == 0


class TestConflictResolution:
    def test_resolve_marks_resolved_and_applies_value(self, db_session):
        doc = _make_doc(fields={"manufacturer": "OCR Value"})
        conflict = ConflictLog(
            ocr_document_id=doc.id,
            field_name="manufacturer",
            values_json=json.dumps([{"source": "manual", "value": "Human Value"}]),
        )
        db.session.add(conflict)
        db.session.commit()

        from app.ocr_extraction.service import resolve_conflict

        result = resolve_conflict(conflict.id, "Human Value")
        assert result.applied_count == 1
        assert conflict.resolved is True
        assert conflict.resolved_value == "Human Value"

    def test_queue_lists_only_unresolved(self, db_session):
        doc = _make_doc()
        db.session.add(ConflictLog(ocr_document_id=doc.id, field_name="open_one", values_json="[]"))
        db.session.add(
            ConflictLog(ocr_document_id=doc.id, field_name="resolved_one", values_json="[]", resolved=True)
        )
        db.session.commit()

        from app.ocr_extraction.service import open_conflicts

        names = [c.field_name for c in open_conflicts()]
        assert "open_one" in names
        assert "resolved_one" not in names


class TestReviewRoutes:
    def test_document_list_renders(self, client, db_session):
        _make_doc()
        resp = client.get("/ocr/documents")
        assert resp.status_code == 200

    def test_review_page_renders_fields(self, client, db_session):
        doc = _make_doc()
        resp = client.get(f"/ocr/documents/{doc.id}/review")
        assert resp.status_code == 200
        assert b"Old Title" in resp.data

    def test_corrections_endpoint_json(self, client, db_session):
        doc = _make_doc()
        resp = client.post(f"/ocr/documents/{doc.id}/corrections", json={"title": "Corrected"})
        assert resp.status_code == 200
        assert resp.json["applied_count"] == 1

    def test_corrections_unknown_document_404(self, client, db_session):
        resp = client.post("/ocr/documents/missing/corrections", json={"title": "x"})
        assert resp.status_code == 404
