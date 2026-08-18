"""Tests for LangGraph M5 — human-in-the-loop + checkpointing (plan §5.4).

Covers the review interrupt node, conditional routing on the human
decision, thread-aware invocation under a checkpointer, and the resume
flow (approved → finalize, rejected → expand-and-retry).  All pipeline
entry points are monkeypatched (no Qdrant / network / torch); the
stub-LLM autouse fixture keeps generation offline.
"""

from __future__ import annotations

import pytest

from app.rag.agent.graph import (
    _build_checkpointer,
    agent_graph_hitl,
    build_graph,
    resume_agent,
    review_node,
    route_after_review,
    run_agent,
)
from app.rag.agent.state import initial_state

pytestmark = pytest.mark.usefixtures("_rag_stub_llm_env")


@pytest.fixture()
def app_env():
    """Build the test app/client once per test (mirrors test_rag_routes)."""
    from tests.test_rag_routes import _setup_test_env

    app, client, app_context = _setup_test_env()
    yield app, client
    app_context.pop()


def _patch_pipeline(monkeypatch, groundedness=0.9, answer="Section 50 answer"):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {
            "chunks": [{"chunk_id": "c1", "score": 0.9, "text": "Section 50 text"}],
            "query_type": "offence",
            "retrieval_latency_ms": 10,
            "log_id": "log-1",
        },
    )
    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {
            "answer": answer,
            "groundedness_score": groundedness,
            "hallucination_detected": False,
            "query_type": "offence",
        },
    )


# ---------------------------------------------------------------------- #
# Graph structure
# ---------------------------------------------------------------------- #


def test_hitl_graph_has_review_node():
    nodes = set(agent_graph_hitl.get_graph().nodes.keys())
    assert "review" in nodes
    assert "verify" in nodes
    assert "finalize" in nodes


def test_default_graph_has_no_review_node():
    nodes = set(build_graph(hitl=False).get_graph().nodes.keys())
    assert "review" not in nodes


# ---------------------------------------------------------------------- #
# Checkpointer selection
# ---------------------------------------------------------------------- #


def test_checkpointer_memory(monkeypatch):
    monkeypatch.setenv("RAG_AGENT_CHECKPOINTER", "memory")
    cp = _build_checkpointer()
    assert cp is not None
    assert "Saver" in type(cp).__name__


def test_checkpointer_none(monkeypatch):
    monkeypatch.setenv("RAG_AGENT_CHECKPOINTER", "none")
    assert _build_checkpointer() is None


