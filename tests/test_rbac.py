"""Phase 18 RBAC tests.

Seams under test (agreed 2026-08-26):
- S1  role gate (ROLE_BLUEPRINTS + central check)          -> later slice
- S2  User.has_role / role assignment                       -> this slice
- S3  login redirect by role                                -> later slice
- S4/S5 CaseFile & Adjudication scoping + stamping         -> later slice
- S6  nested inheritance + billing carve-out               -> later slice
- S7  Inspection + Work Diary scoping                      -> later slice
- S8  Notepad access                                       -> later slice
- S9  Comments                                             -> later slice
- S10 Provisioning (extended create-user, uniqueness)      -> this slice (backfill)
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def env():
    """App + clean schema (no users/roles) for RBAC tests."""
    from app import create_app
    from app.extensions import db

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    # conftest disables the RBAC gate session-wide; these tests exercise it.
    app.config["DISABLE_RBAC"] = False

    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()

    yield app

    db.session.remove()
    db.drop_all()
    ctx.pop()


def _make_user(username: str, *, is_admin: bool = False):
    from app.extensions import db
    from app.models import User

    user = User(username=username, password_hash="pbkdf2:sha256$test$dummy", is_admin=is_admin)
    db.session.add(user)
    db.session.commit()
    return user


class TestHasRole:
    """S2 — a user reports the roles explicitly assigned to them."""

    def test_new_user_has_no_roles(self, env):
        user = _make_user("plain")
        assert user.has_role("fso") is False
        assert user.has_role("admin") is False

    def test_assigned_role_is_reported(self, env):
        from app.extensions import db
        from app.models import Role

        user = _make_user("officer")
        db.session.add(Role(name="fso"))
        db.session.commit()
        user.roles.append(Role.query.filter_by(name="fso").one())
        db.session.commit()

        assert user.has_role("fso") is True
        assert user.has_role("admin") is False

    def test_second_role_assignment_does_not_confuse_checks(self, env):
        from app.extensions import db
        from app.models import Role

        user = _make_user("hybrid")
        db.session.add(Role(name="fso"))
        db.session.add(Role(name="admin"))
        db.session.commit()
        user.roles.extend(Role.query.all())
        db.session.commit()

        assert user.has_role("fso") and user.has_role("admin")


class TestSeedRbac:
    """S10 (backfill half) — base roles exist and legacy users become admins."""

    def test_ensure_roles_creates_admin_and_fso(self, env):
        from app.extensions import db
        from app.models import Role
        from app.shared.rbac import ensure_roles

        ensure_roles()

        names = {r.name for r in db.session.query(Role).all()}
        assert {"admin", "fso"} <= names

    def test_ensure_roles_is_idempotent(self, env):
        from app.extensions import db
        from app.models import Role
        from app.shared.rbac import ensure_roles

        ensure_roles()
        ensure_roles()

        assert db.session.query(Role).filter_by(name="admin").count() == 1
        assert db.session.query(Role).filter_by(name="fso").count() == 1

    def test_backfill_grants_admin_to_all_existing_users(self, env):
        from app.extensions import db
        from app.models import User
        from app.shared.rbac import backfill_admin_role, ensure_roles

        _make_user("legacy1")
        _make_user("legacy2", is_admin=True)
        ensure_roles()
        backfill_admin_role()

        assert all(u.has_role("admin") for u in db.session.query(User).all())

    def test_backfill_is_idempotent(self, env):
        from app.models import Role, User
        from app.shared.rbac import backfill_admin_role, ensure_roles

        _make_user("legacy")
        ensure_roles()
        backfill_admin_role()
        backfill_admin_role()

        admin_role = Role.query.filter_by(name="admin").one()
        user = User.query.filter_by(username="legacy").one()
        assert user.roles.filter_by(name="admin").count() == 1 if hasattr(user.roles, "filter_by") else True
        assert sum(1 for r in user.roles if r.id == admin_role.id) == 1


# --------------------------------------------------------------------------- #
# S3/S1 helpers — real-login fixtures (password-based)
# --------------------------------------------------------------------------- #

TEST_PASSWORD = "correct-horse-battery"


def _make_password_user(username: str, *, is_admin: bool = False, role_name: str | None = None):
    from werkzeug.security import generate_password_hash

    from app.extensions import db
    from app.models import User
    from app.shared.rbac import ensure_roles

    ensure_roles()
    user = User(
        username=username,
        password_hash=generate_password_hash(TEST_PASSWORD),
        is_admin=is_admin,
    )
    db.session.add(user)
    db.session.commit()

    if role_name:
        from app.models import Role

        user.roles.append(Role.query.filter_by(name=role_name).one())
        db.session.commit()
    return user


def _login_via_post(client, username: str):
    return client.post(
        "/auth/login",
        data={"username": username, "password": TEST_PASSWORD},
        follow_redirects=False,
    )


class TestLoginRedirectByRole:
    """S3 — where each role lands after a successful login."""

    def test_fso_lands_on_sample_adjudication(self, env):
        _make_password_user("officer1", role_name="fso")
        client = env.test_client()
        resp = _login_via_post(client, "officer1")
        assert resp.status_code == 302
        assert "/case_file_generator" in resp.headers["Location"]

    def test_admin_login_unchanged(self, env):
        _make_password_user("boss", is_admin=True)
        client = env.test_client()
        resp = _login_via_post(client, "boss")
        assert resp.status_code == 302
        assert "/case_file_generator" in resp.headers["Location"]


class TestRoleGate:
    """S1 — fso reaches only ROLE_BLUEPRINTS[fso]; admins bypass."""

    def _fso_client(self, app):
        from app.extensions import db
        from app.models import User

        _make_password_user("officer1", role_name="fso")
        user = db.session.query(User).filter_by(username="officer1").one()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
        return client

    def _admin_client(self, app):
        from app.extensions import db
        from app.models import User

        _make_password_user("boss", is_admin=True)
        user = db.session.query(User).filter_by(username="boss").one()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
        return client

    def test_fso_blocked_from_admin_only_blueprint(self, env):
        app = env
        client = self._fso_client(app)
        resp = client.get("/settings/")
        assert resp.status_code == 302
        assert "/case_file_generator" in resp.headers["Location"]

    def test_block_carries_explanation_flash(self, env):
        app = env
        client = self._fso_client(app)
        resp = client.get("/settings/", follow_redirects=True)
        assert "do not have access" in resp.get_data(as_text=True).lower()

    def test_fso_reaches_allowed_blueprint(self, env):
        app = env
        client = self._fso_client(app)
        assert client.get("/adjudication/").status_code == 200

    def test_admin_bypasses_gate(self, env):
        app = env
        client = self._admin_client(app)
        assert client.get("/settings/").status_code == 200

    def test_auth_routes_never_blocked(self, env):
        app = env
        client = self._fso_client(app)
        assert client.get("/auth/change-password").status_code == 200


# --------------------------------------------------------------------------- #
# S4 — CaseFile scoping + officer stamping
# --------------------------------------------------------------------------- #

app_ref: dict = {}


def _make_fso(name: str):
    """Create (or reuse) an FSO row + a bound fso-role account; returns (user, client)."""
    from app.extensions import db
    from app.models import FSO

    app = app_ref["app"]
    if db.session.query(FSO).filter_by(fso_name=name).count() == 0:
        db.session.add(FSO(fso_name=name))
        db.session.commit()
    slug = name.lower().replace(" ", "_")
    user = _make_password_user(f"u_{slug}", role_name="fso")
    user.fso_name = name
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
    return user, client


@pytest.fixture()
def two_officers(env):
    from app.extensions import db
    from app.models import FSO

    app = env
    app_ref["app"] = app
    db.session.add(FSO(fso_name="Officer A"))
    db.session.add(FSO(fso_name="Officer B"))
    db.session.commit()
    return app


_CASE_DEFAULTS = dict(
    case_number="CF/2026/001",
    authorization_date="2026-01-05",
    sample_draw_date="2026-01-10",
    sample_draw_time="10:00",
    manufacturer_fssai_license="12345678901234",
    manufacturer_person_name="Mfg Person",
    manufacturer_trade_name="Mfg Trade",
    manufacturer_address="Mfg Address",
    retailer_fssai_license="22345678901234",
    retailer_person_name="Ret Person",
    retailer_trade_name="Ret Trade",
    retailer_address="Ret Address",
    product_name="Atta",
    batch_no="B1",
    sample_quantity="1 kg",
    packet_count="4",
    mfg_date="2025-12-01",
    expiry_date="2026-12-01",
    other_food_articles="",
    total_cost="500",
    cost_in_words="Five hundred",
    sample_code="S-001",
    sample_submission_date="2026-01-11",
    lab_registration_no="LAB-1",
    do_receipt_date="2026-01-12",
    analyst_report_no="AR-1",
    analyst_report_date="2026-01-20",
    directive_letter_no="DL-1",
    directive_letter_date="2026-01-21",
    retailer_report_receive_date="2026-01-25",
    manufacturer_report_receive_date="2026-01-26",
)


def _make_case_file(officer: str, number: str):
    from datetime import UTC, datetime

    from app.extensions import db
    from app.models import CaseFile

    case = CaseFile(
        case_number=number,
        food_safety_officer_name=officer,
        authorization_date=datetime(2026, 1, 5, tzinfo=UTC),
        inspection_date=datetime(2026, 1, 10, tzinfo=UTC),
        inspection_time="10:00",
        manufacturer_fssai="12345678901234",
        manufacturer_name="Mfg Person",
        manufacturer_fbo_name="Mfg Trade",
        manufacturer_address="Mfg Address",
        retailer_fssai="22345678901234",
        retailer_name="Ret Person",
        retailer_fbo_name="Ret Trade",
        retailer_address="Ret Address",
        product_name="Atta",
        batch_no="B1",
        sample_quantity="1 kg",
        packet_count=4,
        mfg_date=datetime(2025, 12, 1, tzinfo=UTC),
        expiry_date=datetime(2026, 12, 1, tzinfo=UTC),
        sample_code=f"S-{number}",
        sample_submission_date=datetime(2026, 1, 11, tzinfo=UTC),
        Lab_Registration_No="LAB-1",
        do_receipt_date=datetime(2026, 1, 12, tzinfo=UTC),
        analyst_report_no="AR-1",
        analyst_report_date=datetime(2026, 1, 20, tzinfo=UTC),
        directive_letter_no="DL-1",
        directive_letter_date=datetime(2026, 1, 21, tzinfo=UTC),
        retailer_report_receive_date=datetime(2026, 1, 25, tzinfo=UTC),
        manufacturer_report_receive_date=datetime(2026, 1, 26, tzinfo=UTC),
    )
    db.session.add(case)
    db.session.commit()
    return case


class TestCaseFileScoping:
    """S4 — an FSO sees and creates only their own sample cases."""

    def test_list_shows_only_own_cases(self, two_officers):
        _make_case_file("Officer A", "CF/A/1")
        _make_case_file("Officer B", "CF/B/1")
        _, client_a = _make_fso("Officer A")

        body = client_a.get("/case_file_generator/cases").get_json()
        numbers = [c["case_number"] for c in body]
        assert numbers == ["CF/A/1"]

    def test_other_officers_case_detail_fails_closed(self, two_officers):
        other_case = _make_case_file("Officer B", "CF/B/2")
        _, client_a = _make_fso("Officer A")
        assert client_a.get(f"/case_file_generator/case/{other_case.id}").status_code == 404

    def test_own_case_detail_visible(self, two_officers):
        own = _make_case_file("Officer A", "CF/A/2")
        _, client_a = _make_fso("Officer A")
        assert client_a.get(f"/case_file_generator/case/{own.id}").status_code == 200

    def test_create_force_stamps_bound_officer(self, two_officers, monkeypatch):
        from app.case_file_generator import routes as cfr

        monkeypatch.setattr(
            cfr,
            "publish_task",
            lambda *a, **k: {"mode": "async", "message_id": "test-msg"},
        )
        payload = dict(_CASE_DEFAULTS)
        payload["food_safety_officer_name"] = "Someone Else"  # spoof attempt
        _, client_a = _make_fso("Officer A")

        resp = client_a.post("/case_file_generator/generate_case_file", data=payload)
        assert resp.status_code in (200, 202), resp.get_data(as_text=True)

        from app.models import CaseFile

        created = CaseFile.query.filter_by(case_number=_CASE_DEFAULTS["case_number"]).one()
        assert created.food_safety_officer_name == "Officer A"


# --------------------------------------------------------------------------- #
# S7 — Inspection & Work Diary scoping
# --------------------------------------------------------------------------- #


def _make_inspection_for(officer: str, code: str, address: str = "12 MG Road"):
    from datetime import UTC, datetime

    from app.extensions import db
    from app.models import Inspection

    db.session.add(
        Inspection(
            inspection_code=code,
            fso_name=officer,
            fbo_name="Sweet Shop",
            fbo_address=address,
            inspection_date=datetime(2026, 3, 5, 10, 30, tzinfo=UTC),
            compliance_deadline=datetime(2026, 4, 4, tzinfo=UTC),
            is_dismissed=False,
        )
    )
    db.session.commit()


class TestInspectionScoping:
    def test_list_shows_only_own_inspections(self, two_officers):
        _make_inspection_for("Officer A", "INSP-A-1")
        _make_inspection_for("Officer B", "INSP-B-1")
        _, client_a = _make_fso("Officer A")

        body = client_a.get("/inspection/list").get_data(as_text=True)
        assert "INSP-A-1" in body
        assert "INSP-B-1" not in body

    def test_create_stamps_bound_officer(self, two_officers):
        payload = {
            "food_safety_officer_name": "Officer B",  # spoof attempt
            "inspection_date": "2026-03-06",
        }
        _, client_a = _make_fso("Officer A")

        resp = client_a.post("/inspection/create", data=payload)
        assert resp.status_code == 201, resp.get_data(as_text=True)

        from app.models import Inspection

        created = Inspection.query.filter_by(inspection_code=resp.get_json()["inspection_code"]).one()
        assert created.fso_name == "Officer A"


class TestWorkDiaryPrivacy:
    def test_diary_locked_to_bound_officer(self, two_officers):
        _make_inspection_for("Officer A", "INSP-A-2", address="1 Own Street")
        _make_inspection_for("Officer B", "INSP-B-2", address="9 Other Road")
        _, client_a = _make_fso("Officer A")

        body = client_a.get("/workdiary/", query_string={"fso_name": "Officer B"}).get_data(as_text=True)
        assert "9 Other Road" not in body  # other officer's entry invisible
        assert "1 Own Street" in body  # own entry present

    def test_other_officers_preview_blocked(self, two_officers):
        _make_inspection_for("Officer B", "INSP-B-3")
        _, client_a = _make_fso("Officer A")

        resp = client_a.get("/workdiary/preview", query_string={"fso_name": "Officer B"})
        assert resp.status_code == 302
        assert "/case_file_generator" in resp.headers["Location"]


# --------------------------------------------------------------------------- #
# S8 — Notepad access (shared-by-default Notes)
# --------------------------------------------------------------------------- #


def _make_note(author_id: int, content: str, *, is_shared: bool = True):
    from app.extensions import db
    from app.models import Note

    note = Note(author_id=author_id, content_text=content, is_shared=is_shared)
    db.session.add(note)
    db.session.commit()
    return note


class TestNotepadAccess:
    """S8 — Notes: shared-by-default visibility; private = author-only.

    NOTE on shape: each test authenticates exactly ONE officer. Within a
    single pytest process, Flask-Login's ``current_user`` resolves through
    thread-local state that makes a second live identity unreliable (first
    identity sticks), so cross-user cases are asserted with the other
    officer's Note seeded directly via the model instead of a second login.
    """

    def test_notepad_reachable_for_fso(self, two_officers):
        _, client_a = _make_fso("Officer A")
        assert client_a.get("/notepad/").status_code == 200

    def test_shared_note_visible_to_other_fso(self, two_officers):
        from app.extensions import db
        from app.models import User

        author = User(username="u_author_shared", password_hash="x")
        db.session.add(author)
        db.session.commit()
        _make_note(author.id, "Shared idea about milk sampling", is_shared=True)

        _, client_b = _make_fso("Officer B")
        body = client_b.get("/notepad/").get_data(as_text=True)
        assert "Shared idea about milk sampling" in body

    def test_private_note_hidden_from_other_fso(self, two_officers):
        from app.extensions import db
        from app.models import User

        # Author exists only as data — never logs in during this test.
        author = User(username="u_author_only", password_hash="x")
        db.session.add(author)
        db.session.commit()
        note = _make_note(author.id, "Private musing", is_shared=False)

        _, client_b = _make_fso("Officer B")
        assert "Private musing" not in client_b.get("/notepad/").get_data(as_text=True)
        assert client_b.get(f"/notepad/{note.id}").status_code == 404

    def test_private_note_visible_to_author(self, two_officers):
        user_a, client_a = _make_fso("Officer A")
        note = _make_note(user_a.id, "Private musing", is_shared=False)
        assert client_a.get(f"/notepad/{note.id}").status_code == 200

    def test_status_change_blocked_for_non_author(self, two_officers):
        """A non-author fso cannot transition someone else's shared Note."""
        from app.extensions import db
        from app.models import User

        author = User(username="u_author_only2", password_hash="x")
        db.session.add(author)
        db.session.commit()
        note = _make_note(author.id, "Plan the raid", is_shared=True)
        _, client_b = _make_fso("Officer B")

        resp = client_b.post(f"/notepad/{note.id}/status", data={"status": "dismissed"})
        assert resp.status_code == 403

    def test_status_change_allowed_for_author(self, two_officers):
        user_a, client_a = _make_fso("Officer A")
        note = _make_note(user_a.id, "Plan the raid", is_shared=True)

        resp = client_a.post(f"/notepad/{note.id}/status", data={"status": "dismissed"})
        assert resp.status_code in (200, 302)


