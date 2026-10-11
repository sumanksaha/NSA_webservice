"""Tests for the human answer-rating tool and the research queue it feeds.

Covers:

- ``RAGAnswerFeedback`` / ``RAGResearchTarget`` models
- ``app.rag.research.failure_queue`` — normalization/hashing, upsert
  dedup, threshold behaviour, reason-to-lane mapping
- ``POST /api/rag/feedback`` — validation, auth gate, auto-enqueue of low
  ratings, ``query_log_id`` resolution

Follows the DB-setup pattern from ``tests/test_retrieval_logger.py`` and the
authenticated-client pattern from ``tests/test_ai_assistant.py``.
"""

from __future__ import annotations

from app.extensions import db
from app.models.rag import RAGAnswerFeedback, RAGQueryLog, RAGResearchTarget
from app.rag.research.failure_queue import (
    enqueue_from_feedback,
    enqueue_research_target,
    normalize_query,
    open_targets,
    query_hash,
)


def _setup_test_env():
    """Test app with in-memory SQLite; returns (app, client, app_context)."""
    from app import create_app
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    user = User(username="feedbackuser", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
    return app, client, ctx


def _setup_unauthenticated_client():
    from app import create_app
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    db.session.add(User(username="feedbackanon", password_hash="pbkdf2:sha256$test$dummy"))
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()
    return app, app.test_client(), ctx


def _teardown(ctx):
    db.session.remove()
    db.drop_all()
    ctx.pop()


def _seed_query_log(query="What is the penalty under Section 55?") -> RAGQueryLog:
    row = RAGQueryLog(
        query=query,
        query_type="penalty",
        retrieved_chunk_ids=["c1"],
        response_text="Section 55 prescribes a penalty.",
        content_hash="a" * 64,
        pipeline="legacy",
    )
    db.session.add(row)
    db.session.commit()
    return row


# --------------------------------------------------------------------------- #
# Models                                                                       #
# --------------------------------------------------------------------------- #


class TestRAGAnswerFeedbackModel:
    def test_row_stores_rating_and_defaults(self):
        _app, _client, ctx = _setup_test_env()
        try:
            row = RAGAnswerFeedback(query="q?", rating=2)
            db.session.add(row)
            db.session.commit()
            assert row.id
            assert row.failure_reasons == []
            assert row.rating == 2
        finally:
            _teardown(ctx)

    def test_research_target_defaults(self):
        _app, _client, ctx = _setup_test_env()
        try:
            target = RAGResearchTarget(
                query_hash=query_hash("q?"),
                query="q?",
                failure_class="model_wrong",
            )
            db.session.add(target)
            db.session.commit()
            assert target.status == "open"
            assert target.occurrences == 1
            assert target.source == "human_rating"
        finally:
            _teardown(ctx)


# --------------------------------------------------------------------------- #
# Failure queue                                                                #
# --------------------------------------------------------------------------- #


class TestNormalizeAndHash:
    def test_normalize_query_strips_punctuation_and_case(self):
        assert normalize_query("  What is Section 55?! ") == "what is section 55"

    def test_hash_is_stable_across_phrasing_noise(self):
        assert query_hash("Section 55 penalty") == query_hash("section 55, penalty")

    def test_hash_distinguishes_different_questions(self):
        assert query_hash("section 55") != query_hash("section 56")


class TestEnqueueResearchTarget:
    def test_first_call_creates_open_target(self):
        _app, _client, ctx = _setup_test_env()
        try:
            target, created = enqueue_research_target("Section 55?", failure_class="model_wrong")
            assert created is True
            assert target.status == "open"
            assert target.occurrences == 1
        finally:
            _teardown(ctx)

    def test_repeat_call_increments_occurrences_without_duplicate(self):
        _app, _client, ctx = _setup_test_env()
        try:
            first, _ = enqueue_research_target("Section 55?", failure_class="model_wrong")
            second, created = enqueue_research_target("Section 55?", failure_class="model_wrong")
            assert created is False
            assert second.id == first.id
            assert second.occurrences == 2
            assert db.session.query(RAGResearchTarget).count() == 1
        finally:
            _teardown(ctx)

    def test_resolved_target_reopens_on_new_failure(self):
        _app, _client, ctx = _setup_test_env()
        try:
            target, _ = enqueue_research_target("Section 55?", failure_class="model_wrong")
            target.status = "resolved"
            target.resolution = "filled corpus"
            db.session.commit()

            again, _ = enqueue_research_target("Section 55?", failure_class="model_wrong")
            assert again.status == "open"
            assert again.resolution is None
        finally:
            _teardown(ctx)

    def test_detail_tracks_worst_rating_and_reasons(self):
        _app, _client, ctx = _setup_test_env()
        try:
            enqueue_research_target(
                "Q", failure_class="model_wrong", detail={"rating": 2, "failure_reasons": ["incomplete"]}
            )
            target, _ = enqueue_research_target(
                "Q", failure_class="model_wrong", detail={"rating": 1, "failure_reasons": ["missed_proviso"]}
            )
            assert target.detail["min_rating"] == 1
            assert target.detail["last_rating"] == 1
            assert sorted(target.detail["failure_reasons"]) == ["incomplete", "missed_proviso"]
            assert target.detail["ratings"] == 2
        finally:
            _teardown(ctx)

    def test_open_targets_orders_by_occurrences(self):
        _app, _client, ctx = _setup_test_env()
        try:
            enqueue_research_target("rare", failure_class="model_wrong")
            enqueue_research_target("frequent", failure_class="model_wrong")
            enqueue_research_target("frequent", failure_class="model_wrong")
            enqueue_research_target("frequent", failure_class="model_wrong")
            targets = open_targets()
            assert targets[0].query == "frequent"
        finally:
            _teardown(ctx)


class TestEnqueueFromFeedback:
    def test_low_rating_queues_target(self):
        _app, _client, ctx = _setup_test_env()
        try:
            _seed_query_log()
            feedback = RAGAnswerFeedback(query="What is the penalty under Section 55?", rating=1)
            db.session.add(feedback)
            db.session.commit()
            target, outcome = enqueue_from_feedback(feedback)
            assert outcome == "queued"
            assert target is not None
            assert target.failure_class == "model_wrong"
            assert target.query == "What is the penalty under Section 55?"
        finally:
            _teardown(ctx)

    def test_good_rating_does_not_queue(self):
        _app, _client, ctx = _setup_test_env()
        try:
            feedback = RAGAnswerFeedback(query="fine answer", rating=4)
            db.session.add(feedback)
            db.session.commit()
            target, outcome = enqueue_from_feedback(feedback)
            assert target is None
            assert outcome == "rating_above_threshold"
            assert db.session.query(RAGResearchTarget).count() == 0
        finally:
            _teardown(ctx)

    def test_retrieval_reason_routes_to_context_assembly(self):
        _app, _client, ctx = _setup_test_env()
        try:
            feedback = RAGAnswerFeedback(query="q", rating=1, failure_reasons=["irrelevant_retrieval"])
            db.session.add(feedback)
            db.session.commit()
            target, _outcome = enqueue_from_feedback(feedback)
            assert target.failure_class == "context_assembly"
        finally:
            _teardown(ctx)

    def test_hallucination_reason_routes_to_ungrounded(self):
        _app, _client, ctx = _setup_test_env()
        try:
            feedback = RAGAnswerFeedback(query="q", rating=1, failure_reasons=["hallucinated_content"])
            db.session.add(feedback)
            db.session.commit()
            target, _outcome = enqueue_from_feedback(feedback)
            assert target.failure_class == "ungrounded"
        finally:
            _teardown(ctx)

    def test_rating_at_threshold_queues(self):
        _app, _client, ctx = _setup_test_env()
        try:
            feedback = RAGAnswerFeedback(query="q", rating=2)
            db.session.add(feedback)
            db.session.commit()
            target, outcome = enqueue_from_feedback(feedback)
            assert outcome == "queued" and target is not None
        finally:
            _teardown(ctx)


# --------------------------------------------------------------------------- #
# Route                                                                        #
# --------------------------------------------------------------------------- #


class TestFeedbackRouteAuth:
    def test_requires_login(self):
        _app, client, ctx = _setup_unauthenticated_client()
        try:
            resp = client.post("/api/rag/feedback", json={"rating": 1, "query": "q"}, follow_redirects=False)
            assert resp.status_code == 302
            assert "/auth/login" in resp.headers["Location"]
        finally:
            _teardown(ctx)


class TestFeedbackRoute:
    def test_low_rating_creates_feedback_and_queues_target(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post(
                "/api/rag/feedback",
                json={
                    "query": "What is the penalty under Section 55 of the FSS Act?",
                    "rating": 1,
                    "failure_reasons": ["missed_proviso"],
                    "comment": "ignored the proviso",
                },
            )
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["ok"] is True
            assert data["enqueued"] is True
            assert data["queue_outcome"] == "queued"

            feedback = db.session.get(RAGAnswerFeedback, data["feedback_id"])
            assert feedback is not None
            assert feedback.rating == 1
            assert feedback.failure_reasons == ["missed_proviso"]

            target = db.session.get(RAGResearchTarget, data["target_id"])
            assert target is not None
            assert target.occurrences == 1
            assert target.detail["min_rating"] == 1
        finally:
            _teardown(ctx)

    def test_good_rating_stored_but_not_queued(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/api/rag/feedback", json={"query": "q", "rating": 5})
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["enqueued"] is False
            assert data["target_id"] is None
            assert db.session.query(RAGAnswerFeedback).count() == 1
            assert db.session.query(RAGResearchTarget).count() == 0
        finally:
            _teardown(ctx)

    def test_repeat_rating_increments_target_occurrences(self):
        _app, client, ctx = _setup_test_env()
        try:
            payload = {"query": "Section 55 penalty?", "rating": 1}
            first = client.post("/api/rag/feedback", json=payload).get_json()
            second = client.post("/api/rag/feedback", json=payload).get_json()
            assert first["target_id"] == second["target_id"]
            target = db.session.get(RAGResearchTarget, first["target_id"])
            assert target.occurrences == 2
            assert target.detail["ratings"] == 2
        finally:
            _teardown(ctx)

    def test_query_log_id_resolves_query_and_pipeline(self):
        _app, client, ctx = _setup_test_env()
        try:
            row = _seed_query_log()
            resp = client.post("/api/rag/feedback", json={"query_log_id": row.id, "rating": 2})
            assert resp.status_code == 201
            feedback = db.session.query(RAGAnswerFeedback).first()
            assert feedback.query == row.query
            assert feedback.query_type == "penalty"
            assert feedback.pipeline == "legacy"
            assert feedback.query_log_id == row.id
            assert resp.get_json()["enqueued"] is True
        finally:
            _teardown(ctx)

    def test_unknown_query_log_id_returns_404(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post(
                "/api/rag/feedback", json={"query_log_id": "00000000-0000-0000-0000-000000000000", "rating": 1}
            )
            assert resp.status_code == 404
        finally:
            _teardown(ctx)

    def test_missing_query_without_log_id_returns_400(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/api/rag/feedback", json={"rating": 1})
            assert resp.status_code == 400
            assert "query" in resp.get_json()["error"]
        finally:
            _teardown(ctx)

    def test_invalid_rating_returns_400(self):
        _app, client, ctx = _setup_test_env()
        try:
            for bad in (0, 6, "3", True):
                resp = client.post("/api/rag/feedback", json={"query": "q", "rating": bad})
                assert resp.status_code == 400, f"rating={bad!r} should be rejected"
        finally:
            _teardown(ctx)

    def test_invalid_failure_reason_returns_400(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.post(
                "/api/rag/feedback", json={"query": "q", "rating": 1, "failure_reasons": ["not_a_reason"]}
            )
            assert resp.status_code == 400
        finally:
            _teardown(ctx)