def test_checkpointer_postgres_degrades_gracefully(monkeypatch):
    """No DATABASE_URL → PostgresSaver degrades to None, never raises."""
    monkeypatch.setenv("RAG_AGENT_CHECKPOINTER", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _build_checkpointer() is None


# ---------------------------------------------------------------------- #
# review_node / route_after_review
# ---------------------------------------------------------------------- #


def test_route_after_review_approved_finalizes():
    assert route_after_review({"approved": True}) == "finalize"


def test_route_after_review_rejected_expands():
    assert route_after_review({"approved": False}) == "expand_query"


def test_review_node_payload_and_resume_value(monkeypatch):
    """The interrupt payload carries the answer; the resume value lands as approved."""
    import langgraph.types as lg_types

    captured = {}

    def fake_interrupt(payload):
        captured["payload"] = payload
        return {"approved": True}

    monkeypatch.setattr(lg_types, "interrupt", fake_interrupt)
    state = initial_state("q")
    state.update({"answer": "ans", "groundedness": 0.9, "retry_count": 1})
    out = review_node(state)
    assert captured["payload"]["answer"] == "ans"
    assert captured["payload"]["groundedness"] == 0.9
    assert captured["payload"]["retry_count"] == 1
    assert out == {"approved": True}

    # A bare-bool resume value is also accepted.
    def fake_interrupt_bool(payload):
        return False

    monkeypatch.setattr(lg_types, "interrupt", fake_interrupt_bool)
    assert review_node(state) == {"approved": False}


# ---------------------------------------------------------------------- #
# End-to-end HITL flow (thread-aware, MemorySaver)
# ---------------------------------------------------------------------- #


def test_hitl_run_pauses_at_review(monkeypatch):
    _patch_pipeline(monkeypatch, groundedness=0.9)
    result = run_agent(
        initial_state("penalty for substandard food"),
        thread_id="t1",
        hitl=True,
        checkpointer=_build_checkpointer("memory"),
    )
    # Paused at the interrupt — no final response, review payload present.
    assert "__interrupt__" in result
    interrupts = result["__interrupt__"]
    assert interrupts
    assert interrupts[0].value["message"].startswith("Review")


def test_hitl_resume_approved_finalizes(monkeypatch):
    _patch_pipeline(monkeypatch, groundedness=0.9)
    cp = _build_checkpointer("memory")
    run_agent(initial_state("penalty"), thread_id="t-approve", hitl=True, checkpointer=cp)
    result = resume_agent("t-approve", approved=True, checkpointer=cp)
    assert "__interrupt__" not in result
    response = result.get("response") or {}
    assert response.get("pipeline") == "agent"
    assert response.get("answer") == "Section 50 answer"
    assert response["agent"]["retry_count"] == 0


def test_hitl_resume_rejected_retries_then_finalizes(monkeypatch):
    """Rejecting the review routes to expand_query → regenerate → finalize."""
    import app.rag.tasks as tasks

    calls = {"n": 0}

    def fake_gen(query, **kw):
        calls["n"] += 1
        return {
            "answer": f"answer {calls['n']}",
            "groundedness_score": 0.9,
            "hallucination_detected": False,
            "query_type": "offence",
        }

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {
            "chunks": [{"chunk_id": "c1", "score": 0.9, "text": "Sec 50"}],
            "query_type": "offence",
            "retrieval_latency_ms": 10,
            "log_id": "log-1",
        },
    )
    monkeypatch.setattr(tasks, "run_generation_pipeline", fake_gen)

    cp = _build_checkpointer("memory")
    run_agent(initial_state("penalty"), thread_id="t-reject", hitl=True, checkpointer=cp)
    # Reject → expand_query → retrieve → generate → review again.
    result = resume_agent("t-reject", approved=False, checkpointer=cp)
    # Second review pause (retry_count bumped, answer regenerated).
    assert "__interrupt__" in result
    assert result.get("retry_count") == 1
    # Approve on the second review.
    result2 = resume_agent("t-reject", approved=True, checkpointer=cp)
    assert "__interrupt__" not in result2
    response = result2.get("response") or {}
    assert response["agent"]["retry_count"] == 1
    assert calls["n"] == 2


def test_resume_without_checkpointer_raises(monkeypatch):
    """RAG_AGENT_CHECKPOINTER=none → no saver → resume must raise ValueError."""
    monkeypatch.setenv("RAG_AGENT_CHECKPOINTER", "none")
    with pytest.raises(ValueError, match="checkpointer"):
        resume_agent("t-x", approved=True, checkpointer=None)


# ---------------------------------------------------------------------- #
# Route-level (202 awaiting_review / resume 200)
# ---------------------------------------------------------------------- #


def test_agent_route_hitl_202_then_resume_200(monkeypatch, app_env):
    """Full HTTP flow: agent route pauses (202) → resume (200)."""
    import app.rag.tasks as tasks

    app, client = app_env
    app.config["RAG_USE_AGENT_PIPELINE"] = True
    app.config["RAG_AGENT_HITL"] = True

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {
            "chunks": [{"chunk_id": "c1", "score": 0.9, "text": "Sec 50"}],
            "query_type": "offence",
            "retrieval_latency_ms": 10,
            "log_id": "log-1",
        },
    )
    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {
            "answer": "ans",
            "groundedness_score": 0.9,
            "hallucination_detected": False,
            "query_type": "offence",
        },
    )

    resp = client.post("/api/rag/query/agent", json={"query": "penalty", "thread_id": "http-1"})
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "awaiting_review"
    assert data["thread_id"] == "http-1"

    resume = client.post("/api/rag/query/agent/resume", json={"thread_id": "http-1", "approved": True})
    assert resume.status_code == 200
    assert resume.get_json()["pipeline"] == "agent"


def test_resume_route_requires_hitl(app_env):

    app, client = app_env
    app.config["RAG_USE_AGENT_PIPELINE"] = True
    app.config["RAG_AGENT_HITL"] = False
    resp = client.post("/api/rag/query/agent/resume", json={"thread_id": "t", "approved": True})
    assert resp.status_code == 400


def test_resume_route_validation(app_env):

    app, client = app_env
    app.config["RAG_AGENT_HITL"] = True
    assert client.post("/api/rag/query/agent/resume", json={}).status_code == 400
    assert client.post("/api/rag/query/agent/resume", json={"thread_id": "t", "approved": "yes"}).status_code == 400
