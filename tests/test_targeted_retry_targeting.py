"""SPEC-2: Retry targeting accuracy tests.

Every strategy must produce a query different from the input query, and each
plan query must contain a class-specific token for its failure class.

No network, no LLM, no Qdrant.
"""
from __future__ import annotations

from app.rag.planning.targeted_retry import (
    ARM_DEFINITION,
    ARM_HIERARCHY,
    ARM_KG_PATHS,
    ARM_NONE,
    ARM_SPARSE_IDENTIFIER,
    TargetedRetryPlanner,
)


class TestStrategyProducesDifferentQuery:
    """Every strategy produces a query different from the input query."""

    def test_identifier_search_produces_different_query(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "punishment under Section 31 of the FSS Act 2006",
            ["missing_provision"],
            "penalty",
            {"top_k": 10},
        )
        assert plan.arm == ARM_SPARSE_IDENTIFIER
        assert plan.query != "punishment under Section 31 of the FSS Act 2006"

    def test_identifier_undetectable_degrades_to_abstain(self):
        """No identifier token recoverable -> explicit abstain, never a silent echo."""
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "what is food safety",
            ["missing_provision"],
            "general",
            {},
        )
        assert plan.arm == ARM_NONE
        assert plan.meta.get("degraded") == "none"

    def test_collection_reroute_produces_different_query(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "wrong provision in the FSS Act",
            ["wrong_provision"],
            "general",
            {"top_k": 10, "chunks": [{"document_title": "FSS Act", "text": "Section 31"}]},
        )
        assert plan.arm == ARM_SPARSE_IDENTIFIER
        assert plan.query != "wrong provision in the FSS Act"
        assert "FSS" in plan.query or "Section" in plan.query

    def test_definition_search_produces_different_query(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "what does food business mean",
            ["missing_definition"],
            "definition",
            {"answer": "'food business' means any undertaking", "chunks": []},
        )
        assert plan.arm == ARM_DEFINITION
        assert plan.query != "what does food business mean"
        assert "means" in plan.query or "definition" in plan.query.lower()

    def test_definition_unminable_degrades_to_abstain(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan("meaning?", ["missing_definition"], "definition", {})
        assert plan.arm == ARM_NONE
        assert plan.meta.get("degraded") == "no_defined_term"

    def test_hierarchy_graph_produces_different_query(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "Section 16 of the FSS Act exceptions",
            ["missing_exception"],
            "general",
            {"chunks": []},
        )
        assert plan.arm == ARM_HIERARCHY
        assert plan.query != "Section 16 of the FSS Act exceptions"
        assert "notwithstanding" in plan.query or "exception" in plan.query.lower()

    def test_kg_traversal_produces_different_query(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "cross reference question",
            ["missing_cross_ref"],
            "general",
            {"kg_paths": [{"steps": ["Section 31"]}]},
        )
        assert plan.arm == ARM_KG_PATHS
        assert plan.query != "cross reference question"
        assert "Section" in plan.query

    def test_abstain_returns_same_query(self):
        """Abstain is the one strategy that may echo the query (arm=none)."""
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "some question",
            ["abstain_required"],
            "general",
            {},
        )
        assert plan.arm == ARM_NONE
        assert plan.query == "some question"

    def test_unknown_strategy_produces_unmapped_plan(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "some question",
            ["no_such_failure_code"],
            "general",
            {},
        )
        assert plan.strategy == "unmapped"
        assert plan.arm == ARM_NONE
        assert plan.query == "some question"  # no-op plan echoes query
        assert plan.meta.get("unmapped_strategy") == "dense_expansion"


class TestClassSpecificTokens:
    """Each plan query contains a class-specific token for its failure class."""

    def test_identifier_has_section_token(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "punishment under Section 31 of the FSS Act 2006",
            ["missing_provision"],
            "penalty",
            {"top_k": 10},
        )
        assert "Section" in plan.query or "section" in plan.query.lower()

    def test_collection_has_collection_token(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "wrong provision in the FSS Act",
            ["wrong_provision"],
            "general",
            {"top_k": 10, "chunks": [{"document_title": "FSS Act", "text": "Section 31"}]},
        )
        # Collection query should contain act or section tokens
        assert "FSS" in plan.query or "Section" in plan.query

    def test_definition_has_definition_token(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "what does food business mean",
            ["missing_definition"],
            "definition",
            {"answer": "'food business' means any undertaking", "chunks": []},
        )
        assert "means" in plan.query or "definition" in plan.query.lower()

    def test_hierarchy_has_hierarchy_token(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "Section 16 of the FSS Act exceptions",
            ["missing_exception"],
            "general",
            {"chunks": []},
        )
        assert "notwithstanding" in plan.query or "exception" in plan.query.lower() or "Section" in plan.query

    def test_kg_has_kg_token(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "cross reference question",
            ["missing_cross_ref"],
            "general",
            {"kg_paths": [{"steps": ["Section 31"]}]},
        )
        assert "Section" in plan.query


