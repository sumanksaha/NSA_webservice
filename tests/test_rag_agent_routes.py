"""Tests for the LangGraph agent route (M3, plan §6).

``POST /api/rag/query/agent`` — validation 400s, RAG-disabled 503s,
flag-off delegation to the legacy pipeline, and the agent path (with the
graph's pipeline entry points monkeypatched — no Qdrant / network).
"""

from __future__ import annotations

import pytest

from tests.test_rag_routes import _setup_test_env


@pytest.fixture(autouse=True)
def _app_env():
    """Build the test app/client once per test (mirrors test_rag_routes)."""
    app, client, app_context = _setup_test_env()
    yield app, client
    app_context.pop()


def _patch_legacy_query(monkeypatch, app):
    """Point the legacy query route's generation call at a fake."""
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {
            "query": query,
            "answer": "legacy answer",
            "groundedness_score": 0.8,
            "query_type": "general",
            "pipeline": "legacy",
        },
    )


def _patch_agent_graph(monkeypatch, result=None):
    """Point the agent route's graph invocation at a fake."""
    import app.rag.agent.graph as graph_mod

    fake = result or {
        "query": "q",
        "answer": "agent answer",
        "groundedness_score": 0.9,
        "query_type": "offence",
        "pipeline": "agent",
        "agent": {"retry_count": 0, "expanded_query": None, "audit_trail": []},
    }
    monkeypatch.setattr(graph_mod, "run_agent", lambda state: fake)


# ---------------------------------------------------------------------- #
# Validation & gating
# ---------------------------------------------------------------------- #


def test_agent_route_missing_query_400(monkeypatch, _app_env):
    app, client = _app_env
    app.config["RAG_USE_AGENT_PIPELINE"] = True
    resp = client.post("/api/rag/query/agent", json={})
    assert resp.status_code == 400


def test_agent_route_empty_query_400(monkeypatch, _app_env):
    app, client = _app_env
    app.config["RAG_USE_AGENT_PIPELINE"] = True
    resp = client.post("/api/rag/query/agent", json={"query": "   "})
    assert resp.status_code == 400


def test_agent_route_bad_top_k_400(monkeypatch, _app_env):
    app, client = _app_env
    app.config["RAG_USE_AGENT_PIPELINE"] = True
    resp = client.post("/api/rag/query/agent", json={"query": "q", "top_k": 0})
    assert resp.status_code == 400


def test_agent_route_503_when_rag_disabled(monkeypatch, _app_env):
    app, client = _app_env
    app.config["RAG_ENABLED"] = False
    resp = client.post("/api/rag/query/agent", json={"query": "q"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------- #
# Flag behavior
# ---------------------------------------------------------------------- #


def test_agent_route_delegates_to_legacy_when_flag_off(monkeypatch, _app_env):
    """RAG_USE_AGENT_PIPELINE=false → identical to /api/rag/query."""
    app, client = _app_env
    app.config["RAG_USE_AGENT_PIPELINE"] = False
    _patch_legacy_query(monkeypatch, app)

    agent_resp = client.post("/api/rag/query/agent", json={"query": "penalty"})
    legacy_resp = client.post("/api/rag/query", json={"query": "penalty"})

    assert agent_resp.status_code == 200
    assert legacy_resp.status_code == 200
    assert agent_resp.get_json()["answer"] == "legacy answer"
    # Same response shape as the legacy route.
    assert agent_resp.get_json()["query"] == "penalty"
    assert "pipeline" not in agent_resp.get_json() or agent_resp.get_json()["pipeline"] == "legacy"


def test_agent_route_runs_agent_when_flag_on(monkeypatch, _app_env):
    app, client = _app_env
    app.config["RAG_USE_AGENT_PIPELINE"] = True
    _patch_agent_graph(monkeypatch)

    resp = client.post("/api/rag/query/agent", json={"query": "who appoints the FSO"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pipeline"] == "agent"
    assert data["agent"]["retry_count"] == 0
    assert data["answer"] == "agent answer"


def test_agent_route_propagates_collection_and_filters(monkeypatch, _app_env):
    """The flag-on path forwards collection_name / filters to the graph."""
    app, client = _app_env
    app.config["RAG_USE_AGENT_PIPELINE"] = True
    captured = {}

    import app.rag.agent.graph as graph_mod

    def fake_run_agent(state):
        captured["collection_name"] = state.get("collection_name")
        captured["filters"] = state.get("filters")
        captured["top_k"] = state.get("top_k")
        return {"pipeline": "agent", "answer": "ok", "agent": {"retry_count": 0}}

    monkeypatch.setattr(graph_mod, "run_agent", fake_run_agent)

    resp = client.post(
        "/api/rag/query/agent",
        json={
            "query": "q",
            "top_k": 7,
            "collection_name": "criminal_legal_768",
            "filters": {"act_name": "BNS"},
        },
    )
    assert resp.status_code == 200
    assert captured["collection_name"] == "criminal_legal_768"
    assert captured["filters"] == {"act_name": "BNS"}
    assert captured["top_k"] == 7
