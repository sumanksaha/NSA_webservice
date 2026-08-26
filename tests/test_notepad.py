"""Tests for the Notepad module (Notes intake queue).

Seams under test (agreed 2026 grilling session):
- HTTP routes: list visibility scoping, creation (paste/PDF), synchronous
  evaluate (flag off / success / failure), append-only evaluations, status
  transitions incl. required implemented_note, author-only controls.
- Model defaults: is_shared=True, status="new".

Prompt internals and template markup are NOT tested.
"""

from __future__ import annotations

import io
import json

from app.extensions import db


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


# --------------------------------------------------------------------------- #
# Slice 5: PDF upload
# --------------------------------------------------------------------------- #


class TestPdfUpload:
    def test_pdf_upload_extracts_text_and_sets_source_type(self, monkeypatch, tmp_path):
        import app.notepad.routes as nr

        _app, clients, ctx = _setup_test_env()
        try:
            monkeypatch.setattr(
                nr,
                "_load_document_text",
                lambda stream, filename: "extracted pdf body text",
            )
            _, client = clients["alice"]
            resp = client.post(
                "/notepad/new",
                data={
                    "content_text": "",
                    "pdf_file": (io.BytesIO(b"%PDF-fake"), "idea.pdf", "application/pdf"),
                },
            )
            assert resp.status_code == 302

            from app.models import Note

            note = db.session.query(Note).one()
            assert note.source_type == "pdf"
            assert note.content_text == "extracted pdf body text"
        finally:
            _teardown_test_env(ctx)


class TestStatusAndPrivacy:
    def test_implemented_requires_note(self):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "idea")
            _, client = clients["alice"]
            resp = client.post(
                f"/notepad/{note.id}/status",
                data={"status": "implemented"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            db.session.refresh(note)
            assert note.status != "implemented"  # rejected: no trail
        finally:
            _teardown_test_env(ctx)

    def test_implemented_with_note_succeeds(self):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "idea")
            _, client = clients["alice"]
            client.post(
                f"/notepad/{note.id}/status",
                data={"status": "implemented", "implemented_note": "shipped in app/notepad"},
            )
            db.session.refresh(note)
            assert note.status == "implemented"
            assert note.implemented_note == "shipped in app/notepad"
        finally:
            _teardown_test_env(ctx)

    def test_dismissed_allowed_without_note(self):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "idea")
            _, client = clients["alice"]
            client.post(f"/notepad/{note.id}/status", data={"status": "dismissed"})
            db.session.refresh(note)
            assert note.status == "dismissed"
        finally:
            _teardown_test_env(ctx)

    def test_non_author_cannot_change_status_or_privacy(self):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "idea")  # shared
            _, bob_client = clients["bob"]
            resp_status = bob_client.post(f"/notepad/{note.id}/status", data={"status": "dismissed"})
            bob_client.post(f"/notepad/{note.id}/visibility", data={"is_shared": "false"})
            db.session.refresh(note)
            assert resp_status.status_code in (302, 403)
            assert note.status == "new"
            assert note.is_shared is True
        finally:
            _teardown_test_env(ctx)

    def test_author_can_toggle_privacy(self):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "idea")  # shared by default
            _, client = clients["alice"]
            client.post(f"/notepad/{note.id}/visibility", data={"is_shared": "false"})
            db.session.refresh(note)
            assert note.is_shared is False
        finally:
            _teardown_test_env(ctx)


_EVALUATION_PAYLOAD = {
    "summary": "s",
    "implementation_plan": "p",
    "risks": "r",
    "game_theory": "g",
    "talebian": "t",
    "first_principles": "f",
    "feasibility_score": 7,
}


def _patch_ai(monkeypatch, *, result=None, exc=None):
    """Stub the public AI seam used by the notepad routes."""
    import app.notepad.routes as nr

    class _FakeService:
        provider = "openrouter"
        model = "test-model"

        @staticmethod
        def is_enabled():
            return True

        def evaluate_note(self, text):
            if exc is not None:
                raise exc
            return dict(result)  # pyright: ignore[reportCallIssue, reportArgumentType]

    monkeypatch.setattr(nr, "_ai_service", lambda: _FakeService())


