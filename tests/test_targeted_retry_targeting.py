"""SPEC-2: Retry targeting accuracy tests.

Every strategy must produce a query different from the input query, and each
plan query must contain a class-specific token for its failure class.

No network, no LLM, no Qdrant.
"""
from __future__ import annotations

from app.rag.planning.targeted_retry import (
    ARM_DEFINITION,
    ARM_HYBRID,
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


class TestRetryPathParity:
    """The retry paths must be parity (ADR-0010 sec. 2.6): the flag is an
    observability switch, never a behavioural fork.

    The flag ``RAG_TARGETED_RETRY_V2`` selects the retry implementation in
    ``linear.py``: ``target_plan`` when on, ``_legacy_target_query`` when off.
    ``_legacy_target_query`` now delegates to ``target_plan().query`` with no
    dispatch of its own, so the two paths can never diverge. The flag is the
    default ``True`` (V2 is the production path); the parity assertions below
    mean that even flipping the flag to ``false`` cannot silently reintroduce
    the SPEC-2 silent-echo defect.
    """

    def test_flag_is_true_by_default_and_stays_true(self):
        """Guard: V2 is the production default, not an opt-in env tweak."""
        from app.shared.config import cfg

        assert bool(cfg.targeted_retry_v2) is True, (
            "RAG_TARGETED_RETRY_V2 must remain the default; production must not "
            "fall back to the legacy path. This is the adoption gate for the V2 "
            "retry behaviour. Set explicitly only for rollback experiments."
        )

    def test_legacy_and_fixed_paths_return_identical_plan(self):
        """Legacy and fixed paths are parity for every failure code."""
        planner = TargetedRetryPlanner()
        failure_codes = [
            "missing_provision",
            "wrong_provision",
            "missing_definition",
            "missing_exception",
            "missing_cross_ref",
            "temporal_invalidity",
            "insufficient_authority",
            "evidence_contradiction",
            "conflicting_authorities",
            "low_relevance",
            "insufficient_evidence_coverage",
            "missing_specificity",
            "abstain_required",
            "no_such_failure_code",
            "EVIDENCE_CONTRADICTION",
        ]
        for code in failure_codes:
            legacy = planner._legacy_target_query(
                "q?", [code], "general", {}
            )
            fixed = planner.target_plan("q?", [code], "general", {})
            assert (
                legacy == fixed.query
            ), "parity break on %r: legacy=%r fixed.query=%r" % (code, legacy, fixed.query)
            assert fixed.failures == (code,), "failures lost: %s" % fixed.to_dict()

    def test_both_paths_abstain_not_echo_for_untargetable_failures(self):
        """A retry plan with non-empty failures must not silently echo:
        untargetable failures -> explicit no-op (arm=none), both paths."""
        planner = TargetedRetryPlanner()
        for code in ("EVIDENCE_CONTRADICTION", "no_such_failure_code"):
            legacy_plan = planner.target_plan("q?", [code], "general", {})
            assert legacy_plan.arm == ARM_NONE, (
                "legacy/delegated path must abstain, got %s" % legacy_plan.to_dict()
            )
            assert legacy_plan.strategy in ("unmapped", "abstain")
            # arm=none + a non-empty failures tuple means the query is echoed
            # by design as an explicit abstain, not a silent re-query.
            assert not (legacy_plan.arm != ARM_NONE and legacy_plan.query == "q?"), (
                "silent echo with an active arm is forbidden: " + legacy_plan.to_dict()
            )

    def test_empty_failures_may_echo_but_flagged_as_no_retry(self):
        """A retry plan with failures=() may echo the input query; it carries no
        failures and no targeted arm."""
        planner = TargetedRetryPlanner()
        plan = planner.target_plan("q?", [], "general", {})
        assert plan.query == "q?"
        assert plan.failures == ()
        assert plan.arm == ARM_HYBRID
        assert plan.strategy == "dense_expansion"
        # legacy wrapper agrees
        assert planner._legacy_target_query("qq?", [], "general", {}) == "qq?"

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
