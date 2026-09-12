"""Characterization tests for the /adjudication/generate_all route.

``generate_all`` had zero direct coverage (only /preview was tested).  These
tests pin its observable contract before splitting the 179-line body into
stage helpers (2026-09-12 review):

1. Happy path: 200, a ZIP attachment, adjudication row persisted.
2. Sync failure → 500 with an error payload.
3. ``include_flagged=true`` without ``flag_override_reason`` → 400.
4. Non-pre-authorization without ``authorization_date`` → 400.
5. ``pre_authorization=yes`` → Permission_Letter.pdf naming path.

External I/O is stubbed (sync_row, PDF generation) — the route's own logic
(persistence, scoping, branching, ZIP assembly) is what's under test.
"""

from __future__ import annotations

import io
import zipfile
from unittest import mock

import pytest

from app.extensions import db
from app.models import User

from tests.test_preview_adjudication import VALID_FORM


@pytest.fixture()
def client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["DISABLE_RBAC"] = True

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            user = User(username="testuser", password_hash="pbkdf2:sha256$test$dummy")
            db.session.add(user)
            db.session.commit()
        yield client
        with app.app_context():
            db.drop_all()


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


def _fake_pdf(monkeypatch):
    """Two distinct fake PDFs so the ZIP can be inspected."""
    monkeypatch.setattr(
        "app.adjudication.routes.generate_pdf_from_html",
        lambda html: (b"%PDF-1.4 fake " + html[:32].encode(), None),
    )
    monkeypatch.setattr("app.adjudication.routes.post_process_pdf_html", lambda html, adjudication_id=None: html)
    monkeypatch.setattr("app.adjudication.routes.embed_photos_as_base64", lambda paths: {})


class TestGenerateAllContract:
    def test_happy_path_returns_zip_and_persists(self, client, monkeypatch):
        _login(client)
        _fake_pdf(monkeypatch)
        sync = mock.Mock()
        monkeypatch.setattr("app.adjudication.routes.sync_row", sync)

        resp = client.post("/adjudication/generate_all", data=VALID_FORM)
        assert resp.status_code == 200
        assert resp.mimetype == "application/zip"
        assert "Petition" in resp.headers.get("Content-Disposition", "")

        with zipfile.ZipFile(io.BytesIO(resp.data)) as z:
            names = z.namelist()
        assert names == ["Petition.pdf"]

        from app.models import Adjudication

        assert Adjudication.query.count() == 1
        sync.assert_called_once()
        assert sync.call_args.kwargs.get("entity_id") is not None

    def test_sync_failure_returns_500(self, client, monkeypatch):
        _login(client)
        _fake_pdf(monkeypatch)
        monkeypatch.setattr(
            "app.adjudication.routes.sync_row",
            mock.Mock(side_effect=RuntimeError("airtable down")),
        )
        resp = client.post("/adjudication/generate_all", data=VALID_FORM)
        assert resp.status_code == 500
        assert "sync failed" in resp.get_json()["error"].lower()

    def test_include_flagged_requires_reason(self, client, monkeypatch):
        _login(client)
        _fake_pdf(monkeypatch)
        monkeypatch.setattr("app.adjudication.routes.sync_row", mock.Mock())
        data = dict(VALID_FORM, include_flagged="true", flag_override_reason="")
        resp = client.post("/adjudication/generate_all", data=data)
        assert resp.status_code == 400
        assert "flag_override_reason" in resp.get_json()["error"]

    def test_non_preauth_requires_authorization_date(self, client, monkeypatch):
        _login(client)
        _fake_pdf(monkeypatch)
        monkeypatch.setattr("app.adjudication.routes.sync_row", mock.Mock())
        data = dict(VALID_FORM, authorization_date="")
        resp = client.post("/adjudication/generate_all", data=data)
        assert resp.status_code == 400
        assert "authorization_date" in resp.get_json()["error"]

    def test_pre_authorization_yields_permission_letter(self, client, monkeypatch):
        _login(client)
        _fake_pdf(monkeypatch)
        monkeypatch.setattr("app.adjudication.routes.sync_row", mock.Mock())
        data = dict(VALID_FORM, pre_authorization="yes")
        resp = client.post("/adjudication/generate_all", data=data)
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.data)) as z:
            names = z.namelist()
        assert names == ["Permission_Letter.pdf"]
        assert "PermissionLetter" in resp.headers.get("Content-Disposition", "")

    def test_fso_scope_stamps_officer(self, client, monkeypatch):
        """A scoped (fso-role, bound) user cannot create under another officer's name.

        Note: an *unbound* fso gets scope "" (falsy) which skips stamping
        entirely — the stamp only fires for a bound officer.
        """
        _login(client)
        _fake_pdf(monkeypatch)
        monkeypatch.setattr("app.adjudication.routes.sync_row", mock.Mock())
        from app.models import FSO, Role

        with client.application.app_context():
            from app.shared import rbac

            rbac.ensure_roles()
            db.session.add(FSO(fso_name="Scoped Officer"))
            user = User.query.filter_by(username="testuser").one()
            fso_role = db.session.query(Role).filter_by(name=rbac.FSO_ROLE).one()
            user.roles.append(fso_role)
            user.fso_name = "Scoped Officer"
            db.session.commit()

        data = dict(VALID_FORM, food_safety_officer_name="Someone Else")
        resp = client.post("/adjudication/generate_all", data=data)
        assert resp.status_code == 200
        with client.application.app_context():
            from app.models import Adjudication

            adj = Adjudication.query.one()
            # The route force-stamped the scoped officer name over the form value
            # (form key food_safety_officer_name → column food_safety_officer).
            assert adj.food_safety_officer == "Scoped Officer"
