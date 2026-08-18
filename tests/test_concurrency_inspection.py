"""Tests for optimistic concurrency control (S9a).

Verifies that concurrent modifications to Inspection and Sample records
trigger ``StaleDataError`` and the routes return HTTP 409.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy.orm.exc import StaleDataError


def _patch_commit_stale():
    """Patch the scoped-session's commit to raise StaleDataError.

    Flask-SQLAlchemy's ``db.session`` is a ``scoped_session`` with a proxied
    ``commit`` method.  Patching the instance directly is the most reliable
    approach — class-level patches on ``Session`` can miss the proxy.
    """
    from app.extensions import db

    return patch.object(db.session, "commit", side_effect=StaleDataError("version mismatch"))


def _setup_test_env():
    """Create a test app with in-memory SQLite, a user, and FSO."""
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

    user = User(username="testfso", password_hash="pbkdf2:sha256$test$dummy")
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


# --------------------------------------------------------------------------- #
# Inspection concurrency
# --------------------------------------------------------------------------- #


class TestInspectionConcurrency:
    def test_put_returns_409_on_staledataerror(self):
        from app.extensions import db
        from app.models import Inspection

        _app, client, ctx = _setup_test_env()
        try:
            insp = Inspection(
                inspection_code="T1",
                fso_name="Test Officer",
                fbo_name="FBO",
                inspection_date=datetime.now(UTC),
                compliance_deadline=datetime.now(UTC),
            )
            db.session.add(insp)
            db.session.commit()
            insp_id = insp.id

            with _patch_commit_stale():
                resp = client.put(f"/inspection/{insp_id}", data={"fbo_name": "Updated"})
            assert resp.status_code == 409
            assert b"Conflict" in resp.data
        finally:
            _teardown_test_env(ctx)

    def test_delete_returns_409_on_staledataerror(self):
        from app.extensions import db
        from app.models import Inspection

        _app, client, ctx = _setup_test_env()
        try:
            insp = Inspection(
                inspection_code="T2",
                fso_name="Test Officer",
                fbo_name="FBO2",
                inspection_date=datetime.now(UTC),
                compliance_deadline=datetime.now(UTC),
            )
            db.session.add(insp)
            db.session.commit()
            insp_id = insp.id

            with _patch_commit_stale():
                resp = client.delete(f"/inspection/{insp_id}")
            assert resp.status_code == 409
            assert b"Conflict" in resp.data
        finally:
            _teardown_test_env(ctx)


# --------------------------------------------------------------------------- #
# Sample concurrency
# --------------------------------------------------------------------------- #


class TestSampleConcurrency:
    def test_put_returns_409_on_concurrent_modification(self):
        from app.extensions import db
        from app.models import Sample

        _app, client, ctx = _setup_test_env()
        try:
            samp = Sample(
                sample_code="SMP-001",
                sample_name="Milk",
                sample_type="enforcement",
                fso_name="Test Officer",
                collection_date=datetime.now(UTC),
            )
            db.session.add(samp)
            db.session.commit()
            samp_id = samp.id

            with _patch_commit_stale():
                resp = client.put(f"/sample/{samp_id}", data={"sample_name": "Updated"})
            assert resp.status_code == 409
            assert b"Conflict" in resp.data
        finally:
            _teardown_test_env(ctx)

    def test_delete_returns_409_on_staledataerror(self):
        from app.extensions import db
        from app.models import Sample

        _app, client, ctx = _setup_test_env()
        try:
            samp = Sample(
                sample_code="SMP-002",
                sample_name="Water",
                sample_type="surveillance",
                fso_name="Test Officer",
                collection_date=datetime.now(UTC),
            )
            db.session.add(samp)
            db.session.commit()
            samp_id = samp.id

            with _patch_commit_stale():
                resp = client.delete(f"/sample/{samp_id}")
            assert resp.status_code == 409
            assert b"Conflict" in resp.data
        finally:
            _teardown_test_env(ctx)
