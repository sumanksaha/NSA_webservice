"""Tests for the admin user-management flows (create / reset / toggle / delete).

Covers:
- GET /auth/users requires login (redirects to /auth/login)
- GET /auth/users and the create/reset/toggle/delete endpoints are blocked for non-admins (403)
- GET /auth/users lists users for an admin
- POST /auth/users/create creates a user, validates input, and audits
- POST /auth/users/<id>/reset-password updates the target's hash and audits
- POST rejects too-short / mismatched / missing passwords
- POST for an unknown user returns 404
- POST /auth/users/<id>/toggle-admin grants/revokes admin and audits
- POST /auth/users/<id>/delete removes the account and audits
- Self-demotion / self-deletion are blocked; last-admin demotion / deletion is blocked
"""

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import RecordAudit, User

ADMIN_PASSWORD = "admin-pass-77"  # noqa: S105 — test fixture password
USER_PASSWORD = "user-pass-88"  # noqa: S105 — test fixture password
NEW_PASSWORD = "fresh-pass-99"  # noqa: S105 — test fixture password
NEW_USER_PASSWORD = "brand-new-pass-1"  # noqa: S105 — test fixture password


@pytest.fixture
def test_client():
    """Test client with an in-memory DB seeded with an admin + a regular user."""
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
            admin = User(
                username="admin",
                password_hash=generate_password_hash(ADMIN_PASSWORD),
                is_admin=True,
            )
            officer = User(
                username="officer",
                password_hash=generate_password_hash(USER_PASSWORD),
                is_admin=False,
            )
            db.session.add_all([admin, officer])
            db.session.commit()
        yield client
        with app.app_context():
            db.drop_all()


def _login(client, username="admin", password=ADMIN_PASSWORD):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def _officer_id():
    return User.query.filter_by(username="officer").first().id