class TestEvaluate:
    def test_flag_off_returns_error_and_no_row(self, monkeypatch):
        from app.shared.config import cfg

        _, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "idea")
            monkeypatch.setattr(cfg, "notepad_ai_enabled", False)
            _, client = clients["alice"]
            resp = client.post(f"/notepad/{note.id}/evaluate")
            assert resp.status_code == 503

            from app.extensions import db
            from app.models import NoteEvaluation

            assert db.session.query(NoteEvaluation).count() == 0
        finally:
            _teardown_test_env(ctx)

    def test_success_appends_evaluation_and_marks_evaluated(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "idea")
            _patch_ai(monkeypatch, result=_EVALUATION_PAYLOAD)
            _, client = clients["alice"]
            resp = client.post(f"/notepad/{note.id}/evaluate", follow_redirects=True)
            assert resp.status_code == 200

            from app.extensions import db
            from app.models import NoteEvaluation

            ev = db.session.query(NoteEvaluation).one()
            assert ev.note_id == note.id
            payload = json.loads(ev.payload)
            for key in _EVALUATION_PAYLOAD:
                assert key in payload
            db.session.refresh(note)
            assert note.status == "evaluated"
        finally:
            _teardown_test_env(ctx)

    def test_ai_failure_writes_no_row(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "idea")
            _patch_ai(monkeypatch, exc=RuntimeError("provider down"))
            _, client = clients["alice"]
            resp = client.post(f"/notepad/{note.id}/evaluate", follow_redirects=False)
            assert resp.status_code in (302, 200)  # graceful, not a crash

            from app.extensions import db
            from app.models import NoteEvaluation

            assert db.session.query(NoteEvaluation).count() == 0
        finally:
            _teardown_test_env(ctx)

    def test_reruns_append_never_overwrite(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "idea")
            _patch_ai(monkeypatch, result=_EVALUATION_PAYLOAD)
            _, client = clients["alice"]
            client.post(f"/notepad/{note.id}/evaluate")
            client.post(f"/notepad/{note.id}/evaluate")

            from app.extensions import db
            from app.models import NoteEvaluation

            rows = db.session.query(NoteEvaluation).order_by(NoteEvaluation.id).all()
            assert len(rows) == 2  # append-only: earlier verdict stays visible
        finally:
            _teardown_test_env(ctx)


def _make_note(author_id: int, content: str, is_shared: bool = True, status: str = "new"):
    from app.extensions import db
    from app.models import Note

    note = Note(author_id=author_id, content_text=content, is_shared=is_shared, status=status)
    db.session.add(note)
    db.session.commit()
    return note


class TestVisibility:
    def _seed(self, clients):
        alice_id = clients["alice"][0].id
        bob_id = clients["bob"][0].id
        return [
            _make_note(alice_id, "alice shared", is_shared=True),
            _make_note(alice_id, "alice private", is_shared=False),
            _make_note(bob_id, "bob shared", is_shared=True),
            _make_note(bob_id, "bob private", is_shared=False),
        ]

    def test_alice_sees_own_private_and_everyones_shared(self):
        _app, clients, ctx = _setup_test_env()
        try:
            self._seed(clients)
            _, client = clients["alice"]
            resp = client.get("/notepad/")
            body = resp.get_data(as_text=True)
            assert resp.status_code == 200
            assert "alice shared" in body
            assert "alice private" in body  # own private notes are visible
            assert "bob shared" in body
            assert "bob private" not in body  # other's private notes are hidden
        finally:
            _teardown_test_env(ctx)

    def test_detail_hides_others_private_note(self):
        _app, clients, ctx = _setup_test_env()
        try:
            private_bob = self._seed(clients)[3]
            _, client = clients["alice"]
            assert client.get(f"/notepad/{private_bob.id}").status_code == 404
        finally:
            _teardown_test_env(ctx)

    def test_detail_shows_shared_note_to_other_user(self):
        _app, clients, ctx = _setup_test_env()
        try:
            shared_bob = self._seed(clients)[2]
            _, client = clients["alice"]
            assert client.get(f"/notepad/{shared_bob.id}").status_code == 200
        finally:
            _teardown_test_env(ctx)


class TestCreateNote:
    def test_pasted_text_creates_shared_new_note(self):
        _app, clients, ctx = _setup_test_env()
        try:
            _, client = clients["alice"]
            resp = client.post(
                "/notepad/new",
                data={"content_text": "Add lab-report reminders to inspections"},
                follow_redirects=False,
            )
            assert resp.status_code == 302

            from app.extensions import db
            from app.models import Note

            note = db.session.query(Note).one()
            assert note.content_text == "Add lab-report reminders to inspections"
            assert note.status == "new"
            assert note.is_shared is True  # shared-by-default (skin-in-the-game)
            assert note.author_id == clients["alice"][0].id
        finally:
            _teardown_test_env(ctx)

    def test_model_defaults(self):
        _app, clients, ctx = _setup_test_env()
        try:
            from app.extensions import db
            from app.models import Note

            note = Note(author_id=clients["alice"][0].id, content_text="x")
            db.session.add(note)
            db.session.commit()
            assert note.is_shared is True
            assert note.source_type == "pasted"
        finally:
            _teardown_test_env(ctx)
