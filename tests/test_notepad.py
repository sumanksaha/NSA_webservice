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


#: A full legal-RAG pipeline result dict (shape: app/rag/tasks.py:737-779).
_RAG_RESULT = {
    "query": "rewritten query",
    "answer": "Section 55 of the FSS Act, 2006 provides for a penalty...",
    "citations": [
        {
            "chunk_id": "c-1",
            "section_number": "55",
            "document_title": "Food Safety and Standards Act, 2006",
            "document_type": "act",
            "authority": "statute",
            "url": None,
            "snippet": "If any person...",
            "confidence": 0.93,
        }
    ],
    "groundedness_score": 0.91,
    "hallucination_detected": False,
    "debug": {},
    "llm_model": "legal-ce/2",
    "token_usage": 1234,
    "log_id": "42",
}


def _patch_pipeline(monkeypatch, *, results=None, exc=None):
    """Stub the legal-RAG pipeline seam used by ``thread.ask``.

    Returns the stub so tests can inspect the captured calls. ``results``
    is consumed one per ``run`` call; ``exc`` is raised on the Nth call
    (1-based) when it is an int, or on every call when it is an Exception.
    """
    import app.notepad.thread as thread

    class _StubPipeline:
        def __init__(self):
            self.calls = []

        def run(self, query, top_k=None, **kwargs):  # noqa: ARG002
            self.calls.append({"query": query, "top_k": top_k})
            n = len(self.calls)
            if isinstance(exc, int):
                if n == exc:
                    raise RuntimeError("pipeline down")
            elif exc is not None:
                raise exc
            if results is None:
                return dict(_RAG_RESULT)
            return dict(results[n - 1])

    stub = _StubPipeline()
    monkeypatch.setattr(thread, "get_pipeline", lambda: stub)
    return stub


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


class TestNoteAnswerModel:
    """NoteAnswer — one append-only grounded RAG answer in a Note's thread.

    Model/migration slice of docs/NOTEPAD_LEGAL_RAG_SPEC.md Phase 1
    (INV-1, INV-3). Route/ask behaviour lands in Phase 2.
    """

    def test_mapping_columns_default_to_none_and_degraded_false(self):
        _app, clients, ctx = _setup_test_env()
        try:
            from app.extensions import db
            from app.models import Note, NoteAnswer

            note = _make_note(clients["alice"][0].id, "Which penalty applies?")
            answer = NoteAnswer(note_id=note.id, question="Section 55 penalty?", answer="...")
            db.session.add(answer)
            db.session.commit()

            assert answer.degraded is False
            assert answer.citations is None
            assert answer.note_snapshot is None
            assert answer.groundedness_score is None
            assert answer.hallucination_detected is None
            assert answer.provider_model is None
            assert answer.token_usage is None
            assert answer.rag_log_id is None
        finally:
            _teardown_test_env(ctx)

    def test_answers_relationship_is_ordered_and_backfills_note(self):
        _app, clients, ctx = _setup_test_env()
        try:
            from datetime import UTC, datetime

            from app.models import Note, NoteAnswer

            note = _make_note(clients["alice"][0].id, "statement")
            first = NoteAnswer(
                note_id=note.id,
                question="q1",
                answer="a1",
                created_at=datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
            )
            second = NoteAnswer(
                note_id=note.id,
                question="q2",
                answer="a2",
                created_at=datetime(2026, 10, 11, 10, 0, tzinfo=UTC),
            )
            db.session.add_all([first, second])
            db.session.commit()

            db.session.refresh(note)
            assert [a.question for a in note.answers] == ["q1", "q2"]
            assert first.note is note  # backref works
        finally:
            _teardown_test_env(ctx)

    def test_deleting_note_cascades_to_answers(self):
        _app, clients, ctx = _setup_test_env()
        try:
            from app.extensions import db
            from app.models import Note, NoteAnswer

            note = _make_note(clients["alice"][0].id, "statement")
            db.session.add(NoteAnswer(note_id=note.id, question="q", answer="a"))
            db.session.commit()

            db.session.delete(note)
            db.session.commit()
            assert db.session.query(NoteAnswer).count() == 0
        finally:
            _teardown_test_env(ctx)


