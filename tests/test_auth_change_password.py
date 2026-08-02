"""Tests for the authenticated change-password flow.

Covers:
- GET /auth/change-password requires login (redirects to /auth/login)
- GET renders the form for an authenticated user
- POST with correct current password updates the hash
- POST with wrong current password is rejected
- POST with mismatched confirmation is rejected
- POST with a too-short new password is rejected
- POST with the same password as current is rejected
"""

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import User

CURRENT_PASSWORD = "correct-horse-9"  # noqa: S105 — test fixture password
NEW_PASSWORD = "battery-staple-42"  # noqa: S105 — test fixture password


@pytest.fixture
def test_client():
    """Test client with an in-memory DB and a seeded, loggable-in user."""
    from app import create_app

    # DB is isolated to a temp SQLite file by tests/conftest.py (the
    # SQLALCHEMY_DATABASE_URI override below would NOT take effect because
    # create_app() runs at import time and already bound the engine).
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            user = User(
                username="admin",
                password_hash=generate_password_hash(CURRENT_PASSWORD),
            )
            db.session.add(user)
            db.session.commit()
        yield client
        with app.app_context():
            db.drop_all()


def _login(client, username="admin", password=CURRENT_PASSWORD):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


class TestChangePasswordAuth:
    def test_requires_login(self, test_client):
        """GET /auth/change-password without login redirects to /auth/login."""
        resp = test_client.get("/auth/change-password", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_renders_form_when_authenticated(self, test_client):
        """GET renders the change-password form for a logged-in user."""
        _login(test_client)
        resp = test_client.get("/auth/change-password", follow_redirects=False)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert 'name="current_password"' in html
        assert 'name="new_password"' in html
        assert 'name="confirm_password"' in html
        assert "{{" not in html


class TestChangePasswordPost:
    def test_success_updates_hash(self, test_client):
        """POST with correct current password updates the stored hash."""
        _login(test_client)
        resp = test_client.post(
            "/auth/change-password",
            data={
                "current_password": CURRENT_PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            user = User.query.filter_by(username="admin").first()
            assert check_password_hash(user.password_hash, NEW_PASSWORD)
            assert not check_password_hash(user.password_hash, CURRENT_PASSWORD)

    def test_wrong_current_password_rejected(self, test_client):
        """POST with a wrong current password is rejected and hash unchanged."""
        _login(test_client)
        resp = test_client.post(
            "/auth/change-password",
            data={
                "current_password": "wrong-password",
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        html = resp.data.decode("utf-8")
        assert "Current password is incorrect" in html
        with test_client.application.app_context():
            user = User.query.filter_by(username="admin").first()
            assert check_password_hash(user.password_hash, CURRENT_PASSWORD)

    def test_mismatched_confirmation_rejected(self, test_client):
        """POST with mismatched confirmation is rejected."""
        _login(test_client)
        resp = test_client.post(
            "/auth/change-password",
            data={
                "current_password": CURRENT_PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": "different-password",
            },
        )
        assert "do not match" in resp.data.decode("utf-8")

    def test_too_short_new_password_rejected(self, test_client):
        """POST with a new password under 8 chars is rejected."""
        _login(test_client)
        resp = test_client.post(
            "/auth/change-password",
            data={
                "current_password": CURRENT_PASSWORD,
                "new_password": "short",
                "confirm_password": "short",
            },
        )
        assert "at least 8 characters" in resp.data.decode("utf-8")

    def test_same_password_rejected(self, test_client):
        """POST reusing the current password is rejected."""
        _login(test_client)
        resp = test_client.post(
            "/auth/change-password",
            data={
                "current_password": CURRENT_PASSWORD,
                "new_password": CURRENT_PASSWORD,
                "confirm_password": CURRENT_PASSWORD,
            },
        )
        assert "must be different" in resp.data.decode("utf-8")

    def test_missing_fields_rejected(self, test_client):
        """POST with missing fields is rejected."""
        _login(test_client)
        resp = test_client.post(
            "/auth/change-password",
            data={"current_password": "", "new_password": "", "confirm_password": ""},
        )
        assert "All fields are required" in resp.data.decode("utf-8")
