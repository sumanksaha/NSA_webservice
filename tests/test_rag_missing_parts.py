"""Integration: plan_node → kg_reason_node → targeted_retry flow + Step-5 units."""

from app.rag.agent.nodes import kg_reason_node, plan_node, targeted_retry_node
from app.rag.planning.confidence_controller import evidence_confidence
from app.rag.planning.kg_reasoner import generate_cypher, reason_from_query
from app.rag.planning.profiles import ProfileManager
from app.rag.retrieval.temporal_validity import extract_amendment_chain, resolve_temporal_state


def test_plan_kg_retry_flow():
    state = {"query": "What is Section 12 penalty?", "query_type": "penalty"}
    planned = plan_node(state)
    assert planned["query_plan"]["total_tasks"] >= 1

    kg_out = kg_reason_node({**state, **planned})
    assert "kg_paths" in kg_out  # empty list OK when KG unconfigured

    retry_state = {
        **state,
        **planned,
        "groundedness": 0.2,
        "evidence_coverage": 0.2,
        "missing_citations": ["x"],
        "kg_traversal_failed": True,
    }
    out = targeted_retry_node(retry_state)
    assert out.get("targeted_query")
    # First failure is citation-based → identifier route; KG path exercised directly.
    from app.rag.planning.targeted_retry import TargetedRetryPlanner

    kg_q = TargetedRetryPlanner().target_query("What is Section 12?", ["kg_traversal_failed"], "general", {})
    assert "HAS_CROSS_REFERENCES" in kg_q


def test_kg_cypher_allowlist():
    assert generate_cypher("penalty", {"section": "12"}) is not None
    assert "HAS_PENALTY" in generate_cypher("penalty", {"section": "12"})
    assert generate_cypher("unknown_intent") is None
    # Injection chars stripped; only allowlisted relations appear.
    q = generate_cypher("permission", {"section": "12; DROP TABLE"})
    assert q is not None and ";" not in q
    assert all(rel in q for rel in ("HAS_AUTHORITY", "GRANTS_POWER_TO"))


def test_reason_from_query_no_sections():
    assert reason_from_query("hello world") == []


def test_per_requirement_rerank_profiles():
    pm = ProfileManager()
    std = pm.get_query_profile("standard")
    assert std.rerank_weights_for("definition")["legal_identity"] >= 0.5
    assert std.rerank_weights_for("unknown_type") == std.rerank_weights


def test_confidence_controller():
    assert evidence_confidence(0.9, 0.8, True, False, 0.9) == "HIGH"
    assert evidence_confidence(0.5, 0.5, True, False, 0.5) == "MEDIUM"
    assert evidence_confidence(0.1, 0.1, True, False, 0.1) == "LOW"
    assert evidence_confidence(0.9, 0.9, True, True, 0.9) == "MEDIUM"


def test_amendment_chain():
    chain = extract_amendment_chain("Section 12 was amended by Food Safety Amendment Act 2020.")
    assert chain and chain[0].kind == "amended"
    assert resolve_temporal_state("FSSA::12", "2025-01-01", chain, "valid") == "valid"
    repealed = extract_amendment_chain("Section 99 repealed by Repealing Act 2021.")
    assert resolve_temporal_state("FSSA::99", "2025-01-01", repealed, "valid") == "invalid"
