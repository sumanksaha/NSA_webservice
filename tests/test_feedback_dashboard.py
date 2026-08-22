"""Tests for the OCR feedback dashboard (Phase D).

Covers: per-field accuracy math from correction history, dashboard rendering,
the few-shot example store rebuild (lab: corrections excluded), and the
manual refresh route.
"""

import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from app import create_app
from app.extensions import db
from app.models import OCRCorrection, OCRDocument


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


def _make_doc() -> OCRDocument:
    doc = OCRDocument(
        file_name=f"{uuid.uuid4().hex}.pdf",
        file_hash=uuid.uuid4().hex,
        extracted_json=json.dumps({"fields": {}, "page_count": 1}),
        status="completed",
    )
    db.session.add(doc)
    db.session.commit()
    return doc


def _make_correction(field_name: str, old="wrong", new="right") -> OCRCorrection:
    doc = _make_doc()
    row = OCRCorrection(
        ocr_document_id=doc.id,
        field_name=field_name,
        old_value=old,
        new_value=new,
    )
    db.session.add(row)
    db.session.commit()
    return row


class TestFieldAccuracy:
    def test_accuracy_math(self, db_session):
        doc_a = _make_doc()
        _make_doc()
        _make_doc()  # untouched second/third documents
        # field "title" corrected in 1 of 3 documents → accuracy 1 - 1/3
        correction = OCRCorrection(
            ocr_document_id=doc_a.id,
            field_name="title",
            old_value="w",
            new_value="r",
        )
        db.session.add(correction)
        db.session.commit()

        from app.feedback_dashboard.routes import field_accuracy_metrics

        metrics = field_accuracy_metrics()
        assert metrics["total_documents"] == 3
        assert metrics["fields"]["title"]["accuracy"] == round(1 - 1 / 3, 4)
        assert metrics["fields"]["title"]["corrections"] == 1

    def test_uncorrected_fields_absent_from_metrics(self, db_session):
        _make_doc()
        from app.feedback_dashboard.routes import field_accuracy_metrics

        assert field_accuracy_metrics()["fields"] == {}

    def test_few_shot_count_reflects_store(self, app, db_session):
        _make_correction("title")
        from app.ocr_pipeline.feedback import refresh_few_shot_examples_sync

        result = refresh_few_shot_examples_sync()
        assert result["examples"] >= 1

        from app.feedback_dashboard.routes import _count_few_shot_examples

        assert _count_few_shot_examples() >= 1


class TestFewShotStore:
    def test_refresh_writes_wrong_right_pairs_and_skips_lab_fields(self, app, db_session):
        _make_correction("manufacturer", old="Acem", new="Acme")
        _make_correction("lab:Vitamin A", old="120", new="118")  # lab corrections excluded

        from app.ocr_pipeline.feedback import refresh_few_shot_examples_sync

        result = refresh_few_shot_examples_sync()
        assert result["examples"] == 1  # only the non-lab correction

        store = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        assert "lab:Vitamin A" not in store
        assert store["manufacturer"] == [{"wrong": "Acem", "right": "Acme"}]

    def test_celery_task_delegates_to_sync_helper(self, app, db_session):
        _make_correction("authority", old="FSSI", new="FSSAI")
        from app.ocr_pipeline.tasks import refresh_few_shot_examples

        result = refresh_few_shot_examples(limit=10)
        assert result["examples"] == 1


class TestDashboardRoutes:
    def test_dashboard_renders(self, client, db_session):
        resp = client.get("/feedback-dashboard/")
        assert resp.status_code == 200

    def test_metrics_api_shape(self, client, db_session):
        resp = client.get("/feedback-dashboard/api/metrics")
        assert resp.status_code == 200
        body = resp.json
        assert {"total_documents", "fields", "few_shot_examples"} <= set(body)

    def test_refresh_route_returns_summary(self, client, db_session):
        _make_correction("title", old="A", new="B")
        resp = client.post("/feedback-dashboard/refresh-examples")
        assert resp.status_code == 200
        assert resp.json["status"] == "ok"
        assert resp.json["examples"] >= 1
