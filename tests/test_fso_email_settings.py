"""Tests for the FSO email (SMTP) settings admin routes.

Covers:
- GET /auth/fso-email — list page renders with FSOs
- GET /auth/fso-email/<name> — edit form renders
- POST /auth/fso-email/<name> — saves SMTP config
- 404 for unknown FSO
- Non-admin access denied
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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

    admin = User(username="admin_email", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
    regular = User(username="regular_email", password_hash="pbkdf2:sha256$test$dummy", is_admin=False)
    db.session.add(admin)
    db.session.add(regular)
    db.session.add(FSO(fso_name="Officer Alpha"))
    db.session.add(FSO(fso_name="Officer Beta"))
    db.session.commit()

    admin_client = app.test_client()
    with admin_client.session_transaction() as sess:
        sess["_user_id"] = str(admin.id)

    regular_client = app.test_client()
    with regular_client.session_transaction() as sess:
        sess["_user_id"] = str(regular.id)

    return app, admin_client, regular_client, ctx


def _teardown_test_env(ctx):
    from app.extensions import db

    db.session.remove()
    db.drop_all()
    ctx.pop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFsoEmailList:
    def test_list_renders_200(self, env):
        app, admin_client, _, ctx = env
        resp = admin_client.get("/auth/fso-email")
        assert resp.status_code == 200
        assert b"Officer Alpha" in resp.data
        assert b"Officer Beta" in resp.data

    def test_list_shows_not_configured(self, env):
        app, admin_client, _, ctx = env
        resp = admin_client.get("/auth/fso-email")
        assert b"Not Configured" in resp.data

    def test_list_denied_for_non_admin(self, env):
        app, _, regular_client, ctx = env
        resp = regular_client.get("/auth/fso-email")
        # Non-admin should be redirected or 403
        assert resp.status_code in (302, 403)


class TestFsoEmailEdit:
    def test_edit_form_renders(self, env):
        app, admin_client, _, ctx = env
        resp = admin_client.get("/auth/fso-email/Officer%20Alpha")
        assert resp.status_code == 200
        assert b"Officer Alpha" in resp.data
        assert b"SMTP Host" in resp.data

    def test_edit_404_for_unknown_fso(self, env):
        app, admin_client, _, ctx = env
        resp = admin_client.get("/auth/fso-email/Nonexistent")
        assert resp.status_code == 404

    def test_post_saves_config(self, env):
        app, admin_client, _, ctx = env
        resp = admin_client.post(
            "/auth/fso-email/Officer%20Alpha",
            data={
                "email": "alpha@kmc.gov.in",
                "smtp_host": "smtp.kmc.gov.in",
                "smtp_port": "587",
                "smtp_user": "alpha@kmc.gov.in",
                "smtp_password": "secret123",
                "smtp_use_tls": "on",
            },
        )
        # Should redirect to list
        assert resp.status_code == 302

        # Verify saved in DB
        from app.extensions import db
        from app.models.inspection import FSO

        with app.app_context():
            fso = db.session.get(FSO, "Officer Alpha")
            assert fso.email == "alpha@kmc.gov.in"
            assert fso.smtp_host == "smtp.kmc.gov.in"
            assert fso.smtp_port == 587
            assert fso.smtp_user == "alpha@kmc.gov.in"
            assert fso.smtp_password == "secret123"
            assert fso.smtp_use_tls is True

    def test_post_saves_without_tls(self, env):
        app, admin_client, _, ctx = env
        resp = admin_client.post(
            "/auth/fso-email/Officer%20Beta",
            data={
                "email": "beta@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_port": "25",
                "smtp_user": "beta@example.com",
                "smtp_password": "pass456",
                # smtp_use_tls NOT checked
            },
        )
        assert resp.status_code == 302

        from app.extensions import db
        from app.models.inspection import FSO

        with app.app_context():
            fso = db.session.get(FSO, "Officer Beta")
            assert fso.smtp_use_tls is False

    def test_post_clears_config(self, env):
        """Submitting empty fields clears the email config."""
        app, admin_client, _, ctx = env
        resp = admin_client.post(
            "/auth/fso-email/Officer%20Alpha",
            data={
                "email": "",
                "smtp_host": "",
                "smtp_port": "",
                "smtp_user": "",
                "smtp_password": "",
                "smtp_use_tls": "on",
            },
        )
        assert resp.status_code == 302

        from app.extensions import db
        from app.models.inspection import FSO

        with app.app_context():
            fso = db.session.get(FSO, "Officer Alpha")
            assert fso.email is None
            assert fso.smtp_host is None

    def test_edit_denied_for_non_admin(self, env):
        app, _, regular_client, ctx = env
        resp = regular_client.get("/auth/fso-email/Officer%20Alpha")
        assert resp.status_code in (302, 403)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def env():
    app, admin_client, regular_client, ctx = _setup_test_env()
    yield app, admin_client, regular_client, ctx
    _teardown_test_env(ctx)
