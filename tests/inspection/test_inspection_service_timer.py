"""Tests for improvement notice service date, 15 working-day timer,
re-inspection shortcomings, and escalation to non-sample adjudication."""

from datetime import UTC, datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import FSO, Inspection, User
from werkzeug.security import generate_password_hash


def _make_app():
    app = create_app()
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def _login(client, username="inspector", password="password123"):
    with client.application.app_context():
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
    return client.post("/auth/login", data={"username": username, "password": password})


def _seed(app, suffix=""):
    with app.app_context():
        fso_name = f"Test Officer {suffix}"
        fso = FSO(fso_name=fso_name)
        db.session.add(fso)
        db.session.flush()
        insp = Inspection(
            fso_name=fso_name,
            fbo_name="Test FBO",
            fbo_address="123 Test St",
            fssai_license="123456789",
            inspection_date=datetime.now(UTC) - timedelta(days=10),
            compliance_deadline=datetime.now(UTC) + timedelta(days=30),
            problem="Test problem",
        )
        db.session.add(insp)
        db.session.commit()
        return insp.id


def test_serve_notice_sets_timer():
    app = _make_app()
    insp_id = _seed(app, "timer")
    client = app.test_client()
    _login(client, username="inspector-timer")
    served = "2026-11-02"
    resp = client.post(
        f"/inspection/{insp_id}/serve-notice",
        data={"service_date": served},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["served_at"].startswith(served)
    assert data["reinspection_due_at"] is not None

    with app.app_context():
        insp = db.session.get(Inspection, insp_id)
        assert insp.improvement_notice_served_at is not None
        assert insp.reinspection_due_at > insp.improvement_notice_served_at


def test_mark_shortcomings_and_escalate():
    app = _make_app()
    insp_id = _seed(app, "escalate")
    client = app.test_client()
    _login(client, username="inspector-escalate")

    # escalation must be blocked before shortcomings persist
    resp = client.post(f"/inspection/{insp_id}/escalate-adjudication")
    assert resp.status_code == 400

    # mark shortcomings persist
    resp = client.post(
        f"/inspection/{insp_id}/mark-shortcomings",
        json={"persist": True, "shortcomings": ["Pest control", "Water"]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["shortcomings_persist"] is True

    # now escalation should succeed
    resp = client.post(f"/inspection/{insp_id}/escalate-adjudication")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["message"] == "escalated"
    assert data["adjudication_id"] is not None

    with app.app_context():
        insp = db.session.get(Inspection, insp_id)
        assert insp.adjudication_id is not None
