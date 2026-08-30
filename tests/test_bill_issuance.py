"""Tests for the BillIssuance module (app/bill_generator/issuance.py).

Interface-level tests for the bill issuance transaction — the invariant suite:
atomic persist (ADR-0001), sync isolation, dispatch failure semantics, and the
``IssuanceResult`` taxonomy. Transport mapping is covered by
``tests/test_bill_generator.py``.
"""

import os
import sys
from datetime import datetime

import pytest
from sqlalchemy.orm.exc import StaleDataError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from flask import Flask

from app.bill_generator.issuance import IssuanceResult, issue, validate_range
from app.bill_generator.routes import bill_generator_bp
from app.extensions import db
from app.models import FSO, Bill, BillSample, Sample
from app.utils.filters import format_date_indian, to_words


@pytest.fixture
def app(monkeypatch):
    """Test Flask app: no QStash (forced), in-memory SQLite."""
    for key in ("QSTASH_TOKEN", "QSTASH_CURRENT_SIGNING_KEY", "QSTASH_NEXT_SIGNING_KEY", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(key, raising=False)

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["TESTING"] = True
    app.register_blueprint(bill_generator_bp, url_prefix="")
    app.jinja_env.filters["to_words"] = to_words
    app.jinja_env.filters["format_date"] = format_date_indian

    with app.app_context():
        db.init_app(app)
        db.create_all()
        yield app
        db.drop_all()


def _seed_samples():
    """Create an FSO plus two unbilled samples; returns their ids."""
    db.session.add(FSO(fso_name="Test FSO"))
    s1 = Sample(
        sample_code="S001",
        sample_name="Sample 1",
        sample_type="enforcement",
        fso_name="Test FSO",
        collection_date=datetime(2026, 1, 15),
        price="100.50",
        billed=False,
    )
    s2 = Sample(
        sample_code="S002",
        sample_name="Sample 2",
        sample_type="surveillance",
        fso_name="Test FSO",
        collection_date=datetime(2026, 1, 16),
        price="200.75",
        billed=False,
    )
    db.session.add_all([s1, s2])
    db.session.commit()
    return [s1.id, s2.id]


FORM = {
    "Name": "Test Officer",
    "EMP_ID": "12345",
    "No_of_enfbills": "1",
    "No_of_survbills": "1",
    "TR_Value": "TR123",
    "TR_date": "2026-01-17",
    "Submission_date": "2026-01-18",
}


class TestValidateRange:
    def test_missing_dates(self):
        assert validate_range(None, "2026-01-31") == "Both start and end dates are required"
        assert validate_range("2026-01-01", "") == "Both start and end dates are required"

    def test_reversed_range(self):
        assert validate_range("2026-01-20", "2026-01-10") == "End date must be >= start date"

    def test_valid_range(self):
        assert validate_range("2026-01-01", "2026-01-31") is None


class TestIssue:
    def test_invalid_returns_without_persisting(self, app):
        result = issue("", "2026-01-31", FORM)
        assert result.status == "invalid"
        assert result.bill_id is None
        assert Bill.query.count() == 0

    def test_queued_async_dispatch(self, app, monkeypatch):
        sample_ids = _seed_samples()
        monkeypatch.setattr(
            "app.utils.qstash_client.publish_task",
            lambda *a, **k: {"mode": "async", "message_id": "msg_123"},
        )
        monkeypatch.setattr(
            "app.services.sync_orchestrator.sync_row",
            lambda *a, **k: None,
        )
        result = issue("2026-01-15", "2026-01-16", FORM)
        assert result.status == "queued"
        assert result.task_id == "msg_123"
        assert result.bill_id is not None
        # ADR-0001 invariant: bill + billed flags + links, all persisted.
        bill = Bill.query.one()
        assert result.bill_id == bill.id
        assert all(s.billed for s in Sample.query.all())
        linked = BillSample.query.filter_by(bill_id=bill.id).all()
        assert sorted(bs.sample_id for bs in linked) == sorted(sample_ids)

    def test_generated_sync_fallback(self, app, monkeypatch):
        _seed_samples()
        monkeypatch.setattr(
            "app.utils.qstash_client.publish_task",
            lambda *a, **k: {"mode": "sync", "result": {"status": "ok", "file_path": "pdfs/bills/bill_1.pdf"}},
        )
        monkeypatch.setattr(
            "app.services.sync_orchestrator.sync_row",
            lambda *a, **k: None,
        )
        result = issue("2026-01-15", "2026-01-16", FORM)
        assert result.status == "generated"
        assert result.bill_id is not None
        assert result.pdf_result["status"] == "ok"

    def test_dispatch_failure_keeps_bill_with_id(self, app, monkeypatch):
        _seed_samples()
        monkeypatch.setattr(
            "app.services.sync_orchestrator.sync_row",
            lambda *a, **k: None,
        )

        def boom(*a, **k):
            raise RuntimeError("qstash down")

        monkeypatch.setattr("app.utils.qstash_client.publish_task", boom)
        result = issue("2026-01-15", "2026-01-16", FORM)
        assert result.status == "error"
        # The Bill stands and the result names it — recoverable, not duplicate-bait.
        assert result.bill_id is not None
        assert Bill.query.count() == 1
        assert all(s.billed for s in Sample.query.all())
        assert "Bill PDF generation failed" in result.detail

    def test_task_error_result_keeps_bill_with_id(self, app, monkeypatch):
        _seed_samples()
        monkeypatch.setattr(
            "app.utils.qstash_client.publish_task",
            lambda *a, **k: {"mode": "sync", "result": {"status": "error", "error": "WeasyPrint exploded"}},
        )
        monkeypatch.setattr(
            "app.services.sync_orchestrator.sync_row",
            lambda *a, **k: None,
        )
        result = issue("2026-01-15", "2026-01-16", FORM)
        assert result.status == "error"
        assert result.bill_id is not None
        assert result.detail == "WeasyPrint exploded"

    def test_missing_tr_date_rejected(self, app):
        """TR_date / Submission_date are NOT NULL columns — missing values
        must yield a clean "invalid" result, not an IntegrityError 500."""
        _seed_samples()
        form = {**FORM, "TR_date": ""}
        result = issue("2026-01-15", "2026-01-16", form)
        assert result.status == "invalid"
        assert "TR date and Submission date" in result.detail
        assert Bill.query.count() == 0

    def test_garbage_tr_date_rejected(self, app):
        _seed_samples()
        form = {**FORM, "Submission_date": "not-a-date"}
        result = issue("2026-01-15", "2026-01-16", form)
        assert result.status == "invalid"
        assert Bill.query.count() == 0

    def test_sync_failure_does_not_block(self, app, monkeypatch):
        _seed_samples()
        monkeypatch.setattr(
            "app.utils.qstash_client.publish_task",
            lambda *a, **k: {"mode": "async", "message_id": "msg_1"},
        )

        def sync_boom(*a, **k):
            raise RuntimeError("gspread down")

        monkeypatch.setattr("app.services.sync_orchestrator.sync_row", sync_boom)
        result = issue("2026-01-15", "2026-01-16", FORM)
        # Sync failure propagates as error — bill is persisted but status is error
        assert result.status == "error"
        assert result.bill_id is not None
        assert Bill.query.count() == 1

    def test_conflict_rolls_back_atomically(self, app, monkeypatch):
        sample_ids = _seed_samples()
        # Fail exactly one commit — the single atomic commit inside issue().
        state = {"armed": False}
        real_commit = db.session.commit

        def maybe_conflict(*a, **k):
            if state["armed"]:
                raise StaleDataError("stale")
            return real_commit(*a, **k)

        monkeypatch.setattr(db.session, "commit", maybe_conflict)
        state["armed"] = True
        result = issue("2026-01-15", "2026-01-16", FORM)
        assert result.status == "conflict"
        assert "modified by another user" in result.detail
        # Nothing persisted: no Bill, Samples still unbilled.
        assert Bill.query.count() == 0
        assert not any(s.billed for s in Sample.query.all())
        assert BillSample.query.filter(BillSample.sample_id.in_(sample_ids)).count() == 0


class TestIssuanceResult:
    def test_defaults(self):
        r = IssuanceResult(status="invalid")
        assert r.bill_id is None and r.task_id is None and r.pdf_result is None
