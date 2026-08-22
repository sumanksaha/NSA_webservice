"""Tests for the shared FBO-issue lookup domain function.

``app.bill_generator.lookup.lookup_fbo_issues`` is the single seam behind the
Flask ``/bill_generator/lookup_fbo_issues`` route and
``GET /api/v2/bill/lookup-fbo-issues`` — these tests exercise the schema both
transports now serve (sample / inspection / generic prefill branches).
"""

from __future__ import annotations

import json

import pytest

from app.bill_generator.lookup import lookup_fbo_issues


@pytest.fixture()
def db_session():
    """Real Flask-SQLAlchemy session against a throwaway SQLite DB."""
    from app import create_app
    from app.extensions import db

    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        yield db.session
        db.session.rollback()
        db.drop_all()


def _add_issue(session, **overrides):
    from app.models.issue import FboIssue

    fields = {
        "fbo_id": "FBO-1",
        "fbo_name": "Acme Foods",
        "source_type": "sample",
        "state": "open",
        "fso_name": "Officer Sharma",
        "detail_json": None,
    }
    fields.update(overrides)
    issue = FboIssue(**fields)
    session.add(issue)
    session.commit()
    return issue


class TestPrefillBranches:
    def test_sample_issue_full_billing_prefill(self, db_session):
        detail = {"sample_code": "S-01", "sample_name": "Milk", "price": 500, "sampling_date": "2026-08-01"}
        _add_issue(
            db_session,
            source_type="sample",
            detail_json=json.dumps(detail),
            manufacturer_fbo_id="MFG-9",
        )
        [row] = lookup_fbo_issues(db_session, fbo_id="FBO-1")
        assert row["prefill"] == {
            "Name": "Acme Foods",
            "EMP_ID": "Officer Sharma",
            "Designation": "Food Safety Officer",
            "sample_code": "S-01",
            "sample_name": "Milk",
            "price": 500,
            "sampling_date": "2026-08-01",
            "manufacturer_fbo_id": "MFG-9",
        }

    def test_sample_without_manufacturer_has_no_manufacturer_key(self, db_session):
        detail = {"sample_code": "S-02"}
        _add_issue(db_session, source_type="sample", detail_json=json.dumps(detail))
        [row] = lookup_fbo_issues(db_session, fbo_id="FBO-1")
        assert "manufacturer_fbo_id" not in row["prefill"]

    def test_inspection_issue_prefill_joins_checklist(self, db_session):
        detail = {"checklist": ["label check", "storage check"]}
        _add_issue(db_session, source_type="inspection", detail_json=json.dumps(detail))
        [row] = lookup_fbo_issues(db_session, fbo_id="FBO-1")
        assert row["prefill"]["inspection_details"] == "label check, storage check"
        assert row["prefill"]["Designation"] == "Food Safety Officer"

    def test_generic_branch_always_present(self, db_session):
        # No detail_json at all → generic prefill (the branch the old /api/v2
        # copy dropped entirely).
        _add_issue(db_session, source_type="inspection", state="permission_granted", detail_json=None)
        [row] = lookup_fbo_issues(db_session, fbo_id="FBO-1")
        assert row["prefill"] == {
            "Name": "Acme Foods",
            "EMP_ID": "Officer Sharma",
            "Designation": "Food Safety Officer",
        }

    def test_sample_without_detail_falls_back_to_generic(self, db_session):
        _add_issue(db_session, source_type="sample", detail_json=None)
        [row] = lookup_fbo_issues(db_session, fbo_id="FBO-1")
        assert set(row["prefill"]) == {"Name", "EMP_ID", "Designation"}

    def test_invalid_detail_json_degrades_to_raw_string(self, db_session):
        _add_issue(db_session, source_type="sample", detail_json="{not json")
        [row] = lookup_fbo_issues(db_session, fbo_id="FBO-1")
        assert row["detail"] == "{not json"
        assert row["prefill"]["Designation"] == "Food Safety Officer"


class TestFilters:
    def test_only_open_and_permission_granted(self, db_session):
        _add_issue(db_session, state="open")
        _add_issue(db_session, state="closed")
        rows = lookup_fbo_issues(db_session, fbo_id="FBO-1")
        assert len(rows) == 1
        assert rows[0]["state"] == "open"

    def test_issue_id_wins_over_fbo_id(self, db_session):
        first = _add_issue(db_session)
        _add_issue(db_session)
        rows = lookup_fbo_issues(db_session, fbo_id="FBO-1", issue_id=first.id)
        assert len(rows) == 1
        assert rows[0]["issue_id"] == first.id

    def test_newest_first_ordering(self, db_session):
        from datetime import UTC, datetime

        from app.models.issue import FboIssue

        older = FboIssue(
            fbo_id="FBO-1",
            fbo_name="old",
            source_type="inspection",
            state="open",
            fso_name="x",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        newer = FboIssue(
            fbo_id="FBO-1",
            fbo_name="new",
            source_type="inspection",
            state="open",
            fso_name="x",
            created_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        db_session.add_all([older, newer])
        db_session.commit()
        rows = lookup_fbo_issues(db_session, fbo_id="FBO-1")
        assert [r["fbo_name"] for r in rows] == ["new", "old"]

    def test_empty_result_when_no_matches(self, db_session):
        assert lookup_fbo_issues(db_session, fbo_id="NOPE") == []
