"""Tests for Inspection Checklist capture (Seam 1).

Behavior: an FSO submits inspection details including the 12-item checklist;
the checklist is stored and retrievable through the inspection detail API.
"""

from __future__ import annotations


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

    # is_admin: Phase 18 scoping stamps create with scoped_officer_name();
    # these tests exercise checklist capture, not scoping — admins are unscoped.
    user = User(username="testfso", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
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


def _base_form(**overrides):
    form = {
        "food_safety_officer_name": "Test Officer",
        "inspection_date": "2026-08-20",
        "compliance_deadline": "2026-09-20",
        "fbo_name": "Acme Foods",
    }
    form.update(overrides)
    return form


class TestChecklistCapture:
    def test_create_with_checklist_stores_and_returns_it(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post(
                "/inspection/create",
                data=_base_form(clean_premise="no", license_display="no"),
            )
            assert resp.status_code in (200, 201), resp.data
            body = resp.get_json()
            inspection_id = body.get("inspection_id") if isinstance(body, dict) else None
            assert inspection_id is not None

            detail = client.get(f"/inspection/{inspection_id}")
            assert detail.status_code == 200
            data = detail.get_json()
            assert data["checklist"]["clean_premise"] == "no"
            assert data["checklist"]["license_display"] == "no"
        finally:
            _teardown_test_env(ctx)

    def test_create_without_checklist_leaves_it_empty(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/inspection/create", data=_base_form())
            assert resp.status_code in (200, 201)
            inspection_id = resp.get_json()["inspection_id"]

            data = client.get(f"/inspection/{inspection_id}").get_json()
            assert data["checklist"] in ({}, None)
        finally:
            _teardown_test_env(ctx)

    def test_violation_defaults_follow_adjudication_conventions(self):
        """artificial_colour / Expired_item default 'no' (= violation when 'yes');
        the other ten default 'yes' (= violation when 'no'). Unspecified items
        are not force-filled — absence means not assessed."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/inspection/create", data=_base_form())
            assert resp.status_code in (200, 201)
            data = client.get(f"/inspection/{resp.get_json()['inspection_id']}").get_json()
            # nothing pre-filled: only what FSO answered is stored
            assert data["checklist"] in ({}, None)
        finally:
            _teardown_test_env(ctx)