class TestAsk:
    """``thread.ask`` — Phase 1 orchestration (spec §5.2/§5.4).

    Covers INV-2 (fragment order), INV-3 (snapshot), INV-8 (all-or-nothing),
    V-1…V-4 (validation before any RAG call), and the prior-turns wiring of
    the rewritten query. Route-level behaviour arrives in Phase 2.
    """

    def test_maps_pipeline_result_onto_answer_row(self, monkeypatch):
        import app.notepad.thread as thread

        _app, clients, ctx = _setup_test_env()
        try:
            _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "We seized adulterated milk.")

            answers = thread.ask(note, "Which penalty applies?")

            assert len(answers) == 1
            a = answers[0]
            assert a.question == "Which penalty applies?"
            assert a.answer == _RAG_RESULT["answer"]
            assert json.loads(a.citations) == _RAG_RESULT["citations"]
            assert a.groundedness_score == 0.91
            assert a.hallucination_detected is False
            assert a.degraded is False
            assert a.provider_model == "legal-ce/2"
            assert a.token_usage == 1234
            assert a.rag_log_id == "42"
            assert a.note_snapshot == "We seized adulterated milk."
        finally:
            _teardown_test_env(ctx)

    def test_one_answer_per_fragment_in_order(self, monkeypatch):
        import app.notepad.thread as thread

        _app, clients, ctx = _setup_test_env()
        try:
            results = [{**_RAG_RESULT, "answer": f"answer-{i}"} for i in range(1, 4)]
            stub = _patch_pipeline(monkeypatch, results=results)
            note = _make_note(clients["alice"][0].id, "statement")

            answers = thread.ask(note, "first\nsecond\nthird")

            assert [a.question for a in answers] == ["first", "second", "third"]
            assert [a.answer for a in answers] == ["answer-1", "answer-2", "answer-3"]
            assert len(stub.calls) == 3  # one pipeline call per fragment (F3)
        finally:
            _teardown_test_env(ctx)

    def test_failure_on_second_fragment_persists_nothing(self, monkeypatch):
        import app.notepad.thread as thread

        _app, clients, ctx = _setup_test_env()
        try:
            from app.extensions import db
            from app.models import NoteAnswer

            _patch_pipeline(monkeypatch, exc=2)
            note = _make_note(clients["alice"][0].id, "statement")

            import pytest

            with pytest.raises(RuntimeError):
                thread.ask(note, "first\nsecond")

            # ask never touches the session: nothing pending, nothing stored (INV-8)
            assert not [o for o in db.session.new if isinstance(o, NoteAnswer)]
            assert db.session.query(NoteAnswer).count() == 0
        finally:
            _teardown_test_env(ctx)

    def test_degraded_fallback_result_is_flagged(self, monkeypatch):
        import app.notepad.thread as thread

        _app, clients, ctx = _setup_test_env()
        try:
            degraded = {
                **_RAG_RESULT,
                "answer": "",
                "citations": [],
                "debug": {"degraded_mode": True, "reason": "circuit_open"},
            }
            _patch_pipeline(monkeypatch, results=[degraded])
            note = _make_note(clients["alice"][0].id, "statement")

            (answer,) = thread.ask(note, "Which penalty applies?")

            assert answer.degraded is True
            assert answer.answer == ""
            assert json.loads(answer.citations) == []
        finally:
            _teardown_test_env(ctx)

    def test_prior_turns_feed_the_rewritten_query(self, monkeypatch):
        import app.notepad.thread as thread

        _app, clients, ctx = _setup_test_env()
        try:
            from app.extensions import db
            from app.models import NoteAnswer

            note = _make_note(clients["alice"][0].id, "We seized adulterated milk.")
            db.session.add_all([
                NoteAnswer(note_id=note.id, question="first-q", answer="first-a"),
                NoteAnswer(note_id=note.id, question="second-q", answer="second-a"),
            ])
            db.session.commit()

            stub = _patch_pipeline(monkeypatch)
            thread.ask(note, "follow-up?")

            query = stub.calls[0]["query"]
            assert "We seized adulterated milk." in query  # the statement
            assert "first-q" in query and "first-a" in query
            assert "second-q" in query and "second-a" in query
            assert "follow-up?" in query
        finally:
            _teardown_test_env(ctx)

    def test_blank_question_rejected_before_any_call(self, monkeypatch):
        import app.notepad.thread as thread

        _app, clients, ctx = _setup_test_env()
        try:
            import pytest

            stub = _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "statement")

            with pytest.raises(ValueError):
                thread.ask(note, "   \n\t ")
            assert stub.calls == []  # V-1/V-2: no RAG call
        finally:
            _teardown_test_env(ctx)

    def test_too_many_fragments_rejected_before_any_call(self, monkeypatch):
        import app.notepad.thread as thread

        _app, clients, ctx = _setup_test_env()
        try:
            import pytest

            stub = _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "statement")
            eleven = "\n".join(f"q{i}" for i in range(11))

            with pytest.raises(ValueError, match="at most 10 fragments"):
                thread.ask(note, eleven)
            assert stub.calls == []  # V-4
        finally:
            _teardown_test_env(ctx)

    def test_overlong_fragment_rejected_before_any_call(self, monkeypatch):
        import app.notepad.thread as thread

        _app, clients, ctx = _setup_test_env()
        try:
            import pytest

            stub = _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "statement")

            with pytest.raises(ValueError, match="fragment 1 exceeds 4000 characters"):
                thread.ask(note, "x" * 4001)
            assert stub.calls == []  # V-3
        finally:
            _teardown_test_env(ctx)


