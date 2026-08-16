"""Tests for the LangGraph agent graph (M3 + M4, plan §6).

The graph is exercised end-to-end with ``run_retrieval_pipeline`` and
``run_generation_pipeline`` monkeypatched (no Qdrant / network / torch),
and the stub-LLM fixture keeps ``expand_query_node`` offline.
"""

from __future__ import annotations

import pytest

from app.rag.agent.graph import GROUNDEDNESS_THRESHOLD, build_graph, route_after_verify, run_agent
from app.rag.agent.nodes import GROUNDEDNESS_THRESHOLD as NODE_THRESHOLD
from app.rag.agent.state import initial_state

pytestmark = pytest.mark.usefixtures("_rag_stub_llm_env")


def _graph():
    return build_graph()


def _patch_pipeline(monkeypatch, groundedness, retrieve_chunks=None):
    """Patch both pipeline entry points on app.rag.tasks."""
    import app.rag.tasks as tasks

    chunks = retrieve_chunks or [{"chunk_id": "c1", "score": 0.9, "text": "Section 50 text"}]

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {
            "chunks": chunks,
            "query_type": "offence",
            "retrieval_latency_ms": 10,
            "log_id": "log-1",
        },
    )
    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {
            "answer": "Section 50 prescribes the penalty.",
            "groundedness_score": groundedness,
            "hallucination_detected": groundedness < 0.5,
            "query_type": "offence",
        },
    )


# ---------------------------------------------------------------------- #
# Graph structure
# ---------------------------------------------------------------------- #


def test_graph_compiles():
    graph = _graph()
    assert graph is not None


def test_graph_has_expected_nodes():
    nodes = set(_graph().get_graph().nodes.keys())
    assert {
        "__start__",
        "classify",
        "retrieve",
        "generate",
        "verify",
        "expand_query",
        "finalize",
        "__end__",
    } <= nodes


def test_graph_has_evidence_node_when_flag_on(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_SELECTOR", "true")
    nodes = set(_graph().get_graph().nodes.keys())
    assert "evidence" in nodes


def test_graph_without_evidence_node_by_default(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_SELECTOR", "false")
    nodes = set(_graph().get_graph().nodes.keys())
    assert "evidence" not in nodes


# ---------------------------------------------------------------------- #
# Conditional edge
# ---------------------------------------------------------------------- #


def test_route_after_verify_retries_when_low_groundedness():
    state = initial_state("q")
    state.update({"groundedness": 0.3, "retry_count": 0})
    assert route_after_verify(state) == "expand_query"


def test_route_after_verify_finalizes_when_grounded():
    state = initial_state("q")
    state.update({"groundedness": 0.95, "retry_count": 0})
    assert route_after_verify(state) == "finalize"


def test_route_after_verify_stops_at_max_retries():
    state = initial_state("q")
    state.update({"groundedness": 0.3, "retry_count": 2})
    assert route_after_verify(state) == "finalize"


def test_route_after_verify_threshold_boundary():
    state = initial_state("q")
    # Exactly at threshold → finalize (strictly-less-than guard).
    state.update({"groundedness": 0.7, "retry_count": 0})
    assert route_after_verify(state) == "finalize"
    state.update({"groundedness": 0.699, "retry_count": 0})
    assert route_after_verify(state) == "expand_query"


# ---------------------------------------------------------------------- #
# End-to-end flow
# ---------------------------------------------------------------------- #


def test_agent_flow_grounded_query(monkeypatch):
    _patch_pipeline(monkeypatch, groundedness=0.9)
    result = run_agent(initial_state("penalty for selling substandard food"))
    assert result["answer"] == "Section 50 prescribes the penalty."
    assert result["pipeline"] == "agent"
    assert result["agent"]["retry_count"] == 0
    assert result["agent"]["expanded_query"] is None
    # One full pass: classify → retrieve → generate → verify → finalize.
    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    assert nodes_run == ["classify", "retrieve", "generate"]


def test_agent_flow_retries_then_succeeds(monkeypatch):
    """Low groundedness on the first pass, high on the retry."""
    import app.rag.tasks as tasks

    calls = {"n": 0}

    def fake_gen(query, **kw):
        calls["n"] += 1
        g = 0.4 if calls["n"] == 1 else 0.85
        return {
            "answer": f"answer {calls['n']}",
            "groundedness_score": g,
            "hallucination_detected": g < 0.5,
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

    result = run_agent(initial_state("penalty for selling substandard food"))
    assert result["agent"]["retry_count"] == 1
    assert result["agent"]["expanded_query"]  # stub LLM rewrote the query
    assert result["answer"] == "answer 2"
    assert result["agent"]["groundedness"] == 0.85
    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    # First pass + expand_query + second retrieve + second generate.
    assert nodes_run.count("generate") == 2
    assert nodes_run.count("expand_query") == 1
    assert nodes_run.index("expand_query") > nodes_run.index("generate")


def test_agent_flow_exhausts_retries(monkeypatch):
    """Persistently low groundedness → stops after max_retries."""
    import app.rag.tasks as tasks

    calls = {"n": 0}

    def fake_gen(query, **kw):
        calls["n"] += 1
        return {
            "answer": f"weak answer {calls['n']}",
            "groundedness_score": 0.2,
            "hallucination_detected": True,
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

    result = run_agent(initial_state("penalty for selling substandard food"))
    # Default max_retries=2 → up to 3 generation passes (initial + 2 retries).
    assert calls["n"] == 3
    assert result["agent"]["retry_count"] == 2
    assert result["agent"]["groundedness"] == 0.2
    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    assert nodes_run.count("expand_query") == 2
    assert nodes_run.count("generate") == 3


def test_threshold_constant_shared():
    assert GROUNDEDNESS_THRESHOLD == NODE_THRESHOLD == 0.7