# --------------------------------------------------------------------------- #
# S10 — Provisioning: bound fso accounts
# --------------------------------------------------------------------------- #


class TestFsoAccountService:
    def test_create_fso_account_binds_and_assigns_role(self, two_officers):
        from app.auth.provisioning import create_fso_account

        from app.extensions import db
        from app.models import User

        user = create_fso_account("new.officer", "Officer A", "Str0ng-Pass!1")

        assert user.fso_name == "Officer A"
        assert user.has_role("fso")
        assert db.session.get(User, user.id) is not None

    def test_duplicate_binding_rejected(self, two_officers):
        from app.auth.provisioning import ProvisioningError, create_fso_account

        create_fso_account("first.account", "Officer A", "Str0ng-Pass!1")
        with pytest.raises(ProvisioningError, match="already bound"):
            create_fso_account("second.account", "Officer A", "Str0ng-Pass!1")

    def test_unknown_fso_name_rejected(self, two_officers):
        from app.auth.provisioning import ProvisioningError, create_fso_account

        with pytest.raises(ProvisioningError, match="Unknown FSO"):
            create_fso_account("ghost.user", "Ghost Officer", "Str0ng-Pass!1")


class TestSeedFsoUsers:
    def test_seeds_all_unbound_fsos_and_is_idempotent(self, two_officers):
        from app.auth.provisioning import seed_fso_users

        from app.extensions import db
        from app.models import User

        result = seed_fso_users("Str0ng-Pass!1")
        assert sorted(result["created"]) == ["Officer A", "Officer B"]
        assert result["skipped"] == []

        again = seed_fso_users("Str0ng-Pass!1")
        assert again["created"] == []
        assert sorted(again["skipped"]) == ["Officer A", "Officer B"]

        users = {u.fso_name: u for u in db.session.query(User).all()}
        assert users["Officer A"].has_role("fso")
        assert users["Officer B"].has_role("fso")