class TestAskRoute:
    """POST /notepad/<id>/ask — Phase 2 route contract (spec §8).

    Order pinned by the spec: visibility (404) → author-only (403) →
    §7 validation (warning redirect) → kill-switch gates (503) →
    ``thread.ask`` → commit. AC-1…AC-6, AC-10…AC-13 at route level.
    """

    def _post(self, client, note_id, follow_redirects: bool = False, **form):
        return client.post(
            f"/notepad/{note_id}/ask",
            data=form or {"question": "Which penalty?"},
            follow_redirects=follow_redirects,
        )

    def test_notepad_ai_disabled_returns_503_and_no_row(self, monkeypatch):
        from app.shared.config import cfg

        _app, clients, ctx = _setup_test_env()
        try:
            from app.models import NoteAnswer

            note = _make_note(clients["alice"][0].id, "statement")
            stub = _patch_pipeline(monkeypatch)
            monkeypatch.setattr(cfg, "notepad_ai_enabled", False)
            _, client = clients["alice"]

            resp = self._post(client, note.id)

            assert resp.status_code == 503
            assert stub.calls == []  # the gate precedes any RAG call
            assert db.session.query(NoteAnswer).count() == 0
        finally:
            _teardown_test_env(ctx)

    def test_rag_disabled_returns_503_and_no_row(self, monkeypatch):
        from app.shared.config import cfg

        _app, clients, ctx = _setup_test_env()
        try:
            from app.models import NoteAnswer

            note = _make_note(clients["alice"][0].id, "statement")
            stub = _patch_pipeline(monkeypatch)
            monkeypatch.setattr(cfg, "rag_enabled", False)
            _, client = clients["alice"]

            resp = self._post(client, note.id)

            assert resp.status_code == 503
            assert stub.calls == []
            assert db.session.query(NoteAnswer).count() == 0
        finally:
            _teardown_test_env(ctx)

    def test_success_appends_one_row_per_fragment_and_keeps_status(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            from app.models import NoteAnswer

            _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "statement")
            _, client = clients["alice"]

            resp = self._post(client, note.id, question="first\nsecond")

            assert resp.status_code == 302
            rows = db.session.query(NoteAnswer).order_by(NoteAnswer.id).all()
            assert [r.question for r in rows] == ["first", "second"]
            db.session.refresh(note)
            assert note.status == "new"  # FR-11: the thread never moves the lifecycle
        finally:
            _teardown_test_env(ctx)

    def test_private_note_non_author_404s(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "statement", is_shared=False)
            _, client = clients["bob"]

            assert self._post(client, note.id).status_code == 404
        finally:
            _teardown_test_env(ctx)

    def test_shared_note_non_author_403s(self, monkeypatch):
        """FR-13: a thread is the author's research record — asking on
        someone else's shared note is forbidden even though it is readable."""
        _app, clients, ctx = _setup_test_env()
        try:
            _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "statement")  # shared
            _, client = clients["bob"]

            assert self._post(client, note.id).status_code == 403
        finally:
            _teardown_test_env(ctx)

    def test_blank_question_warns_without_pipeline_call(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            from app.models import NoteAnswer

            stub = _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "statement")
            _, client = clients["alice"]

            resp = self._post(client, note.id, question="   ")

            assert resp.status_code == 302  # warning flash + redirect, no 500
            assert stub.calls == []  # V-1: no RAG call
            assert db.session.query(NoteAnswer).count() == 0
        finally:
            _teardown_test_env(ctx)

    def test_too_many_fragments_warns_without_pipeline_call(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            from app.models import NoteAnswer

            stub = _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "statement")
            _, client = clients["alice"]
            eleven = "\n".join(f"q{i}" for i in range(11))

            resp = self._post(client, note.id, question=eleven)

            assert resp.status_code == 302
            assert stub.calls == []  # V-4
            assert db.session.query(NoteAnswer).count() == 0
        finally:
            _teardown_test_env(ctx)

    def test_pipeline_failure_persists_nothing_and_redirects(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            from app.models import NoteAnswer

            _patch_pipeline(monkeypatch, exc=RuntimeError("provider down"))
            note = _make_note(clients["alice"][0].id, "statement")
            _, client = clients["alice"]

            resp = self._post(client, note.id)

            assert resp.status_code == 302  # graceful danger flash, not a crash
            assert db.session.query(NoteAnswer).count() == 0  # INV-8
        finally:
            _teardown_test_env(ctx)

    def test_success_redirects_to_detail_with_fragment_count_flash(self, monkeypatch):
        _app, clients, ctx = _setup_test_env()
        try:
            _patch_pipeline(monkeypatch)
            note = _make_note(clients["alice"][0].id, "statement")
            _, client = clients["alice"]

            resp = self._post(client, note.id, question="q1\nq2", follow_redirects=True)

            assert resp.status_code == 200
            body = resp.get_data(as_text=True)
            assert "2 fragment(s)" in body  # plan §6 wording, shown on the detail page
        finally:
            _teardown_test_env(ctx)


class TestEditRoute:
    """POST /notepad/<id>/edit — Phase 2 route contract (FR-14…FR-16)."""

    def test_author_updates_statement(self):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "original statement")
            _, client = clients["alice"]

            resp = client.post(f"/notepad/{note.id}/edit", data={"content_text": " updated statement "})

            assert resp.status_code == 302
            db.session.refresh(note)
            assert note.content_text == "updated statement"  # stripped (V-5 input)
        finally:
            _teardown_test_env(ctx)

    def test_edit_leaves_answers_untouched(self):
        """FR-15/INV-1: the thread is append-only; an edit changes the
        statement, never the recorded history."""
        _app, clients, ctx = _setup_test_env()
        try:
            from app.models import NoteAnswer

            note = _make_note(clients["alice"][0].id, "original statement")
            answer = NoteAnswer(note_id=note.id, question="q", answer="a", citations="[]")
            db.session.add(answer)
            db.session.commit()
            before = (answer.question, answer.answer, answer.citations, answer.note_snapshot)

            _, client = clients["alice"]
            client.post(f"/notepad/{note.id}/edit", data={"content_text": "new statement"})

            db.session.refresh(answer)
            assert (answer.question, answer.answer, answer.citations, answer.note_snapshot) == before
        finally:
            _teardown_test_env(ctx)

    def test_blank_content_warns_and_writes_nothing(self):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "original statement")
            _, client = clients["alice"]

            resp = client.post(f"/notepad/{note.id}/edit", data={"content_text": "   "})

            assert resp.status_code == 302  # warning flash + redirect (V-5)
            db.session.refresh(note)
            assert note.content_text == "original statement"
        finally:
            _teardown_test_env(ctx)

    def test_non_author_403s(self):
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "statement")  # shared: readable, not editable
            _, client = clients["bob"]

            resp = client.post(f"/notepad/{note.id}/edit", data={"content_text": "hijacked"})

            assert resp.status_code == 403  # SEC-1
            db.session.refresh(note)
            assert note.content_text == "statement"
        finally:
            _teardown_test_env(ctx)

    def test_other_columns_are_not_exposed(self):
        """FR-16: the form may not smuggle is_shared/status changes."""
        _app, clients, ctx = _setup_test_env()
        try:
            note = _make_note(clients["alice"][0].id, "statement")
            _, client = clients["alice"]

            client.post(
                f"/notepad/{note.id}/edit",
                data={"content_text": "kept", "is_shared": "false", "status": "dismissed"},
            )

            db.session.refresh(note)
            assert note.content_text == "kept"
            assert note.is_shared is True
            assert note.status == "new"
        finally:
            _teardown_test_env(ctx)
