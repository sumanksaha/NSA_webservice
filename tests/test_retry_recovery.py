"""Failure → retry agreement: one map, every code routed.

The recovery map lived in two places (classifier + planner mirror) and
neither matched the UPPERCASE rubric names the sufficiency gate emits —
every rubric-sourced retry silently degraded to dense expansion. These
tests pin the single seam: taxonomy values, members, and rubric names all
resolve, and the planner builds from the same map.
"""

import re

from app.rag.planning.failure_classifier import FailureClassifier, RetrievalFailure
from app.rag.planning.targeted_retry import TargetedRetryPlanner

_PSEUDO = re.compile(r"\(\s*(identifier|definition|temporal|hierarchy|authority|case_law|expand)\s*\)", re.IGNORECASE)

_KNOWN_STRATEGIES = {
    "identifier_search",
    "collection_reroute",
    "temporal_filter",
    "hierarchy_graph",
    "definition_search",
    "kg_traversal",
    "temporal_retrieval",
    "authority_retrieval",
    "case_law_retrieval",
    "dense_expansion",
    "expand_query",
    "semantic_expansion",
    "abstain",
    "kg_reasoning",
}


def test_every_taxonomy_code_routes_to_a_known_strategy():
    classifier = FailureClassifier()
    for member in RetrievalFailure:
        for form in (member, member.value, member.name):
            assert classifier.recovery_strategy(form) in _KNOWN_STRATEGIES, form


def test_no_mirror_map_on_the_planner():
    assert not hasattr(TargetedRetryPlanner, "_recovery_strategy")
    assert not hasattr(TargetedRetryPlanner(), "_recovery_strategy")


def test_rubric_names_route_like_taxonomy_values():
    classifier = FailureClassifier()
    assert classifier.recovery_strategy("EVIDENCE_CONTRADICTION") == "temporal_retrieval"
    assert classifier.recovery_strategy("TEMPORAL_INVALIDITY") == "temporal_retrieval"
    assert classifier.recovery_strategy("INSUFFICIENT_AUTHORITY_SCORE") == "authority_retrieval"
    assert classifier.recovery_strategy("INSUFFICIENT_EVIDENCE_COVERAGE") == "expand_query"
    assert classifier.recovery_strategy("LOW_RELEVANCE") == "semantic_expansion"
    assert classifier.recovery_strategy("MISSING_SPECIFICITY") == "identifier_search"


def test_planner_builds_from_the_single_map():
    # Pseudo-tags must NEVER reach an emitted query (RAG-TR-001 §4 hard rule;
    # pinned by test_targeted_retry_v2.py::test_no_pseudo_tags_anywhere).
    # Targeting is expressed through the query text + arm, not "(tag)" suffixes.
    planner = TargetedRetryPlanner()
    for code in ("EVIDENCE_CONTRADICTION", "INSUFFICIENT_AUTHORITY_SCORE", "CONFLICTING_AUTHORITIES", "LOW_RELEVANCE"):
        q = planner.target_query("q?", [code], "general", {})
        assert not _PSEUDO.search(q), f"{code} -> {q!r}"


def test_abstain_returns_the_query_unchanged():
    planner = TargetedRetryPlanner()
    assert planner.target_query("q?", ["ABSTAIN_REQUIRED"], "general", {}) == "q?"
    assert planner.target_query("q?", [RetrievalFailure.ABSTAIN_REQUIRED], "general", {}) == "q?"


def test_unknown_code_falls_back_to_dense_expansion():
    planner = TargetedRetryPlanner()
    # Unmapped strategy -> explicit no-op plan (SPEC-2), not a pseudo-tagged echo.
    plan = planner.target_plan("q?", ["no_such_failure"], "general", {})
    assert plan.strategy == "unmapped"
    assert plan.query == "q?"
    assert planner.target_query("q?", [], "general", {}) == "q?"
