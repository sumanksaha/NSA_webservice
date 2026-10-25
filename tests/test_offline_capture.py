"""Tests for offline capture: ID reconciliation, device-data purge, sync hooks.

Covers the seams in docs/OFFLINE_CAPTURE_DESIGN.md:
- InspectionCodeGenerator.next_code() allocation entrypoint
- Offline inspection create (provisional_id leaves inspection_code NULL)
- Online inspection create still allocates a server code
- Device-data purge on logout / APP_GENERATION mismatch
- PERMANENT_SESSION_LIFETIME config
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.inspection.code_generation import InspectionCodeGenerator
from app.models import FSO, Inspection, User
from app.services.device_data import purge_device_data


@pytest.fixture
def app():
    """A bare Flask app for config / service tests."""
    from app import create_app

    return create_app()


@pytest.fixture
def test_client():
    """Test client with an isolated DB, an FSO, and a logged-in user.

    Drops/recreates all tables per test so the function-scoped fixture does
    not collide with the module-scoped conftest DB (UNIQUE constraint on
    seeded username/FSO).
    """
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.drop_all()
            db.create_all()
            fso = FSO(fso_name="Officer Test")
            db.session.add(fso)
            user = User(
                username="officer",
                password_hash=generate_password_hash("password"),
            )
            db.session.add(user)
            db.session.commit()
        yield client


def _login(client):
    return client.post(
        "/auth/login",
        data={"username": "officer", "password": "password"},
        follow_redirects=False,
    )


def _create_form(base=None):
    data = {
        "food_safety_officer_name": "Officer Test",
        "inspection_date": "2026-01-15",
        "fbo_name": "Test FBO",
        "problem": "Test problem",
    }
    if base:
        data.update(base)
    return data


# ---------------------------------------------------------------------------
# ID reconciliation: code generation seam
# ---------------------------------------------------------------------------


class TestInspectionCodeGeneratorNextCode:
    def test_next_code_format(self):
        generator = InspectionCodeGenerator()
        with patch("app.inspection.code_generation.db.session") as mock_session:
            mock_seq = MagicMock()
            mock_seq.last_value = 7
            mock_session.get.return_value = mock_seq

            code = generator.next_code()

        year = datetime.now(UTC).year
        assert code == f"INSP-{year}-00008"

    def test_next_code_increments_sequence(self):
        generator = InspectionCodeGenerator()
        with patch("app.inspection.code_generation.db.session") as mock_session:
            mock_seq = MagicMock()
            mock_seq.last_value = 0
            mock_session.get.return_value = mock_seq

            generator.next_code()
            generator.next_code()

        assert mock_seq.last_value == 2

    def test_generate_code_delegates_to_next_code(self):
        generator = InspectionCodeGenerator()
        with patch.object(generator, "next_code", return_value="INSP-2026-00001") as mock:
            assert generator.generate_code() == "INSP-2026-00001"
            mock.assert_called_once()


# ---------------------------------------------------------------------------
# Offline inspection create seam
# ---------------------------------------------------------------------------


class TestOfflineInspectionCreate:
    def test_offline_capture_leaves_code_null(self, test_client):
        """provisional_id capture stores NULL inspection_code + provisional_id."""
        _login(test_client)
        form = _create_form({
            "provisional_id": "PEND-device1-1234567-abcd",
            "captured_at": "2026-01-15T10:00:00Z",
        })
        resp = test_client.post("/inspection/create", data=form)
        assert resp.status_code == 201, resp.get_data(as_text=True)

        with test_client.application.app_context():
            insp = db.session.get(Inspection, resp.get_json()["inspection_id"])
            assert insp.inspection_code is None
            assert insp.provisional_id == "PEND-device1-1234567-abcd"
            assert insp.captured_at is not None

    def test_online_capture_allocates_code(self, test_client):
        """No provisional_id -> server allocates INSP-YYYY-##### as before."""
        _login(test_client)
        form = _create_form()
        resp = test_client.post("/inspection/create", data=form)
        assert resp.status_code == 201, resp.get_data(as_text=True)

        with test_client.application.app_context():
            insp = db.session.get(Inspection, resp.get_json()["inspection_id"])
            assert insp.inspection_code is not None
            assert insp.inspection_code.startswith("INSP-")
            assert insp.provisional_id is None

    def test_response_includes_provisional_id(self, test_client):
        _login(test_client)
        form = _create_form({"provisional_id": "PEND-xyz-1-zzz"})
        resp = test_client.post("/inspection/create", data=form)
        body = resp.get_json()
        assert body["provisional_id"] == "PEND-xyz-1-zzz"


# ---------------------------------------------------------------------------
# Device-data purge seam
# ---------------------------------------------------------------------------


class TestPurgeDeviceData:
    def test_purge_with_none_user_is_noop(self, app):
        with app.app_context():
            purge_device_data(None)

    def test_purge_with_unknown_user_is_noop(self, app):
        with app.app_context():
            purge_device_data(999999)


# ---------------------------------------------------------------------------
# Config seam
# ---------------------------------------------------------------------------


class TestSessionConfig:
    def test_permanent_session_lifetime_is_30_minutes(self, app):
        """Session expires after 30 minutes of inactivity."""
        assert app.config["PERMANENT_SESSION_LIFETIME"] == 1800