class TestPlanInvariant:
    """Plan-time invariant: non-empty failures must produce a different query."""

    def test_no_failures_may_echo_query(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan("some question", [], "general", {})
        assert plan.query == "some question"
        assert plan.failures == ()

    def test_non_empty_failures_never_silently_echo(self):
        """No non-empty-failure plan may silently echo the input query.

        An echo is only permitted when the plan declares itself an explicit
        no-op: abstain (arm=none) or unmapped (strategy="unmapped").
        """
        planner = TargetedRetryPlanner()
        test_cases = [
            ("punishment under Section 31", ["missing_provision"], "penalty", {"top_k": 10}),
            ("what does food business mean", ["missing_definition"], "definition", {"answer": "'food business' means X"}),
            ("Section 16 exceptions", ["missing_exception"], "general", {"chunks": []}),
            ("cross ref question", ["missing_cross_ref"], "general", {"kg_paths": [{"steps": ["Section 31"]}]}),
            ("wrong provision", ["wrong_provision"], "general", {"chunks": [{"document_title": "FSS Act"}]}),
            ("totally generic question", ["missing_provision"], "general", {}),
            ("unmapped code question", ["no_such_failure_code"], "general", {}),
        ]
        for query, failures, qtype, ctx in test_cases:
            plan = planner.target_plan(query, failures, qtype, ctx)
            if not plan.failures:
                continue
            if plan.query == query:
                # Echo is allowed only for declared no-ops.
                assert plan.arm == ARM_NONE, (
                    f"failures={failures} plan silently echoed with arm={plan.arm!r}"
                )
                assert (
                    plan.strategy == "unmapped"
                    or plan.strategy == "abstain"
                    or "degraded" in (plan.meta or {})
                ), f"failures={failures} echo must be declared via arm/strategy/meta, got {plan.to_dict()}"

    def test_abstain_is_exception_to_invariant(self):
        """Abstain may echo the query because it's a no-op (arm=none)."""
        planner = TargetedRetryPlanner()
        plan = planner.target_plan("some question", ["abstain_required"], "general", {})
        assert plan.failures == ("abstain_required",)
        assert plan.query == "some question"  # abstain echoes by design
        assert plan.arm == ARM_NONE


class TestLegacyPathIsFlagGated:
    """The legacy path's silent echo is live only while the flag is OFF.

    ``RAG_TARGETED_RETRY_V2`` selects the retry implementation:
    ``linear.py`` calls ``target_plan`` when it is on and
    ``_legacy_target_query`` when it is off. The legacy path keeps its own
    dispatch and echoes the query for strategies it does not handle.

    Collapsing the two was tried and reverted (it changes served behaviour with
    no adoption gate). Instead the flag was enabled, so production now takes
    the fixed path and the divergence is dormant. These tests pin which side of
    that switch we are on, so turning the flag off cannot silently reintroduce
    the SPEC-2 defect.
    """

    def test_flag_is_enabled_so_production_uses_the_fixed_path(self):
        from app.shared.config import cfg

        assert bool(cfg.targeted_retry_v2) is True, (
            "RAG_TARGETED_RETRY_V2 is off: production falls back to "
            "_legacy_target_query, which silently echoes the query for "
            "collection_reroute / temporal_retrieval / authority_retrieval / "
            "kg_* (the SPEC-2 defect). Either collapse the two paths "
            "deliberately, or leave the flag on."
        )

    def test_legacy_path_would_still_echo_if_the_flag_were_off(self):
        """Documents the dormant defect so a flag flip is never a surprise."""
        planner = TargetedRetryPlanner()
        # temporal_retrieval is dispatched by neither the legacy path nor...
        out = planner._legacy_target_query("q?", ["EVIDENCE_CONTRADICTION"], "general", {})
        assert out == "q?", (
            "legacy path echo changed; re-evaluate whether the two paths can "
            "now be collapsed"
        )

    def test_fixed_path_does_not_echo_for_that_same_input(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan("q?", ["EVIDENCE_CONTRADICTION"], "general", {})
        assert plan.arm == ARM_NONE, "flag-on path must abstain, not echo"
        assert plan.strategy in ("temporal_retrieval", "unmapped", "abstain")


class TestCollectionReroute:
    """Collection_reroute must produce a real collection-targeted query."""

    def test_collection_reroute_with_act_and_section(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "wrong provision in the FSS Act Section 31",
            ["wrong_provision"],
            "general",
            {"top_k": 10, "chunks": [{"document_title": "FSS Act", "text": "Section 31"}]},
        )
        assert plan.strategy == "collection_reroute"
        assert plan.arm == ARM_SPARSE_IDENTIFIER
        assert plan.query != "wrong provision in the FSS Act Section 31"
        assert "FSS" in plan.query or "Section" in plan.query

    def test_collection_reroute_without_targeting_tokens_abstains(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "some generic question",
            ["wrong_provision"],
            "general",
            {"top_k": 10, "chunks": []},
        )
        # No act, no section, no collection terms -> abstain
        assert plan.arm == ARM_NONE
        assert plan.strategy == "collection_reroute"
        assert plan.query == "some generic question"

    def test_collection_reroute_has_meta(self):
        planner = TargetedRetryPlanner()
        plan = planner.target_plan(
            "wrong provision in the FSS Act",
            ["wrong_provision"],
            "general",
            {"top_k": 10, "chunks": [{"document_title": "FSS Act", "text": "Section 31"}]},
        )
        assert "act" in plan.meta or "section" in plan.meta or "collection_terms" in plan.meta
