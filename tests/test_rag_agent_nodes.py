"""Tests for the LangGraph agent nodes (M3, plan §6).

All pipeline calls are monkeypatched — no Qdrant, no network, no torch.
The stub-LLM env is pinned by the autouse ``_rag_stub_llm_env`` fixture in
``tests/conftest.py`` so ``expand_query_node`` (which reuses
``GroundedLLMClient``) stays offline and deterministic.
"""

from __future__ import annotations

import pytest

from app.rag.agent.nodes import (
    GROUNDEDNESS_THRESHOLD,
    classify_node,
    evidence_node,
    expand_query_node,
    finalize_node,
    generate_node,
    retrieve_node,
    verify_node,
)
from app.rag.agent.state import initial_state


def _make_state(**overrides):
    state = initial_state("penalty for selling substandard food")
    state.update(overrides)
    return state


# ---------------------------------------------------------------------- #
# classify_node
# ---------------------------------------------------------------------- #


def test_classify_node_sets_query_type(monkeypatch):
    from app.rag.retrieval import QueryClassifier

    class FakeClassifier:
        def classify(self, query):
            return type("QT", (), {"value": "offence"})()

    monkeypatch.setattr(QueryClassifier, "classify", FakeClassifier().classify)
    out = classify_node(_make_state())
    assert out["query_type"] == "offence"
    assert out["audit_trail"][-1]["node"] == "classify"


def test_classify_node_falls_back_to_general(monkeypatch):
    from app.rag.retrieval import QueryClassifier

    def boom(self, query):
        raise RuntimeError("no classifier")

    monkeypatch.setattr(QueryClassifier, "classify", boom)
    out = classify_node(_make_state())
    assert out["query_type"] == "general"
    assert out["audit_trail"][-1]["detail"]["fallback"] is True


# ---------------------------------------------------------------------- #
# retrieve_node
# ---------------------------------------------------------------------- #


def test_retrieve_node_calls_pipeline_and_records(monkeypatch):
    import app.rag.tasks as tasks

    fake_chunks = [{"chunk_id": "c1", "score": 0.9, "text": "Sec 50"}]
    captured = {}

    def fake_run(query, **kwargs):
        captured["query"] = query
        return {
            "chunks": fake_chunks,
            "query_type": "offence",
            "retrieval_latency_ms": 42,
            "log_id": "log-1",
        }

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", fake_run)
    out = retrieve_node(_make_state())
    assert out["chunks"] == fake_chunks
    assert out["query_type"] == "offence"
    assert out["retrieval_latency_ms"] == 42
    assert out["log_id"] == "log-1"
    assert captured["query"] == "penalty for selling substandard food"
    assert out["audit_trail"][-1]["detail"]["chunk_count"] == 1


def test_retrieve_node_uses_expanded_query(monkeypatch):
    import app.rag.tasks as tasks

    captured = {}

    def fake_run(query, **kwargs):
        captured["query"] = query
        return {"chunks": [], "query_type": "offence", "retrieval_latency_ms": 0, "log_id": None}

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", fake_run)
    retrieve_node(_make_state(expanded_query="penalty under section 50 FSS Act"))
    assert captured["query"] == "penalty under section 50 FSS Act"


def test_retrieve_node_keeps_query_type_when_empty(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {"chunks": [], "query_type": "", "retrieval_latency_ms": 0, "log_id": None},
    )
    out = retrieve_node(_make_state(query_type="prohibition"))
    assert out["query_type"] == "prohibition"


# ---------------------------------------------------------------------- #
# evidence_node
# ---------------------------------------------------------------------- #


