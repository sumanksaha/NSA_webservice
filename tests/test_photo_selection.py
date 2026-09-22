"""Tests for the photo-selection seam (app/shared/photo_selection.py).

"Which photos go in the document" lived in seven copies behind route
handlers (some request-coupled, some silently PASS-only). The seam owns
filtering, flagged-photo policy + audit, and embedding; callers pass
explicit ids and flags — no request context, no filesystem.
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest

from app.extensions import db
from app.models import Evidence, User
from app.shared import photo_selection


@pytest.fixture()
def app_ctx():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["DISABLE_RBAC"] = True

    with app.app_context():
        db.create_all()
        db.session.add(User(username="testuser", password_hash="pbkdf2:sha256$test$dummy", is_admin=True))
        db.session.commit()
        yield app
        db.drop_all()


def _photo(**kwargs):
    defaults = {
        "evidence_type": "photo",
        "filepath": "/tmp/p.jpg",
        "filename": "p.jpg",
        "verification_status": "PASS",
        "captured_at": datetime(2026, 1, 10),
    }
    defaults.update(kwargs)
    photo = Evidence(**defaults)
    db.session.add(photo)
    db.session.commit()
    return photo


@pytest.fixture()
def seeded(app_ctx):
    _photo(id="pass-case", case_id=1, captured_at=datetime(2026, 1, 10))
    _photo(id="flag-case", case_id=1, verification_status="FLAG", captured_at=datetime(2026, 1, 11))
    _photo(id="pass-adj", adjudication_id=1, captured_at=datetime(2026, 1, 9))
    return app_ctx


def _embedder(paths):
    return [f"EMBED:{p}" for p in paths]


class TestSelectForDocument:
    def test_pass_only_by_default(self, seeded):
        sel = photo_selection.select_for_document(case_id=1, embedder=_embedder)
        assert [p.id for p in sel.photos] == ["pass-case"]
        assert sel.embeds == ["EMBED:/tmp/p.jpg"]
        assert sel.flagged_included is False

    def test_flagged_included_with_reason_and_audited(self, seeded):
        auditor = mock.Mock()
        sel = photo_selection.select_for_document(
            case_id=1, include_flagged=True, flag_reason="visible damage",
            actor="FSO", embedder=_embedder, auditor=auditor,
        )
        assert [p.id for p in sel.photos] == ["pass-case", "flag-case"]
        assert sel.flagged_included is True
        auditor.log.assert_called_once()
        _, kwargs = auditor.log.call_args
        assert kwargs["reason"] == "visible damage"

    def test_flagged_without_reason_raises(self, seeded):
        with pytest.raises(photo_selection.FlagReasonRequired):
            photo_selection.select_for_document(case_id=1, include_flagged=True, embedder=_embedder)

    def test_ids_are_not_conflated(self, seeded):
        # Same integer on the other track must not leak in (the old code
        # OR-ed both columns against one id).
        adj_sel = photo_selection.select_for_document(adjudication_id=1, embedder=_embedder)
        assert [p.id for p in adj_sel.photos] == ["pass-adj"]
        case_sel = photo_selection.select_for_document(case_id=1, embedder=_embedder)
        assert "pass-adj" not in [p.id for p in case_sel.photos]

    def test_exactly_one_id_required(self, seeded):
        with pytest.raises(ValueError):
            photo_selection.select_for_document(embedder=_embedder)
        with pytest.raises(ValueError):
            photo_selection.select_for_document(case_id=1, adjudication_id=1, embedder=_embedder)

    def test_ordered_by_capture_time(self, seeded):
        _photo(id="earlier", case_id=1, captured_at=datetime(2026, 1, 1))
        sel = photo_selection.select_for_document(case_id=1, embedder=_embedder)
        assert [p.id for p in sel.photos] == ["earlier", "pass-case"]