class TestAdminCreateUser:
    def test_create_requires_login(self, test_client):
        """GET /auth/users/create without login redirects to /auth/login."""
        resp = test_client.get("/auth/users/create", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_non_admin_blocked_from_create(self, test_client):
        """A logged-in non-admin gets 403 on the create endpoint."""
        _login(test_client, "officer", USER_PASSWORD)
        resp = test_client.post(
            "/auth/users/create",
            data={"username": "newbie", "password": NEW_USER_PASSWORD, "confirm_password": NEW_USER_PASSWORD},
        )
        assert resp.status_code == 403

    def test_create_renders_form(self, test_client):
        """GET renders the create-user form for an admin."""
        _login(test_client)
        resp = test_client.get("/auth/users/create")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert 'name="username"' in html
        assert 'name="password"' in html
        assert 'name="confirm_password"' in html
        assert 'name="is_admin"' in html

    def test_create_success_regular_user(self, test_client):
        """Admin creates a regular user; hash set and audit recorded."""
        _login(test_client)
        resp = test_client.post(
            "/auth/users/create",
            data={
                "username": "newbie",
                "password": NEW_USER_PASSWORD,
                "confirm_password": NEW_USER_PASSWORD,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            newbie = User.query.filter_by(username="newbie").first()
            assert newbie is not None
            assert newbie.is_admin is False
            assert check_password_hash(newbie.password_hash, NEW_USER_PASSWORD)
            audit = RecordAudit.query.filter_by(action="user_created").first()
            assert audit is not None
            assert audit.record_id == str(newbie.id)
            assert audit.user_id is not None  # actor recorded

    def test_create_success_admin_user(self, test_client):
        """Admin creates a user with admin rights when the checkbox is on."""
        _login(test_client)
        resp = test_client.post(
            "/auth/users/create",
            data={
                "username": "newadmin",
                "password": NEW_USER_PASSWORD,
                "confirm_password": NEW_USER_PASSWORD,
                "is_admin": "on",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            newadmin = User.query.filter_by(username="newadmin").first()
            assert newadmin is not None
            assert newadmin.is_admin is True

    def test_create_duplicate_username_rejected(self, test_client):
        """Creating a user with an existing username is rejected."""
        _login(test_client)
        resp = test_client.post(
            "/auth/users/create",
            data={
                "username": "officer",  # already seeded
                "password": NEW_USER_PASSWORD,
                "confirm_password": NEW_USER_PASSWORD,
            },
        )
        html = resp.data.decode("utf-8")
        assert "already taken" in html
        with test_client.application.app_context():
            assert User.query.filter_by(username="officer").count() == 1

    def test_create_too_short_password_rejected(self, test_client):
        """Passwords under 8 chars are rejected."""
        _login(test_client)
        resp = test_client.post(
            "/auth/users/create",
            data={
                "username": "newbie",
                "password": "short",
                "confirm_password": "short",
            },
        )
        assert "at least 8 characters" in resp.data.decode("utf-8")

    def test_create_mismatched_confirmation_rejected(self, test_client):
        """Mismatched confirmation is rejected."""
        _login(test_client)
        resp = test_client.post(
            "/auth/users/create",
            data={
                "username": "newbie",
                "password": NEW_USER_PASSWORD,
                "confirm_password": "different-password",
            },
        )
        assert "do not match" in resp.data.decode("utf-8")

    def test_create_missing_fields_rejected(self, test_client):
        """Missing fields are rejected."""
        _login(test_client)
        resp = test_client.post(
            "/auth/users/create",
            data={"username": "", "password": "", "confirm_password": ""},
        )
        assert "All fields are required" in resp.data.decode("utf-8")

    def test_created_user_can_login(self, test_client):
        """A user created via the UI can log in immediately."""
        _login(test_client)
        test_client.post(
            "/auth/users/create",
            data={
                "username": "newbie",
                "password": NEW_USER_PASSWORD,
                "confirm_password": NEW_USER_PASSWORD,
            },
        )
        test_client.get("/auth/logout")
        resp = test_client.post(
            "/auth/login",
            data={"username": "newbie", "password": NEW_USER_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 302


class TestAdminUsersAccess:
    def test_requires_login(self, test_client):
        """GET /auth/users without login redirects to /auth/login."""
        resp = test_client.get("/auth/users", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_non_admin_blocked_from_list(self, test_client):
        """A logged-in non-admin gets 403 on the user list."""
        _login(test_client, "officer", USER_PASSWORD)
        resp = test_client.get("/auth/users")
        assert resp.status_code == 403

    def test_non_admin_blocked_from_reset(self, test_client):
        """A logged-in non-admin gets 403 on the reset endpoint."""
        _login(test_client, "officer", USER_PASSWORD)
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(
            f"/auth/users/{target_id}/reset-password",
            data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
        )
        assert resp.status_code == 403

    def test_admin_views_user_list(self, test_client):
        """Admin sees the user list with both accounts."""
        _login(test_client)
        resp = test_client.get("/auth/users")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "admin" in html
        assert "officer" in html
        # The list page must not contain password entry fields.
        assert 'name="new_password"' not in html
        assert 'name="confirm_password"' not in html


class TestAdminResetPassword:
    def test_reset_success_updates_hash_and_audits(self, test_client):
        """Admin reset updates the target hash and records an audit row."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(
            f"/auth/users/{target_id}/reset-password",
            data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            officer = User.query.filter_by(username="officer").first()
            assert check_password_hash(officer.password_hash, NEW_PASSWORD)
            assert not check_password_hash(officer.password_hash, USER_PASSWORD)
            audit = RecordAudit.query.filter_by(action="admin_pwd_reset").first()
            assert audit is not None
            assert audit.record_type == "user"
            assert audit.record_id == str(target_id)
            assert audit.user_id is not None  # actor recorded

    def test_reset_unknown_user_404(self, test_client):
        """Reset for a non-existent user id returns 404."""
        _login(test_client)
        resp = test_client.post(
            "/auth/users/999999/reset-password",
            data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
        )
        assert resp.status_code == 404

    def test_reset_too_short_rejected(self, test_client):
        """New passwords under 8 chars are rejected and hash unchanged."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(
            f"/auth/users/{target_id}/reset-password",
            data={"new_password": "short", "confirm_password": "short"},
        )
        html = resp.data.decode("utf-8")
        assert "at least 8 characters" in html
        with test_client.application.app_context():
            officer = User.query.filter_by(username="officer").first()
            assert check_password_hash(officer.password_hash, USER_PASSWORD)

    def test_reset_mismatch_rejected(self, test_client):
        """Mismatched confirmation is rejected."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(
            f"/auth/users/{target_id}/reset-password",
            data={"new_password": NEW_PASSWORD, "confirm_password": "different"},
        )
        assert "do not match" in resp.data.decode("utf-8")

    def test_reset_missing_fields_rejected(self, test_client):
        """Missing fields are rejected."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(
            f"/auth/users/{target_id}/reset-password",
            data={"new_password": "", "confirm_password": ""},
        )
        assert "All fields are required" in resp.data.decode("utf-8")

    def test_reset_password_works_at_login(self, test_client):
        """After an admin reset, the target can log in with the new password."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
        test_client.post(
            f"/auth/users/{target_id}/reset-password",
            data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
        )
        # New session as the target user
        test_client.get("/auth/logout")
        resp = test_client.post(
            "/auth/login",
            data={"username": "officer", "password": NEW_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 302


class TestAdminToggleRole:
    def test_toggle_requires_login(self, test_client):
        """POST toggle-admin without login redirects to /auth/login."""
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(f"/auth/users/{target_id}/toggle-admin", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_non_admin_blocked_from_toggle(self, test_client):
        """A logged-in non-admin gets 403 on the toggle endpoint."""
        _login(test_client, "officer", USER_PASSWORD)
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(f"/auth/users/{target_id}/toggle-admin")
        assert resp.status_code == 403

    def test_toggle_unknown_user_404(self, test_client):
        """Toggle for a non-existent user id returns 404."""
        _login(test_client)
        resp = test_client.post("/auth/users/999999/toggle-admin")
        assert resp.status_code == 404

    def test_admin_promotes_user(self, test_client):
        """Admin grants admin rights and records an audit row."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(
            f"/auth/users/{target_id}/toggle-admin",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            officer = User.query.filter_by(username="officer").first()
            assert officer.is_admin is True
            audit = RecordAudit.query.filter_by(action="admin_promoted").first()
            assert audit is not None
            assert audit.record_id == str(target_id)

    def test_admin_demotes_user(self, test_client):
        """Admin revokes admin rights and records an audit row."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
            officer = User.query.get(target_id)
            officer.is_admin = True
            db.session.commit()
        resp = test_client.post(
            f"/auth/users/{target_id}/toggle-admin",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            officer = User.query.get(target_id)
            assert officer.is_admin is False
            audit = RecordAudit.query.filter_by(action="admin_demoted").first()
            assert audit is not None
            assert audit.record_id == str(target_id)

    def test_self_demotion_blocked(self, test_client):
        """An admin cannot revoke their own admin rights."""
        _login(test_client)
        with test_client.application.app_context():
            admin_id = User.query.filter_by(username="admin").first().id
        resp = test_client.post(
            f"/auth/users/{admin_id}/toggle-admin",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            admin = User.query.filter_by(username="admin").first()
            assert admin.is_admin is True
            assert RecordAudit.query.filter_by(action="admin_demoted").first() is None

    def test_toggle_button_rendered_on_list(self, test_client):
        """The user list shows a Grant/Revoke control and the reset button."""
        _login(test_client)
        resp = test_client.get("/auth/users")
        html = resp.data.decode("utf-8")
        assert "toggle-admin" in html  # per-row form posts to the toggle endpoint
        assert "Reset password" in html
        assert "data-confirm" in html  # destructive actions are guarded client-side


class TestAdminDeleteUser:
    def test_delete_requires_login(self, test_client):
        """POST delete-user without login redirects to /auth/login."""
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(f"/auth/users/{target_id}/delete", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_non_admin_blocked_from_delete(self, test_client):
        """A logged-in non-admin gets 403 on the delete endpoint."""
        _login(test_client, "officer", USER_PASSWORD)
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(f"/auth/users/{target_id}/delete")
        assert resp.status_code == 403

    def test_delete_unknown_user_404(self, test_client):
        """Delete for a non-existent user id returns 404."""
        _login(test_client)
        resp = test_client.post("/auth/users/999999/delete")
        assert resp.status_code == 404

    def test_admin_deletes_user(self, test_client):
        """Admin deletes a user and records an audit row."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
        resp = test_client.post(
            f"/auth/users/{target_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            assert User.query.filter_by(username="officer").first() is None
            audit = RecordAudit.query.filter_by(action="user_deleted").first()
            assert audit is not None
            assert audit.record_id == str(target_id)
            assert audit.user_id is not None  # actor recorded

    def test_self_delete_blocked(self, test_client):
        """An admin cannot delete their own account."""
        _login(test_client)
        with test_client.application.app_context():
            admin_id = User.query.filter_by(username="admin").first().id
        resp = test_client.post(
            f"/auth/users/{admin_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            assert User.query.filter_by(username="admin").first() is not None
            assert RecordAudit.query.filter_by(action="user_deleted").first() is None

    def test_deleted_user_cannot_login(self, test_client):
        """After deletion the account is gone and cannot log in."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
        test_client.post(f"/auth/users/{target_id}/delete")
        test_client.get("/auth/logout")
        resp = test_client.post(
            "/auth/login",
            data={"username": "officer", "password": USER_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 200  # re-renders login with error, no redirect

    def test_delete_nullifies_audit_fk(self, test_client):
        """Audit rows referencing the deleted user survive with user_id NULL."""
        _login(test_client)
        with test_client.application.app_context():
            target_id = _officer_id()
            # Seed an audit row that references the target as the actor.
            db.session.add(
                RecordAudit(
                    user_id=target_id,
                    action="login_success",
                    record_type="auth",
                    record_id=str(target_id),
                )
            )
            db.session.commit()
        resp = test_client.post(
            f"/auth/users/{target_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with test_client.application.app_context():
            # The seeded audit row survives the delete, with its FK nulled.
            seeded = RecordAudit.query.filter_by(action="login_success", record_id=str(target_id)).first()
            assert seeded is not None  # audit history is not wiped
            assert seeded.user_id is None
