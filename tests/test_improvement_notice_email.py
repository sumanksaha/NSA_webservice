"""Tests for the Improvement Notice email sender.

Covers:
- EmailResult dataclass
- _get_fso_smtp_config loading from DB
- send_improvement_notice_email with various scenarios
- Route: POST /food-cell/improvement-notice/inspection/<id>/email
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

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

    user = User(username="emailtest", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
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


def _create_inspection(client, **overrides):
    form = {
        "food_safety_officer_name": "Test Officer",
        "inspection_date": "2026-08-20",
        "compliance_deadline": "2026-09-20",
        "fbo_name": "Acme Foods",
        "clean_premise": "no",
        "license_display": "no",
    }
    form.update(overrides)
    resp = client.post("/inspection/create", data=form)
    assert resp.status_code == 201, resp.data
    return resp.get_json()["inspection_id"]


def _configure_fso_email(app):
    """Set email config on the FSO within an app context."""
    from app.extensions import db
    from app.models.inspection import FSO

    with app.app_context():
        fso = db.session.get(FSO, "Test Officer")
        fso.email = "test@kmc.gov.in"
        fso.smtp_host = "smtp.kmc.gov.in"
        fso.smtp_port = 587
        fso.smtp_user = "test@kmc.gov.in"
        fso.smtp_password = "secret123"
        fso.smtp_use_tls = True
        db.session.commit()


# ---------------------------------------------------------------------------
# Unit tests (all need app context for db access)
# ---------------------------------------------------------------------------


class TestEmailResult:
    def test_success_result(self):
        from app.food_cell.email_sender import EmailResult

        r = EmailResult(success=True, message="Sent OK", details={"to": "a@b.com"})
        assert r.success is True
        assert r.message == "Sent OK"
        assert r.details["to"] == "a@b.com"

    def test_failure_result(self):
        from app.food_cell.email_sender import EmailResult

        r = EmailResult(success=False, error="Auth failed")
        assert r.success is False
        assert r.error == "Auth failed"


class TestGetFsoSmtpConfig:
    def test_returns_config_when_set(self, env):
        app, client, ctx = env
        _configure_fso_email(app)
        from app.food_cell.email_sender import _get_fso_smtp_config

        config = _get_fso_smtp_config("Test Officer")
        assert config is not None
        assert config["email"] == "test@kmc.gov.in"
        assert config["smtp_host"] == "smtp.kmc.gov.in"
        assert config["smtp_port"] == 587
        assert config["smtp_use_tls"] is True

    def test_returns_none_when_no_email(self, env):
        app, client, ctx = env
        from app.food_cell.email_sender import _get_fso_smtp_config

        config = _get_fso_smtp_config("Test Officer")
        assert config is None

    def test_returns_none_for_unknown_fso(self, env):
        app, client, ctx = env
        from app.food_cell.email_sender import _get_fso_smtp_config

        config = _get_fso_smtp_config("Nonexistent Officer")
        assert config is None


class TestSendEmail:
    def test_fails_when_not_configured(self, env):
        app, client, ctx = env
        from app.food_cell.email_sender import send_improvement_notice_email

        result = send_improvement_notice_email(
            fso_name="Test Officer",
            recipient_email="do@kmc.gov.in",
            subject="Test",
            html_body="<p>Hello</p>",
        )
        assert result.success is False
        assert "not configured" in result.error.lower()

    def test_fails_with_invalid_recipient(self, env):
        app, client, ctx = env
        _configure_fso_email(app)
        from app.food_cell.email_sender import send_improvement_notice_email

        result = send_improvement_notice_email(
            fso_name="Test Officer",
            recipient_email="not-an-email",
            subject="Test",
            html_body="<p>Hello</p>",
        )
        assert result.success is False
        assert "invalid" in result.error.lower()

    @patch("app.food_cell.email_sender.smtplib.SMTP")
    def test_sends_email_successfully(self, MockSMTP, env):
        app, client, ctx = env
        _configure_fso_email(app)
        from app.food_cell.email_sender import send_improvement_notice_email

        mock_server = MagicMock()
        MockSMTP.return_value = mock_server

        result = send_improvement_notice_email(
            fso_name="Test Officer",
            recipient_email="do@kmc.gov.in",
            subject="Test Notice",
            html_body="<p>Hello World</p>",
            docx_bytes=b"fake-docx-bytes",
            docx_filename="Notice.docx",
        )

        assert result.success is True
        assert "sent successfully" in result.message.lower()
        mock_server.ehlo.assert_called()
        mock_server.starttls.assert_called()
        mock_server.login.assert_called_once_with("test@kmc.gov.in", "secret123")
        mock_server.sendmail.assert_called_once()

    @patch("app.food_cell.email_sender.smtplib.SMTP")
    def test_auth_failure(self, MockSMTP, env):
        app, client, ctx = env
        _configure_fso_email(app)
        from app.food_cell.email_sender import send_improvement_notice_email

        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        MockSMTP.return_value = mock_server

        result = send_improvement_notice_email(
            fso_name="Test Officer",
            recipient_email="do@kmc.gov.in",
            subject="Test",
            html_body="<p>Hello</p>",
        )

        assert result.success is False
        assert "authentication" in result.error.lower()

    @patch("app.food_cell.email_sender.smtplib.SMTP")
    def test_connection_failure(self, MockSMTP, env):
        app, client, ctx = env
        _configure_fso_email(app)
        from app.food_cell.email_sender import send_improvement_notice_email

        MockSMTP.side_effect = smtplib.SMTPConnectError(-1, b"Connection refused")

        result = send_improvement_notice_email(
            fso_name="Test Officer",
            recipient_email="do@kmc.gov.in",
            subject="Test",
            html_body="<p>Hello</p>",
        )

        assert result.success is False
        assert "connect" in result.error.lower()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env():
    app, client, ctx = _setup_test_env()
    yield app, client, ctx
    _teardown_test_env(ctx)


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


class TestEmailRoute:
    def test_email_404_for_missing_inspection(self, env):
        app, client, ctx = env
        resp = client.post(
            "/food-cell/improvement-notice/inspection/99999/email",
            data={"recipient_email": "do@kmc.gov.in", "subject": "Test"},
        )
        assert resp.status_code == 404

    def test_email_400_without_violations(self, env):
        app, client, ctx = env
        insp_id = _create_inspection(client)
        resp = client.post(
            f"/food-cell/improvement-notice/inspection/{insp_id}/email",
            data={"recipient_email": "do@kmc.gov.in", "subject": "Test"},
        )
        assert resp.status_code == 400

    def test_email_400_without_recipient(self, env):
        app, client, ctx = env
        insp_id = _create_inspection(client)
        resp = client.post(
            f"/food-cell/improvement-notice/inspection/{insp_id}/email",
            data={"subject": "Test"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "recipient" in data["error"].lower()

    def test_email_400_when_fso_not_configured(self, env):
        app, client, ctx = env
        insp_id = _create_inspection(client)
        resp = client.post(
            f"/food-cell/improvement-notice/inspection/{insp_id}/email",
            data={
                "recipient_email": "do@kmc.gov.in",
                "subject": "Test Notice",
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "not configured" in data["error"].lower()

    @patch("app.food_cell.email_sender.smtplib.SMTP")
    def test_email_200_when_configured_and_sent(self, MockSMTP, env):
        app, client, ctx = env
        _configure_fso_email(app)

        mock_server = MagicMock()
        MockSMTP.return_value = mock_server

        insp_id = _create_inspection(client)
        resp = client.post(
            f"/food-cell/improvement-notice/inspection/{insp_id}/email",
            data={
                "recipient_email": "do@kmc.gov.in",
                "subject": "Improvement Notice — Acme Foods",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sent successfully" in data["message"].lower()
        mock_server.sendmail.assert_called_once()
