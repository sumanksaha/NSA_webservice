"""Slice 4/5: Corrective Measures Implemented route + Open Issues semantics.

Replaces dismissal: any FSO can assert corrective measures at any time (no
deadline precondition), audited with who/when. Open Issues lists every
inspection that is neither corrective-implemented nor adjudication-linked,
regardless of compliance deadline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _setup_test_env():
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    ctx = app.app_context()
    ctx.push()

    db.drop_all()
    db.create_all()

    user = User(username="testfso", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)

    return app, client, ctx


def _teardown_test_env(ctx):
    from app.extensions import db

    db.session.remove()
    db.drop_all()
    ctx.pop()


def _make_inspection(days_offset: int):
    from app.extensions import db
    from app.models.inspection import Inspection

    insp = Inspection(
        inspection_code=f"INSP-CORR-{days_offset}",
        fso_name="Test Officer",
        fssai_license="12345678901234",
        fbo_name="Corr Test FBO",
        fbo_address="Test Address",
        concerned_food="Milk",
        problem="Unclean premises",
        inspection_date=datetime.now(UTC),
        compliance_deadline=datetime.now(UTC) + timedelta(days=days_offset),
    )
    db.session.add(insp)
    db.session.commit()
    return insp.id


class TestCorrectiveMeasures:
    def test_implement_corrective_sets_flag_who_when_any_deadline(self):
        """Future-deadline inspection can still get corrective measures (Q8)."""
        app, client, ctx = _setup_test_env()
        try:
            with app.app_context():
                iid = _make_inspection(days_offset=10)  # NOT past deadline

            resp = client.post(f"/inspection/{iid}/implement_corrective_measures")
            assert resp.status_code == 200, resp.data
            assert "successfully" in resp.get_json()["message"]

            with app.app_context():
                from app.extensions import db
                from app.models.inspection import Inspection

                row = db.session.get(Inspection, iid)
                assert row.is_dismissed  # internal flag name unchanged
                assert row.dismissed_by == "Test Officer"
                assert row.dismissed_at is not None
        finally:
            _teardown_test_env(ctx)

    def test_implement_corrective_twice_rejected(self):
        app, client, ctx = _setup_test_env()
        try:
            with app.app_context():
                iid = _make_inspection(days_offset=-5)
                from app.extensions import db
                from app.models.inspection import Inspection

                db.session.get(Inspection, iid).is_dismissed = True
                db.session.commit()

            resp = client.post(f"/inspection/{iid}/implement_corrective_measures")
            assert resp.status_code == 400
        finally:
            _teardown_test_env(ctx)

    def test_old_dismiss_route_gone(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/inspection/999999/dismiss")
            assert resp.status_code == 404
        finally:
            _teardown_test_env(ctx)

    def test_open_issues_includes_future_and_overdue_excludes_corrective(self):
        """Open Issues drops the deadline filter; corrective-implemented excluded."""
        app, client, ctx = _setup_test_env()
        try:
            with app.app_context():
                _make_inspection(days_offset=30)
                _make_inspection(days_offset=-30)
                overdue_closed = _make_inspection(days_offset=-1)
                from app.extensions import db
                from app.models.inspection import Inspection

                db.session.get(Inspection, overdue_closed).is_dismissed = True
                db.session.commit()

            html = client.get("/inspection/open").get_data(as_text=True)
            # both non-corrective inspections visible regardless of deadline...
            assert "INSP-CORR-30" in html
            assert "INSP-CORR--30" in html
            # ...but the corrective-implemented one is not
            assert "INSP-CORR--1" not in html
        finally:
            _teardown_test_env(ctx)
