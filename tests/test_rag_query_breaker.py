"""Route-level tests for the circuit breaker on POST /api/rag/query (2026-08-23).

The Flask query route now routes through ``ResilientRAGPipeline``: repeated
pipeline failures open the circuit and requests degrade to the stub fallback
(HTTP 200, ``debug.degraded_mode``) instead of hammering a dead Qdrant/LLM —
matching the protection ``/api/v2/rag/generate`` already had.

No Qdrant / network required — the pipeline entry point is monkeypatched.
"""

from __future__ import annotations

import pytest

from tests.test_rag_routes import _setup_test_env


@pytest.fixture(autouse=True)
def _fresh_breaker():
    """Give every test a pristine breaker (and leave none behind).

    The breaker is a module-level singleton so its state survives across
    real requests — but tests must not leak circuit state into each other.
    """
    import app.rag.routes as routes

    routes._query_breaker = None
    yield
    routes._query_breaker = None


@pytest.fixture(autouse=True)
def _app_env():
    app, client, app_context = _setup_test_env()
    yield app, client
    app_context.pop()


def _ok_result(query: str) -> dict:
    return {
        "query": query,
        "answer": f"answer for {query}",
        "groundedness_score": 0.9,
        "hallucination_detected": False,
        "citations": [],
        "retrieved_chunks": [],
    }


class TestQueryRouteBreaker:
    def test_success_passes_through_breaker(self, monkeypatch, _app_env):
        """Healthy pipeline → normal 200 response with the pipeline payload."""
        _app, client = _app_env
        import app.rag.tasks as tasks

        monkeypatch.setattr(
            tasks,
            "run_generation_pipeline",
            lambda query, **kw: _ok_result(query),
        )

        resp = client.post("/api/rag/query", json={"query": "penalty"})
        assert resp.status_code == 200
        assert resp.get_json()["answer"] == "answer for penalty"

    def test_failure_degrades_to_stub_fallback_not_500(self, monkeypatch, _app_env):
        """A failing pipeline degrades gracefully instead of raising."""
        _app, client = _app_env
        import app.rag.tasks as tasks

        def boom(*args, **kwargs):
            raise RuntimeError("qdrant down")

        monkeypatch.setattr(tasks, "run_generation_pipeline", boom)

        resp = client.post("/api/rag/query", json={"query": "penalty"})
        assert resp.status_code == 200
        data = resp.get_json()
        # Either the stub fallback answered or the last-resort error dict was
        # returned — in both cases the request degrades, never 500s.
        degraded = data.get("debug", {}).get("degraded_mode") is True or "error" in data
        assert degraded

    def test_circuit_opens_after_threshold_and_fails_fast(self, monkeypatch, _app_env):
        """3 consecutive failures open the circuit; later calls skip the pipeline."""
        _app, client = _app_env
        import app.rag.tasks as tasks

        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("down")

        monkeypatch.setattr(tasks, "run_generation_pipeline", flaky)

        for _ in range(3):  # ResilientRAGPipeline default failure_threshold=3
            resp = client.post("/api/rag/query", json={"query": "q"})
            assert resp.status_code == 200
        assert calls["n"] == 3

        # Pipeline has "recovered", but the circuit is OPEN → fail fast.
        monkeypatch.setattr(tasks, "run_generation_pipeline", lambda query, **kw: _ok_result(query))
        resp = client.post("/api/rag/query", json={"query": "q"})
        assert resp.status_code == 200
        assert calls["n"] == 3  # pipeline NOT invoked while the circuit is open
        assert resp.get_json().get("debug", {}).get("degraded_mode") is True

    def test_success_resets_failure_count(self, monkeypatch, _app_env):
        """Interleaved successes stop the circuit from opening."""
        _app, client = _app_env
        import app.rag.tasks as tasks

        state = {"fail_next": True}
        calls = {"n": 0}

        def flaky(query, **kw):
            calls["n"] += 1
            if state["fail_next"]:
                state["fail_next"] = False
                raise RuntimeError("transient")
            return _ok_result(query)

        monkeypatch.setattr(tasks, "run_generation_pipeline", flaky)

        # fail → success → fail → success … never 3 consecutive failures.
        for i in range(6):
            state["fail_next"] = i % 2 == 0
            resp = client.post("/api/rag/query", json={"query": "q"})
            assert resp.status_code == 200
        assert calls["n"] == 6  # every request reached the (flaky) pipeline

    def test_agent_delegation_inherits_breaker_protection(self, monkeypatch, _app_env):
        """/api/rag/query/agent (flag off) delegates through the same breaker."""
        app, client = _app_env
        app.config["RAG_USE_AGENT_PIPELINE"] = False
        import app.rag.tasks as tasks

        def boom(*args, **kwargs):
            raise RuntimeError("qdrant down")

        monkeypatch.setattr(tasks, "run_generation_pipeline", boom)

        resp = client.post("/api/rag/query/agent", json={"query": "penalty"})
        assert resp.status_code == 200  # degraded, not 500
        data = resp.get_json()
        assert data.get("debug", {}).get("degraded_mode") is True or "error" in data