def test_evidence_node_skipped_when_flag_off(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_SELECTOR", "false")
    out = evidence_node(_make_state(chunks=[{"chunk_id": "c1"}]))
    assert out["evidence_set"] is None
    assert out["audit_trail"][-1]["detail"]["evidence_set"] is False


def test_evidence_node_runs_when_flag_on(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_SELECTOR", "true")
    import app.rag.retrieval.evidence_selector as es_mod

    class FakeEvidenceSet:
        def to_dict(self):
            return {"items": [{"evidence_type": "statute"}]}

    def fake_select(query, chunks, **kwargs):
        return FakeEvidenceSet()

    monkeypatch.setattr(es_mod, "select_evidence_set", fake_select)
    out = evidence_node(_make_state(chunks=[{"chunk_id": "c1"}]))
    assert out["evidence_set"] == {"items": [{"evidence_type": "statute"}]}
    assert out["audit_trail"][-1]["detail"]["evidence_set"] is True


def test_evidence_node_degrades_on_error(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_SELECTOR", "true")
    import app.rag.retrieval.evidence_selector as es_mod

    def boom(*args, **kwargs):
        raise RuntimeError("selector down")

    monkeypatch.setattr(es_mod, "select_evidence_set", boom)
    out = evidence_node(_make_state(chunks=[{"chunk_id": "c1"}]))
    assert out["evidence_set"] is None


# ---------------------------------------------------------------------- #
# generate_node
# ---------------------------------------------------------------------- #


def test_generate_node_calls_pipeline(monkeypatch):
    import app.rag.tasks as tasks

    fake_response = {
        "answer": "Section 50 prescribes the penalty.",
        "groundedness_score": 0.82,
        "hallucination_detected": False,
        "query_type": "offence",
    }
    captured = {}

    def fake_run(query, **kwargs):
        captured["query"] = query
        captured["chunks"] = kwargs.get("chunks")
        return fake_response

    monkeypatch.setattr(tasks, "run_generation_pipeline", fake_run)
    chunks = [{"chunk_id": "c1"}]
    out = generate_node(_make_state(chunks=chunks, query_type="offence"))
    assert out["answer"] == "Section 50 prescribes the penalty."
    assert out["groundedness"] == 0.82
    assert out["hallucination_detected"] is False
    assert out["response"] == fake_response
    assert captured["chunks"] == chunks
    assert out["audit_trail"][-1]["node"] == "generate"


def test_generate_node_defaults_on_empty(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {},
    )
    out = generate_node(_make_state())
    assert out["answer"] == ""
    assert out["groundedness"] == 0.0
    assert out["hallucination_detected"] is False


# ---------------------------------------------------------------------- #
# verify_node
# ---------------------------------------------------------------------- #


def test_verify_node_passes_through():
    out = verify_node(_make_state(groundedness=0.95, hallucination_detected=False))
    assert out["groundedness"] == 0.95
    assert out["hallucination_detected"] is False


# ---------------------------------------------------------------------- #
# expand_query_node
# ---------------------------------------------------------------------- #


def test_expand_query_node_increments_retry_count():
    out = expand_query_node(_make_state(retry_count=1))
    assert out["retry_count"] == 2
    assert out["audit_trail"][-1]["node"] == "expand_query"
    assert out["audit_trail"][-1]["detail"]["retry"] == 2


def test_expand_query_node_stub_llm_returns_something():
    """Stub LLM returns a canned text — must not raise, query survives."""
    out = expand_query_node(_make_state())
    assert out["retry_count"] == 1
    # Stub returns the canned "Based on the provided context..." text.
    assert isinstance(out["expanded_query"], str) and out["expanded_query"]


def test_expand_query_node_keeps_query_on_llm_failure(monkeypatch):
    import app.rag.generation.llm_client as llm_mod

    class FailingClient:
        def call(self, *args, **kwargs):
            return type("R", (), {"success": False, "text": ""})()

    monkeypatch.setattr(llm_mod, "GroundedLLMClient", lambda *a, **k: FailingClient())
    out = expand_query_node(_make_state())
    assert out["expanded_query"] == "penalty for selling substandard food"
    assert out["retry_count"] == 1


# ---------------------------------------------------------------------- #
# finalize_node
# ---------------------------------------------------------------------- #


def test_finalize_node_merges_agent_metadata():
    state = _make_state(
        query_type="offence",
        chunks=[{"chunk_id": "c1"}],
        groundedness=0.9,
        hallucination_detected=False,
        retry_count=1,
        expanded_query="expanded q",
        response={"answer": "ans", "query_type": "offence"},
    )
    state["audit_trail"].append({"node": "classify", "latency_ms": 1, "detail": {}})
    out = finalize_node(state)
    resp = out["response"]
    assert resp["pipeline"] == "agent"
    assert resp["agent"]["retry_count"] == 1
    assert resp["agent"]["expanded_query"] == "expanded q"
    assert resp["agent"]["groundedness"] == 0.9
    assert len(resp["agent"]["audit_trail"]) == 1
    assert resp["retrieved_chunks"] == [{"chunk_id": "c1"}]


def test_finalize_node_fills_defaults_from_state():
    out = finalize_node(_make_state(query="q?", query_type="general"))
    resp = out["response"]
    assert resp["query"] == "q?"
    assert resp["query_type"] == "general"
    assert resp["retrieved_chunks"] == []
    assert resp["agent"]["retry_count"] == 0


# ---------------------------------------------------------------------- #
# Threshold sanity
# ---------------------------------------------------------------------- #


def test_groundedness_threshold_is_0_7():
    assert GROUNDEDNESS_THRESHOLD == 0.7
