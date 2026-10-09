"""RAG-TR-001 section 8 tests: TargetPlan builders, node wiring, shadow scorer.

No network, no LLM, no Qdrant.
"""
from __future__ import annotations

import re

from app.rag.planning.targeted_retry import TargetedRetryPlanner, TargetPlan

_PSEUDO = re.compile(r"\(\s*(identifier|definition|temporal|hierarchy|authority|case_law|expand)\s*\)", re.IGNORECASE)


def _all_queries(planner: TargetedRetryPlanner) -> list[str]:
    out = []
    out.append(planner.target_plan("punishment under Section 31 of the FSS Act 2006", ["missing_provision"], "penalty", {"top_k": 10}).query)
    out.append(planner.target_plan("what does foo mean", ["missing_definition"], "definition", {"answer": "'foo' means a bar", "chunks": []}).query)
    out.append(planner.target_plan("Section 16 of the FSS Act exceptions", ["missing_exception"], "general", {"chunks": []}).query)
    out.append(planner.target_plan("cross ref question", ["missing_cross_ref"], "general", {"kg_paths": [{"steps": ["Section 31"]}]}).query)
    out.append(planner.target_plan("some temporal question", ["temporal_invalidity"], "general", {}).query)
    out.append(planner.target_plan("authority question", ["insufficient_authority"], "general", {}).query)
    return out


class TestBuilders:
    def test_identifier_lexical_and_topk(self):
        plan = TargetedRetryPlanner().target_plan(
            "punishment under Section 31 of the FSS Act 2006", ["missing_provision"], "penalty", {"top_k": 10})
        assert plan.arm == "sparse_identifier"
        assert "Section 31" in plan.query or "section 31" in plan.query
        assert plan.top_k_override == 20
        assert not _PSEUDO.search(plan.query)

    def test_identifier_undetectable_degrades(self):
        # SPEC-2: no recoverable identifier token -> explicit abstain, not a silent echo.
        plan = TargetedRetryPlanner().target_plan("what is food safety", ["missing_provision"], "general", {})
        assert plan.arm == "none" and plan.query == "what is food safety"

    def test_definition_builder(self):
        plan = TargetedRetryPlanner().target_plan("meaning?", ["missing_definition"], "definition", {"answer": "'food business' means any undertaking", "chunks": []})
        assert plan.arm == "definition" and "means" in plan.query

    def test_definition_unminable_fallback(self):
        plan = TargetedRetryPlanner().target_plan("meaning?", ["missing_definition"], "definition", {})
        assert plan.arm == "none"

    def test_hierarchy_builder(self):
        plan = TargetedRetryPlanner().target_plan("Section 16 of the FSS Act exceptions", ["missing_exception"], "general", {"chunks": []})
        assert plan.arm == "hierarchy" and "notwithstanding" in plan.query

    def test_kg_builder(self):
        plan = TargetedRetryPlanner().target_plan("q", ["missing_cross_ref"], "general", {"kg_paths": [{"steps": ["Section 31"]}]})
        assert plan.arm == "kg_paths"

    def test_kg_empty_fallback(self):
        plan = TargetedRetryPlanner().target_plan("q", ["missing_cross_ref"], "general", {})
        assert plan.arm == "none" and plan.query == "q"

    def test_abstain_arm_none(self):
        plan = TargetedRetryPlanner().target_plan("q", ["abstain_required"], "general", {})
        assert plan.arm == "none" and plan.query == "q"

    def test_no_pseudo_tags_anywhere(self):
        for q in _all_queries(TargetedRetryPlanner()):
            assert not _PSEUDO.search(q), q

    def test_plan_json_roundtrip(self):
        plan = TargetedRetryPlanner().target_plan("Section 31 FSS Act", ["missing_provision"], "general", {"top_k": 8})
        assert TargetPlan.from_dict(plan.to_dict()) == plan


class TestShadow:
    def test_empty_claims_flag(self):
        from app.rag.verification.hardened_scorer import (
            hardened_citation_ratio,
            hardened_claim_ratio,
        )

        r, f = hardened_claim_ratio([], "non-empty answer")
        assert r == 0.50 and f == "empty_claims"
        r2, _ = hardened_claim_ratio([], "")
        assert r2 == 1.0
        c, cf = hardened_citation_ratio(None, "answer text")
        assert c == 0.50 and cf == "no_citations"

    def test_strict_gates(self):
        from app.rag.retrieval.result import RetrievedChunk
        from app.rag.verification.claim_extractor import ClaimExtractor
        from app.rag.verification.hardened_scorer import verify_claim_strict

        chunk = RetrievedChunk(chunk_id="c1", text="Section 55 allows sale.", score=1.0, section_number="55")
        claims = ClaimExtractor().extract("Under Section 99, sale is banned.")
        assert claims
        ok, reason = verify_claim_strict(claims[0], [chunk])
        assert ok is False and reason == "section_mismatch"
        claims2 = ClaimExtractor().extract("No person shall sell under Section 55.")
        ok2, reason2 = verify_claim_strict(claims2[0], [RetrievedChunk(chunk_id="c2", text="Section 55: sale may be permitted.", score=1.0, section_number="55")])
        assert ok2 is False and reason2 == "polarity_mismatch"
