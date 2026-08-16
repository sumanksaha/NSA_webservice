"""Tests for the LangGraph agent state schema (M3, plan §6)."""

from __future__ import annotations

from app.rag.agent.state import RAGState, initial_state


def test_initial_state_defaults():
    state = initial_state("penalty for selling substandard food")
    assert state["query"] == "penalty for selling substandard food"
    assert state["top_k"] == 10
    assert state["query_type"] == ""
    assert state["chunks"] == []
    assert state["retry_count"] == 0
    assert state["max_retries"] == 2
    assert state["groundedness"] == 0.0
    assert state["expanded_query"] is None
    assert state["audit_trail"] == []
    assert state["response"] == {}


def test_initial_state_custom_args():
    state = initial_state(
        "who appoints the FSO",
        top_k=5,
        collection_name="criminal_legal_768",
        filters={"act_name": "BNS"},
    )
    assert state["top_k"] == 5
    assert state["collection_name"] == "criminal_legal_768"
    assert state["filters"] == {"act_name": "BNS"}


def test_initial_state_max_retries_override():
    assert initial_state("q", max_retries=4)["max_retries"] == 4
    # Default stays 2 (plan §5.3 guard: retry_count < 2).
    assert initial_state("q")["max_retries"] == 2


def test_state_is_a_typed_dict():
    """RAGState must be a TypedDict so LangGraph StateGraph accepts it."""
    assert hasattr(RAGState, "__annotations__")
    keys = set(RAGState.__annotations__.keys())
    for required in (
        "query",
        "query_type",
        "chunks",
        "retry_count",
        "audit_trail",
        "groundedness",
        "response",
        "log_id",
    ):
        assert required in keys


def test_state_is_json_serializable():
    """State must survive json round-trip (M5 checkpointing readiness)."""
    import json

    state = initial_state("prohibition on sale of adulterated food")
    state["chunks"] = [
        {
            "chunk_id": "c1",
            "score": 0.9,
            "text": "Section 50: General penalty.",
            "section_number": "50",
            "document_title": "FSS Act",
            "act_name": "FSS Act",
        }
    ]
    state["audit_trail"].append({"node": "classify", "latency_ms": 1, "detail": {}})
    round_tripped = json.loads(json.dumps(state))
    assert round_tripped["chunks"][0]["chunk_id"] == "c1"
    assert round_tripped["audit_trail"][0]["node"] == "classify"
