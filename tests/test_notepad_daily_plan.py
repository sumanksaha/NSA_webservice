"""Tests for the Notepad Daily Plan feature.

Seams under test (agreed in docs/DAILY_PLAN_IMPLEMENTATION_PLAN.md):
- POST /notepad/plan/generate: one append-only DailyPlan row per generate,
  own-open-notes-only scope, flag-off / AI-failure write nothing.
- GET /notepad/plan: latest plan rendered with live note status badges.

Prompt internals and template markup are NOT tested.
"""

from __future__ import annotations

import json


def _setup_test_env():
    """Create a test app with fresh SQLite, two users."""
    from app import create_app
    from app.extensions import db
    from app.models import User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    alice = User(username="alice", password_hash="pbkdf2:sha256$test$dummy")
    bob = User(username="bob", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add_all([alice, bob])
    db.session.commit()

    clients = {}
    for user in (alice, bob):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
        clients[user.username] = (user, client)

    return app, clients, app_context


def _teardown_test_env(app_context):
    from app.extensions import db

    db.session.remove()
    db.drop_all()
    app_context.pop()


_PLAN_PAYLOAD = {
    "items": [],
    "ranking": [],
    "portfolio_rationale": "Do the quick win first.",
}


def _patch_plan_ai(monkeypatch, *, result=None, exc=None):
    """Stub the public AI seam used by the notepad plan routes; records input."""
    import app.notepad.routes as nr

    seen = {}

    class _FakeService:
        provider = "openrouter"
        model = "test-model"

        @staticmethod
        def is_enabled():
            return True

        def plan_open_notes(self, notes):
            seen["notes"] = notes
            if exc is not None:
                raise exc
            return json.loads(json.dumps(result))

    monkeypatch.setattr(nr, "_ai_service", lambda: _FakeService())
    return seen


def _make_note(user_id, content="Fix the fridge seal", status="new"):
    from app.extensions import db
    from app.models import Note

    note = Note(author_id=user_id, content_text=content, source_type="pasted")
    if status != "new":
        note.status = status
    if status == "implemented":
        note.implemented_note = "did it"
    db.session.add(note)
    db.session.commit()
    return note


class TestDailyPlanGenerate:
    def test_generate_creates_one_daily_plan_own_notes_only(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            alice, alice_client = clients["alice"]
            bob, _ = clients["bob"]

            own = _make_note(alice.id, content="Own task")
            _make_note(bob.id, content="Bob shared task")
            _make_note(alice.id, content="Already done", status="implemented")

            result = dict(_PLAN_PAYLOAD)
            result["items"] = [{"note_id": own.id, "effort_bucket": "quick", "why": "smallest"}]
            result["ranking"] = [{"note_id": own.id, "effort_bucket": "quick", "why": "smallest"}]
            seen = _patch_plan_ai(monkeypatch, result=result)

            resp = alice_client.post("/notepad/plan/generate")
            assert resp.status_code == 302

            from app.models import DailyPlan

            plans = DailyPlan.query.filter_by(author_id=alice.id).all()
            assert len(plans) == 1
            payload = json.loads(plans[0].payload)
            assert payload["portfolio_rationale"] == "Do the quick win first."
            assert [i["note_id"] for i in payload["items"]] == [own.id]
            assert all(i["effort_bucket"] in ("quick", "medium", "long") for i in payload["items"])
            # Only alice's OPEN notes were offered to the AI
            offered_ids = {n["id"] for n in seen["notes"]}
            assert offered_ids == {own.id}
        finally:
            _teardown_test_env(ctx)

    def test_regenerate_appends_never_overwrites(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            alice, alice_client = clients["alice"]
            _make_note(alice.id, content="Task A")
            _patch_plan_ai(monkeypatch, result=dict(_PLAN_PAYLOAD))

            alice_client.post("/notepad/plan/generate")
            alice_client.post("/notepad/plan/generate")

            from app.models import DailyPlan

            assert DailyPlan.query.filter_by(author_id=alice.id).count() == 2
        finally:
            _teardown_test_env(ctx)

    def test_flag_off_blocks_generate_and_writes_no_row(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            from app.shared.config import cfg

            alice, alice_client = clients["alice"]
            _make_note(alice.id)

            # Opt-out convention: anything but "false" is on.
            monkeypatch.setattr(cfg, "notepad_ai_enabled", False)
            seen = _patch_plan_ai(monkeypatch, result=dict(_PLAN_PAYLOAD))

            resp = alice_client.post("/notepad/plan/generate")
            assert resp.status_code == 503

            from app.models import DailyPlan

            assert DailyPlan.query.count() == 0
            assert seen == {}  # AI never called
        finally:
            _teardown_test_env(ctx)

    def test_ai_failure_writes_no_row(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            alice, alice_client = clients["alice"]
            _make_note(alice.id)
            _patch_plan_ai(monkeypatch, exc=RuntimeError("provider down"))

            resp = alice_client.post("/notepad/plan/generate")
            assert resp.status_code == 302  # flash + redirect, no crash

            from app.models import DailyPlan

            assert DailyPlan.query.count() == 0
        finally:
            _teardown_test_env(ctx)


class TestDailyPlanView:
    def test_view_shows_latest_plan_with_live_status_badge(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            alice, alice_client = clients["alice"]
            note = _make_note(alice.id, content="Do me")

            payload = dict(_PLAN_PAYLOAD)
            payload["items"] = [{"note_id": note.id, "effort_bucket": "quick", "why": "smallest"}]

            class _FakeService:
                provider = "x"
                model = "y"

                @staticmethod
                def is_enabled():
                    return True

                def plan_open_notes(self, notes):
                    return dict(payload)

            import app.notepad.routes as nr

            monkeypatch.setattr(nr, "_ai_service", lambda: _FakeService())
            assert alice_client.post("/notepad/plan/generate").status_code == 302

            # Status changes AFTER generation -> badge must reflect live state
            from app.extensions import db

            note.status = "implemented"
            note.implemented_note = "done"
            db.session.commit()

            resp = alice_client.get("/notepad/plan")
            body = resp.data.decode("utf-8")
            assert resp.status_code == 200
            assert "Implemented" in body
            assert "smallest" in body
        finally:
            _teardown_test_env(ctx)
