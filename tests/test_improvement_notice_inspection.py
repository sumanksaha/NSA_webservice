"""Tests for the inspection-keyed Improvement Notice (Seam 2).

Behavior under test:
- GET html renders violation titles + FBO details, freezes the record on first access
- Frozen records reject updates (verified through the inspection update route)
- Inspections without violations are refused (no junk legal paper)
- PDF download serves a PDF document
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


def _base_form(**overrides):
    form = {
        "food_safety_officer_name": "Test Officer",
        "inspection_date": "2026-08-20",
        "compliance_deadline": "2026-09-20",
        "fbo_name": "Acme Foods",
    }
    form.update(overrides)
    return form


def _create_inspection(client, **form_overrides):
    resp = client.post("/inspection/create", data=_base_form(**form_overrides))
    assert resp.status_code == 201, resp.data
    return resp.get_json()["inspection_id"]


VIOLATING_CHECKLIST = {"clean_premise": "no", "license_display": "no"}


class TestInspectionImprovementNotice:
    def test_html_renders_violations_and_freezes_record(self):
        _app, client, ctx = _setup_test_env()
        try:
            form = dict(_base_form(fbo_name="Sharma Sweets", problem="Unhygienic storage"))
            form.update(VIOLATING_CHECKLIST)
            insp_id = _create_inspection(client, **form)

            page = client.get(f"/food-cell/improvement-notice/inspection/{insp_id}/html")
            assert page.status_code == 200
            html = page.data.decode("utf-8")
            assert "Unclean Premises" in html
            assert "Improper License Display" in html
            assert "Sharma Sweets" in html

            # First render froze the record: updates now conflict
            frozen = client.put(f"/inspection/{insp_id}", data={"fbo_name": "Renamed"})
            assert frozen.status_code == 409
        finally:
            _teardown_test_env(ctx)

    def test_notice_refused_without_violations(self):
        _app, client, ctx = _setup_test_env()
        try:
            insp_id = _create_inspection(client)  # no checklist → no violations

            page = client.get(f"/food-cell/improvement-notice/inspection/{insp_id}/html")
            assert page.status_code == 400
            assert b"violation" in page.data.lower()

            # Refusal must not freeze the record
            editable = client.put(f"/inspection/{insp_id}", data={"fbo_name": "Renamed"})
            assert editable.status_code != 409
        finally:
            _teardown_test_env(ctx)

    def test_pdf_download_serves_pdf(self):
        _app, client, ctx = _setup_test_env()
        try:
            form = dict(_base_form())
            form.update(VIOLATING_CHECKLIST)
            insp_id = _create_inspection(client, **form)

            pdf = client.get(f"/food-cell/improvement-notice/inspection/{insp_id}/pdf")
            assert pdf.status_code == 200
            assert pdf.data[:5] == b"%PDF-"
        finally:
            _teardown_test_env(ctx)

    def test_unknown_inspection_is_404(self):
        _app, client, ctx = _setup_test_env()
        try:
            page = client.get("/food-cell/improvement-notice/inspection/99999/html")
            assert page.status_code == 404
        finally:
            _teardown_test_env(ctx)
