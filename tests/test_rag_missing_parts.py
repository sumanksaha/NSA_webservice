"""Integration: plan_node → kg_reason_node → targeted_retry flow + Step-5 units."""

import re

from app.rag.agent.nodes import kg_reason_node, plan_node, targeted_retry_node
from app.rag.planning.kg_reasoner import ALLOWED_RELATIONS, generate_cypher, reason_from_query
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

    planner = TargetedRetryPlanner()

    # With KG paths in context the retry appends a "Section N" hint derived
    # from the traversal, so the query differs from the input.
    kg_plan = planner.target_plan(
        "What is Section 12?", ["kg_traversal_failed"], "general", {"kg_paths": [{"steps": ["12", "55"]}]}
    )
    assert kg_plan.arm == "kg_paths"
    assert kg_plan.query != "What is Section 12?"
    assert "Section 12" in kg_plan.query

    # With no KG targets the planner must abstain rather than silently echo
    # the query (SPEC-2): an explicit no-op arm, not a pretend retry.
    empty_plan = planner.target_query("What is Section 12?", ["kg_traversal_failed"], "general", {})
    assert empty_plan == "What is Section 12?"
    assert planner.target_plan("What is Section 12?", ["kg_traversal_failed"], "general", {}).arm == "none"


def test_kg_cypher_allowlist():
    # Only relationships the graph actually stores may appear — the old
    # HAS_PENALTY / HAS_AUTHORITY set matched nothing (see ALLOWED_RELATIONS).
    q = generate_cypher("penalty", {"section": "12"})
    assert q is not None
    assert any(rel in q for rel in ("PRESCRIBES_PENALTY", "CREATES_OFFENCE"))
    assert "HAS_PENALTY" not in q
    assert generate_cypher("unknown_intent") is None
    # Injection chars stripped; only allowlisted relations appear.
    q = generate_cypher("permission", {"section": "12; DROP TABLE"})
    assert q is not None and ";" not in q
    assert all(rel in q for rel in ("GRANTS_POWER_TO", "GRANTS_PERMISSION"))
    # Every relationship emitted by any template is in the allowlist.
    for intent in ("permission", "penalty", "prohibition", "definition"):
        emitted = generate_cypher(intent, {"section": "12"})
        if emitted:
            for rel in __import__("re").findall(r"-\[:([A-Z_]+)", emitted):
                assert rel in ALLOWED_RELATIONS, rel


def test_reason_from_query_no_sections():
    assert reason_from_query("hello world") == []


def test_per_requirement_rerank_profiles():
    pm = ProfileManager()
    std = pm.get_query_profile("standard")
    assert std.rerank_weights_for("definition")["legal_identity"] >= 0.5
    assert std.rerank_weights_for("unknown_type") == std.rerank_weights


def test_amendment_chain():
    chain = extract_amendment_chain("Section 12 was amended by Food Safety Amendment Act 2020.")
    assert chain and chain[0].kind == "amended"
    assert resolve_temporal_state("FSSA::12", "2025-01-01", chain, "valid") == "valid"
    repealed = extract_amendment_chain("Section 99 repealed by Repealing Act 2021.")
    assert resolve_temporal_state("FSSA::99", "2025-01-01", repealed, "valid") == "invalid"
