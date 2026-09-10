"""Tests for budget-aware routing economics (Phase 3, item 18).

Pure-function tests over the routing module — no graph, no retrieval, no LLM.
"""

from __future__ import annotations

from app.rag.agent.routing_economics import (
    BUDGET_TIERS,
    apply_budget_tier,
    is_exhausted,
    route_strategy,
)

# ---------------------------------------------------------------------- #
# route_strategy
# ---------------------------------------------------------------------- #


def test_multi_hop_plan_routes_dag():
    d = route_strategy({"complexity": "multi_hop"}, "general", "if a licence is revoked then what applies")
    assert d["strategy"] == "decomposition"
    assert d["tier"] == "deep"
    assert d["pinned"] is False


def test_multi_part_plan_routes_dag_at_moderate_tier():
    d = route_strategy({"complexity": "multi_part"}, "general", "penalty and licensing exceptions")
    assert d["strategy"] == "decomposition"
    assert d["tier"] == "moderate"


def test_simple_plan_routes_direct():
    d = route_strategy({"complexity": "simple"}, "general", "what is food adulteration")
    assert d["strategy"] == "direct"
    assert d["tier"] == "direct"


def test_direct_override_single_identifier_lookup():
    # The planner labels "What is Section 12?" MULTI_PART (one section ref),
    # but the DIRECT override keeps it off the decomposition machinery.
    d = route_strategy({"complexity": "multi_part"}, "general", "What is Section 12?")
    assert d["strategy"] == "direct"


def test_direct_override_requires_no_conjunction():
    d = route_strategy({"complexity": "multi_part"}, "general", "penalty under section 12 and exceptions")
    assert d["strategy"] == "decomposition"


def test_cross_reference_query_type_routes_multi_hop():
    d = route_strategy({"complexity": "simple"}, "cross_reference", "which rule does section 12 refer to")
    assert d["strategy"] == "multi_hop"
    assert d["tier"] == "deep"


def test_retry_pins_prior_decision():
    prior = {
        "strategy": "decomposition",
        "complexity": "multi_part",
        "query_type": "general",
        "tier": "moderate",
    }
    d = route_strategy({"complexity": "simple"}, "general", "now simple?", retry_count=1, prior_decision=prior)
    assert d["strategy"] == "decomposition"
    assert d["pinned"] is True


def test_no_pin_on_first_plan():
    d = route_strategy(
        {"complexity": "simple"},
        "general",
        "x",
        retry_count=0,
        prior_decision={"strategy": "decomposition"},
    )
    assert d["strategy"] == "direct"
    assert d["pinned"] is False


# ---------------------------------------------------------------------- #
# BUDGET_TIERS / apply_budget_tier
# ---------------------------------------------------------------------- #


def test_tier_table_shape():
    for caps in BUDGET_TIERS.values():
        assert set(caps) == {"max_tasks", "max_retrieval_rounds", "max_documents", "max_llm_calls"}
    assert BUDGET_TIERS["direct"]["max_tasks"] == 0  # no DAG work on the direct tier
    assert BUDGET_TIERS["deep"]["max_llm_calls"] > BUDGET_TIERS["direct"]["max_llm_calls"]


def test_fresh_budget_set_to_tier():
    b = apply_budget_tier({"max_tasks": 10, "consumed_tasks": 0}, "direct")
    assert b["max_tasks"] == 0
    assert b["max_llm_calls"] == BUDGET_TIERS["direct"]["max_llm_calls"]


def test_shrink_only_never_raises_explicit_caps():
    b = apply_budget_tier({"max_llm_calls": 2, "max_tasks": 0}, "deep")
    assert b["max_llm_calls"] == 2  # operator-supplied smaller cap preserved


def test_consumed_counters_preserved():
    b = apply_budget_tier({"consumed_llm_calls": 3, "consumed_retrieval_rounds": 2}, "moderate")
    assert b["consumed_llm_calls"] == 3
    assert b["consumed_retrieval_rounds"] == 2


def test_unknown_tier_falls_back_to_ceiling():
    b = apply_budget_tier(None, "bogus")
    assert b["max_llm_calls"] == BUDGET_TIERS["deep"]["max_llm_calls"]


# ---------------------------------------------------------------------- #
# is_exhausted
# ---------------------------------------------------------------------- #


def test_rounds_cap_trips():
    assert is_exhausted({"consumed_retrieval_rounds": 3, "max_retrieval_rounds": 3})


def test_llm_cap_trips():
    assert is_exhausted({"consumed_llm_calls": 4, "max_llm_calls": 4})


def test_under_cap_not_exhausted():
    assert not is_exhausted({"consumed_retrieval_rounds": 2, "max_retrieval_rounds": 3})


def test_zero_task_cap_ignored_for_linear_path():
    b = {"consumed_tasks": 0, "max_tasks": 0, "consumed_retrieval_rounds": 0, "max_retrieval_rounds": 3}
    assert not is_exhausted(b, include_tasks=False)  # linear path cannot spend tasks
    assert is_exhausted(b)  # DAG path: zero capacity is genuinely spent


def test_missing_budget_not_exhausted():
    assert not is_exhausted(None)
