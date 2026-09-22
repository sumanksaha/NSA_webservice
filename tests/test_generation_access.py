"""Tests for the generation-access seam (app/shared/generation_access.py).

"May this document generate?" was three checks (30-day embargo,
authorization date, pre-auth routing) sequenced per route across ten
call sites; omission order caused 403/200 mismatches. The seam answers
per document type in one fixed order:

  1. embargo (case_file track only — current behavior),
  2. authorization (petition-class documents, unless the caller opts
     into the pre-authorization draft exception),
  3. allowed.

Pre-auth "no petition" 400s stay at the routes (routing, not a gate).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.shared import generation_access


def _recent():
    return datetime.now(UTC) - timedelta(days=5)


def _old():
    return datetime(2026, 7, 7)


class TestCaseFilePetition:
    def test_no_dates_allows(self):
        d = generation_access.check_generation_allowed(
            case_type="case_file", doc_type="petition",
            authorization_date=datetime(2026, 7, 1),
        )
        assert d.allowed is True and d.status is None

    def test_recent_handover_blocks_with_dates(self):
        d = generation_access.check_generation_allowed(
            case_type="case_file", doc_type="petition",
            authorization_date=datetime(2026, 7, 1),
            retailer_receive=_recent(),
        )
        assert d.allowed is False and d.status == 403
        assert d.earliest_allowed_date and d.handover_date
        assert "30 days" in d.error

    def test_missing_authorization_blocks(self):
        d = generation_access.check_generation_allowed(
            case_type="case_file", doc_type="petition",
            authorization_date=None, retailer_receive=_old(),
        )
        assert d.allowed is False and d.status == 403
        assert "authorization" in d.error

    def test_embargo_wins_over_authorization(self):
        d = generation_access.check_generation_allowed(
            case_type="case_file", doc_type="petition",
            authorization_date=None, retailer_receive=_recent(),
        )
        assert d.allowed is False
        assert "30 days" in d.error

    def test_regenerate_draft_exception_skips_authorization(self):
        d = generation_access.check_generation_allowed(
            case_type="case_file", doc_type="both", require_authorization=False,
            authorization_date=None, retailer_receive=_old(),
        )
        assert d.allowed is True

    def test_regenerate_still_enforces_embargo(self):
        d = generation_access.check_generation_allowed(
            case_type="case_file", doc_type="both", require_authorization=False,
            authorization_date=None, retailer_receive=_recent(),
        )
        assert d.allowed is False and d.status == 403


class TestCaseFilePermission:
    def test_permission_needs_no_authorization(self):
        d = generation_access.check_generation_allowed(
            case_type="case_file", doc_type="permission",
            authorization_date=None, retailer_receive=_old(),
        )
        assert d.allowed is True

    def test_permission_enforces_embargo(self):
        d = generation_access.check_generation_allowed(
            case_type="case_file", doc_type="permission",
            authorization_date=None, retailer_receive=_recent(),
        )
        assert d.allowed is False and d.status == 403


class TestAdjudication:
    def test_no_embargo_on_adjudication_track(self):
        # Current behavior pinned: the handover embargo applies to the
        # sample track only.
        d = generation_access.check_generation_allowed(
            case_type="adjudication", doc_type="petition",
            authorization_date=datetime(2026, 7, 1), retailer_receive=_recent(),
        )
        assert d.allowed is True

    def test_petition_needs_authorization(self):
        d = generation_access.check_generation_allowed(
            case_type="adjudication", doc_type="petition", authorization_date=None,
        )
        assert d.allowed is False and d.status == 403

    def test_permission_open(self):
        d = generation_access.check_generation_allowed(
            case_type="adjudication", doc_type="permission", authorization_date=None,
        )
        assert d.allowed is True
