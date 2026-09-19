"""Tests for the shared agent service core (app/rag/agent/service.py).

Pins the result-shape contract: ``run_agent`` returns the unwrapped
``RAGResponse`` dict while ``resume_agent`` returns full state (and test
doubles use either shape).  A double-unwrap here once turned every real
200 into ``{}`` while fakes kept passing — these tests cover both shapes.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.rag.agent.service import resume_agent_query, run_agent_query


def _response(answer="agent answer"):
    return {"query": "q", "answer": answer, "pipeline": "agent"}


def test_run_agent_query_unwrapped_shape(monkeypatch):
    """Real ``run_agent`` contract: already-unwrapped response dict."""
    import app.rag.agent.graph as graph_mod

    monkeypatch.setattr(graph_mod, "run_agent", lambda state, **kw: _response())
    status, body = run_agent_query(query="q")
    assert status == 200
    assert body["answer"] == "agent answer"
    assert body["pipeline"] == "agent"


def test_run_agent_query_full_state_shape(monkeypatch):
    """Full-state shape (``{"response": ...}``) is also accepted."""
    import app.rag.agent.graph as graph_mod

    monkeypatch.setattr(graph_mod, "run_agent", lambda state, **kw: {"response": _response()})
    status, body = run_agent_query(query="q")
    assert status == 200
    assert body["answer"] == "agent answer"


def test_resume_agent_query_full_state_shape(monkeypatch):
    """Real ``resume_agent`` contract: full state with a ``response`` key."""
    import app.rag.agent.graph as graph_mod

    monkeypatch.setattr(graph_mod, "resume_agent", lambda tid, **kw: {"response": _response("resumed")})
    status, body = resume_agent_query(thread_id="t-1", approved=True, hitl=True)
    assert status == 200
    assert body["answer"] == "resumed"


def test_resume_agent_query_unwrapped_shape(monkeypatch):
    import app.rag.agent.graph as graph_mod

    monkeypatch.setattr(graph_mod, "resume_agent", lambda tid, **kw: _response("resumed"))
    status, body = resume_agent_query(thread_id="t-1", approved=True, hitl=True)
    assert status == 200
    assert body["answer"] == "resumed"


def test_run_agent_query_validation():
    assert run_agent_query(query="   ")[0] == 400
    assert run_agent_query(query="q", top_k=0)[0] == 400
    assert run_agent_query(query="q", top_k=5, thread_id="  ", hitl=True)[0] == 400


def test_resume_agent_query_validation():
    assert resume_agent_query(thread_id="t", hitl=False)[0] == 400
    assert resume_agent_query(thread_id="  ", hitl=True)[0] == 400
    assert resume_agent_query(thread_id="t", approved="yes", hitl=True)[0] == 400


def test_run_agent_query_import_error_maps_503(monkeypatch):
    import app.rag.agent.graph as graph_mod

    def _boom(state, **kw):
        raise ImportError("The LangGraph agent pipeline requires 'langgraph'.")

    monkeypatch.setattr(graph_mod, "run_agent", _boom)
    status, body = run_agent_query(query="q")
    assert status == 503
    assert "error" in body


def test_interrupt_maps_202_with_durable(monkeypatch):
    import app.rag.agent.graph as graph_mod

    class _FakeInterrupt:
        value: ClassVar[dict] = {"reason": "needs human review"}

    monkeypatch.setattr(graph_mod, "run_agent", lambda state, **kw: {"__interrupt__": [_FakeInterrupt()]})
    status, body = run_agent_query(query="q", thread_id="tid-1", hitl=True, resume_hint="hint")
    assert status == 202
    assert body["status"] == "awaiting_review"
    assert body["thread_id"] == "tid-1"
    assert body["hint"] == "hint"
    assert "durable" in body


@pytest.mark.parametrize("shape", ["full", "unwrapped"])
def test_completed_shapes_agree(monkeypatch, shape):
    """Both shapes yield identical 200 payloads for the same answer."""
    import app.rag.agent.graph as graph_mod

    payload = {"response": _response()} if shape == "full" else _response()
    monkeypatch.setattr(graph_mod, "run_agent", lambda state, **kw: payload)
    assert run_agent_query(query="q") == (200, _response())
