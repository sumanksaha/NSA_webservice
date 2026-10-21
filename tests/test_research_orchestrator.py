"""Tests for the autoresearch orchestrator (session + query-log sweep).

Seams under test (agreed design):

1. ``ResearchOrchestrator.run_session()`` — drains open research targets,
   routes each failure class to a lane, re-asks with the targeted retry
   plan, scores the answer, and resolves / re-opens under budget caps with
   per-target error isolation.
2. ``ResearchOrchestrator.sweep_query_log()`` — turns stored
   ``rag_query_log`` failure signals (errors, hallucinations, low
   groundedness, abstentions, citationless answers) into research targets,
   idempotently.
3. ``run_research_session()`` — cfg-gated convenience entry.

Retrieval/generation are injected fakes: the session behaviour (what it
decides and records) is the contract, not the pipeline.
"""

from __future__ import annotations

from app.extensions import db
from app.models.rag import RAGQueryLog, RAGResearchTarget
from app.rag.research.failure_queue import enqueue_research_target


def _setup_test_env():
    from app import create_app
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    user = User(username="researchuser", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()
    return app, ctx


def _teardown(ctx):
    db.session.remove()
    db.drop_all()
    ctx.pop()


def _seed_target(query="What is the penalty under Section 55?", **kwargs) -> RAGResearchTarget:
    target, _ = enqueue_research_target(query, **kwargs)
    return target


def _good_answer(query, **_kwargs):
    return {
        "query": query,
        "answer": "Section 55 prescribes a penalty of up to Rs 1,00,000. [1]",
        "groundedness_score": 0.85,
        "hallucination_detected": False,
        "citations": [{"chunk_id": "c1", "document_title": "FSS Act 2006", "section_number": "55"}],
        "log_id": "log-1",
    }


def _bad_answer(query, **_kwargs):
    return {
        "query": query,
        "answer": "I cannot determine the penalty from the available corpus.",
        "groundedness_score": 0.2,
        "hallucination_detected": False,
        "citations": [],
        "log_id": "log-2",
    }


class TestSessionResolvesRecoveredTarget:
    def test_passing_reask_resolves_target(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator

            target = _seed_target(failure_class="model_wrong")
            calls = []

            def generator(query, **kwargs):
                calls.append((query, kwargs.get("query_type")))
                return _good_answer(query)

            orchestrator = ResearchOrchestrator(generator=generator)
            report = orchestrator.run_session()

            assert report["targets_researched"] == 1
            assert report["resolved"] == [target.id]
            assert target.status == "resolved"
            assert "passed quality gates" in (target.resolution or "")
            assert calls, "session must actually re-ask the question"
            assert target.detail["research_rounds"] == 1
        finally:
            _teardown(ctx)

    def test_failing_reask_keeps_target_open_and_counts_round(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator

            target = _seed_target(failure_class="model_wrong")
            orchestrator = ResearchOrchestrator(generator=_bad_answer)
            report = orchestrator.run_session()

            assert report["still_open"] == [target.id]
            assert target.status == "open"
            assert target.occurrences == 1
            assert target.detail["research_rounds"] == 1
            assert target.detail["last_outcome"] == "not_recovered"
        finally:
            _teardown(ctx)


class TestSessionBudget:
    def test_llm_budget_cap_stops_session_and_leaves_targets_open(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator

            first = _seed_target(query="question one?", failure_class="model_wrong")
            second = _seed_target(query="question two?", failure_class="model_wrong")

            app = _app
            app.config["RAG_RESEARCH_SESSION_MAX_LLM_CALLS"] = 1
            app.config["RAG_RESEARCH_SESSION_MAX_TARGETS"] = 10

            orchestrator = ResearchOrchestrator(generator=_bad_answer)
            report = orchestrator.run_session()

            assert report["llm_calls_used"] == 1
            assert report["resolved"] == []
            assert first.status == "open" and second.status == "open"
            assert second.id in report["skipped"]
            skipped = {s["target_id"]: s["reason"] for s in report["skipped_detail"]}
            assert skipped[second.id] == "llm_budget_exhausted"
        finally:
            _teardown(ctx)

    def test_error_in_one_target_does_not_kill_session(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator

            target = _seed_target(query="exploding question?", failure_class="model_wrong")

            def generator(query, **kwargs):
                raise RuntimeError("Qdrant is down")

            orchestrator = ResearchOrchestrator(generator=generator)
            report = orchestrator.run_session()

            assert report["errors"]
            assert target.status == "open"
            assert "Qdrant is down" in target.detail["last_error"]
        finally:
            _teardown(ctx)


class TestSessionLanes:
    def test_evaluation_lane_targets_are_skipped_without_llm(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator

            target = _seed_target(query="is section 3 or rule 4 better?", failure_class="evaluation")

            def generator(query, **kwargs):  # pragma: no cover - must not run
                raise AssertionError("evaluation lane must not spend LLM budget")

            orchestrator = ResearchOrchestrator(generator=generator)
            report = orchestrator.run_session()

            assert report["llm_calls_used"] == 0
            assert report["skipped"] == [target.id]
            assert target.detail["needs_human_triage"] is True
        finally:
            _teardown(ctx)

    def test_targeted_plan_is_built_from_failure_class(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator

            _seed_target(query="penalty under section 55", failure_class="evidence_missing")
            seen = {}

            def generator(query, **kwargs):
                seen["query"] = query
                return _good_answer(query)

            orchestrator = ResearchOrchestrator(generator=generator)
            report = orchestrator.run_session()

            # evidence_missing -> missing_provision -> identifier_search arm,
            # whose query builder emits the section-scoped lexical query.
            assert "section 55" in seen["query"]
            assert report["actions"][0]["arm"] == "sparse_identifier"
        finally:
            _teardown(ctx)


class TestSweepQueryLog:
    def test_sweep_enqueues_targets_from_failure_signals_and_marks_rows(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator

            bad = RAGQueryLog(
                query="What is the penalty under Section 55?",
                query_type="penalty",
                response_text="I cannot determine this from the corpus.",
                groundedness_score=0.2,
                content_hash="b" * 64,
            )
            good = RAGQueryLog(
                query="What is Section 55?",
                query_type="section_lookup",
                response_text="Section 55 concerns penalties.",
                groundedness_score=0.9,
                cited_chunk_ids=["c1"],
                content_hash="c" * 64,
            )
            db.session.add_all([bad, good])
            db.session.commit()

            orchestrator = ResearchOrchestrator()
            swept = orchestrator.sweep_query_log()

            assert swept == 1
            target = db.session.query(RAGResearchTarget).one()
            assert target.query == "What is the penalty under Section 55?"
            assert target.source == "detector"
            assert target.failure_class == "evidence_missing"
            assert bad.research_swept_at is not None
            assert good.research_swept_at is not None  # evaluated, nothing to queue
        finally:
            _teardown(ctx)

    def test_sweep_is_idempotent(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator

            db.session.add(
                RAGQueryLog(
                    query="abstained question",
                    query_type="general",
                    response_text="I cannot find a relevant provision in the corpus.",
                    groundedness_score=0.1,
                    content_hash="d" * 64,
                )
            )
            db.session.commit()

            orchestrator = ResearchOrchestrator()
            assert orchestrator.sweep_query_log() == 1
            assert orchestrator.sweep_query_log() == 0
            assert db.session.query(RAGResearchTarget).one().occurrences == 1
        finally:
            _teardown(ctx)

    def test_sweep_maps_error_and_hallucination_rows(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator

            errored = RAGQueryLog(
                query="crashing question",
                query_type="general",
                error="Retrieval timeout",
                content_hash="e" * 64,
            )
            hallucinated = RAGQueryLog(
                query="hallucinated question",
                query_type="general",
                response_text="Section 99 prescribes a fine of Rs 50.",
                groundedness_score=0.8,
                hallucination_detected=True,
                cited_chunk_ids=["c1"],
                content_hash="f" * 64,
            )
            db.session.add_all([errored, hallucinated])
            db.session.commit()

            ResearchOrchestrator().sweep_query_log()

            by_query = {t.query: t for t in db.session.query(RAGResearchTarget).all()}
            assert by_query["crashing question"].failure_class == "pipeline_error"
            assert by_query["hallucinated question"].failure_class == "ungrounded"
        finally:
            _teardown(ctx)


class TestGatedEntry:
    def test_disabled_master_switch_returns_disabled_report(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import run_research_session

            _seed_target(failure_class="model_wrong")
            report = run_research_session()
            assert report["enabled"] is False
            assert report["targets_researched"] == 0
            target = db.session.query(RAGResearchTarget).one()
            assert target.status == "open"
        finally:
            _teardown(ctx)

    def test_enabled_switch_runs_session(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.agent.research_orchestrator import ResearchOrchestrator, run_research_session

            _seed_target(failure_class="model_wrong")
            app = _app
            app.config["RAG_RESEARCH_ENABLED"] = "true"
            orig = ResearchOrchestrator.run_session

            def _stub(self, **_kwargs):
                self.last_report = {"targets_researched": 0}
                return {"enabled": True, "targets_researched": 0}

            ResearchOrchestrator.run_session = _stub
            try:
                report = run_research_session()
            finally:
                ResearchOrchestrator.run_session = orig
            assert report["enabled"] is True
        finally:
            _teardown(ctx)