class TestCreateUserRouteRoleField:
    """S10 — the admin Add User form understands role + fso_name."""

    def _admin_client(self, env):
        from app.extensions import db
        from app.models import User

        app = env
        boss = User(username="boss", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
        db.session.add(boss)
        db.session.commit()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(boss.id)
        return client

    def test_route_creates_bound_fso_user(self, env):
        from app.extensions import db
        from app.models import FSO, User

        db.session.add(FSO(fso_name="Officer A"))
        db.session.commit()
        client = self._admin_client(env)

        resp = client.post(
            "/auth/users/create",
            data={
                "username": "route.fso",
                "password": "Str0ng-Pass!1",
                "confirm_password": "Str0ng-Pass!1",
                "role": "fso",
                "fso_name": "Officer A",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        user = User.query.filter_by(username="route.fso").one()
        assert user.fso_name == "Officer A"
        assert user.has_role("fso")

    def test_route_rejects_duplicate_binding(self, env):
        from app.auth.provisioning import create_fso_account

        from app.extensions import db
        from app.models import FSO

        db.session.add(FSO(fso_name="Officer A"))
        db.session.commit()
        create_fso_account("taken.account", "Officer A", "Str0ng-Pass!1")

        client = self._admin_client(env)
        resp = client.post(
            "/auth/users/create",
            data={
                "username": "other.account",
                "password": "Str0ng-Pass!1",
                "confirm_password": "Str0ng-Pass!1",
                "role": "fso",
                "fso_name": "Officer A",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200  # re-rendered form with flash
        assert "already bound" in resp.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# S9 — Comments on cases
# --------------------------------------------------------------------------- #


def _make_adjudication(officer: str, number: str):
    from datetime import UTC, datetime

    from app.extensions import db
    from app.models import Adjudication

    adj = Adjudication(
        case_number=number,
        food_safety_officer=officer,
        fssai_license="12345678901234",
        fbo_owner="Owner",
        fbo_name="FBO",
        fbo_address="FBO Address",
        First_inspection_date=datetime(2026, 2, 1, tzinfo=UTC),
        compliance_deadline=datetime(2026, 3, 1, tzinfo=UTC),
        inspection_date=datetime(2026, 2, 10, tzinfo=UTC),
        authorization_date=datetime(2026, 2, 5, tzinfo=UTC),
    )
    db.session.add(adj)
    db.session.commit()
    return adj


class TestComments:
    """S9 — comments inherit the parent case's visibility."""

    def test_fso_can_comment_on_own_case(self, two_officers):
        own = _make_case_file("Officer A", "CF/A/5")
        _, client_a = _make_fso("Officer A")

        resp = client_a.post(
            "/comments",
            json={"case_type": "case_file", "case_id": own.id, "content": "Report filed."},
        )
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)

        from app.extensions import db
        from app.models import Comment

        assert db.session.query(Comment).filter_by(case_id=own.id, case_type="case_file").count() == 1

    def test_fso_cannot_comment_on_invisible_case(self, two_officers):
        other = _make_case_file("Officer B", "CF/B/5")
        _, client_a = _make_fso("Officer A")

        resp = client_a.post(
            "/comments",
            json={"case_type": "case_file", "case_id": other.id, "content": "sneaky"},
        )
        assert resp.status_code == 404

    def test_admin_can_comment_on_any_case(self, two_officers):
        adj = _make_adjudication("Officer B", "ADJ/B/1")
        client = self._admin_client()

        resp = client.post(
            "/comments",
            json={"case_type": "adjudication", "case_id": adj.id, "content": "DO note"},
        )
        assert resp.status_code in (200, 201)

    def _admin_client(self):
        from app.extensions import db
        from app.models import User

        app = app_ref["app"]
        _make_password_user("boss", is_admin=True)
        user = db.session.query(User).filter_by(username="boss").one()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
        return client

    def test_non_author_on_invisible_case_gets_404(self, two_officers):
        """Fail-closed: B can't even see A's case, so its comments 404 to them."""
        from app.extensions import db
        from app.models import Comment, User

        own = _make_case_file("Officer A", "CF/A/6")
        author = User(username="u_comment_author", password_hash="x")
        db.session.add(author)
        db.session.commit()
        comment = Comment(
            case_id=own.id,
            case_type="case_file",
            user_id=author.id,
            content="mine",
        )
        db.session.add(comment)
        db.session.commit()

        _, client_b = _make_fso("Officer B")
        assert client_b.delete(f"/comments/{comment.id}").status_code == 404
        assert db.session.query(Comment).count() == 1  # nothing deleted

    def test_admin_can_delete_any_visible_comment(self, two_officers):
        own = _make_case_file("Officer A", "CF/A/8")
        _, client_a = _make_fso("Officer A")
        client_a.post("/comments", json={"case_type": "case_file", "case_id": own.id, "content": "mine"})

        from app.extensions import db
        from app.models import Comment

        comment = db.session.query(Comment).one()
        admin_client = self._admin_client()
        assert admin_client.delete(f"/comments/{comment.id}").status_code in (200, 204)
        assert db.session.query(Comment).count() == 0

    def test_author_can_delete_own_comment(self, two_officers):
        own = _make_case_file("Officer A", "CF/A/7")
        _, client_a = _make_fso("Officer A")
        client_a.post("/comments", json={"case_type": "case_file", "case_id": own.id, "content": "mine"})

        from app.extensions import db
        from app.models import Comment

        comment = db.session.query(Comment).one()
        assert client_a.delete(f"/comments/{comment.id}").status_code in (200, 204)
        assert db.session.query(Comment).count() == 0


class TestChildRouteInheritance:
    """S6 — child routes inherit the parent case's visibility."""

    def test_editor_visible_for_own_case(self, two_officers):
        own = _make_case_file("Officer A", "CF/A/3")
        _, client_a = _make_fso("Officer A")
        assert client_a.get(f"/case_file_generator/{own.id}/editor").status_code == 200

    def test_editor_blocked_for_other_officers_case(self, two_officers):
        other = _make_case_file("Officer B", "CF/B/3")
        _, client_a = _make_fso("Officer A")
        assert client_a.get(f"/case_file_generator/{other.id}/editor").status_code == 404

    def test_export_json_blocked_for_other_officers_case(self, two_officers):
        other = _make_case_file("Officer B", "CF/B/4")
        _, client_a = _make_fso("Officer A")
        resp = client_a.get(f"/case_file_generator/api/cases/{other.id}/export.json")
        assert resp.status_code == 404

    def test_export_json_allowed_for_own_case(self, two_officers):
        own = _make_case_file("Officer A", "CF/A/4")
        _, client_a = _make_fso("Officer A")
        assert client_a.get(f"/case_file_generator/api/cases/{own.id}/export.json").status_code == 200
