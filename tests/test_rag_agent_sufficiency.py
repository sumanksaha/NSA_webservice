"""Tests for the per-task 7-signal sufficiency rubric (Phase 2, items 14–17).

Pure-function tests over serialized chunk dicts — no retrieval, no LLM.
"""

from __future__ import annotations

from app.rag.agent.sufficiency import (
    CLAIM_GROUNDEDNESS_THRESHOLD,
    GATING_SIGNALS,
    SufficiencyAssessor,
    aggregate_verdicts,
    as_retrieved_chunks,
    chunk_authority_score,
    signal_to_failure,
)
from app.rag.evidence_task import EvidenceTask
from app.rag.verification.evidence_verifier import EvidenceVerifier


def _task(task_id="T1", **overrides):
    data = {
        "task_id": task_id,
        "objective": "determine_penalty",
        "question": f"question for {task_id}",
        "evidence_requirement": "penalty",
        "dependency": [],
    }
    data.update(overrides)
    return EvidenceTask.from_dict(data)


def _chunk(chunk_id, score=0.9, text="the penalty is Rs. 500 under section 12", **extra):
    base = {"chunk_id": chunk_id, "score": score, "text": text}
    base.update(extra)
    return base


# ---------------------------------------------------------------------- #
# Authority scoring (item 17)
# ---------------------------------------------------------------------- #


def test_authority_score_prefers_statutes():
    assert chunk_authority_score({"document_type": "act"}) == 1.0
    assert chunk_authority_score({"document_type": "regulation"}) > chunk_authority_score(
        {"document_type": "blog"}
    )


def test_authority_score_uses_authority_name_hierarchy():
    assert chunk_authority_score({"authority": "Supreme Court of India"}) > 0.9
    assert chunk_authority_score({"authority": "Food Safety and Standards Authority"}) >= 0.9
    # Unknown metadata is neutral, not penalized.
    assert chunk_authority_score({}) == 0.5


# ---------------------------------------------------------------------- #
# Contradiction detection (item 16)
# ---------------------------------------------------------------------- #


def test_find_contradictions_numeric_same_section():
    verifier = EvidenceVerifier()
    chunks = as_retrieved_chunks(
        [
            _chunk("c1", text="fine of Rs. 500", section_number="12"),
            _chunk("c2", text="fine of Rs. 1000", section_number="12"),
        ]
    )
    conflicts = verifier.find_contradictions(chunks)
    assert len(conflicts) == 1
    assert conflicts[0].kind == "numeric"


def test_find_contradictions_ignores_different_sections():
    verifier = EvidenceVerifier()
    chunks = as_retrieved_chunks(
        [
            _chunk("c1", text="fine of Rs. 500", section_number="12"),
            _chunk("c2", text="fine of Rs. 1000", section_number="15"),
        ]
    )
    assert verifier.find_contradictions(chunks) == []


def test_find_contradictions_prohibition_vs_permission():
    verifier = EvidenceVerifier()
    chunks = as_retrieved_chunks(
        [
            _chunk("c1", text="no person shall sell this product", section_number="12"),
            _chunk("c2", text="the product may be sold freely", section_number="12"),
        ]
    )
    conflicts = verifier.find_contradictions(chunks)
    assert len(conflicts) == 1
    assert conflicts[0].kind == "prohibition"


def test_find_contradictions_empty_for_single_chunk():
    verifier = EvidenceVerifier()
    chunks = as_retrieved_chunks([_chunk("c1")])
    assert verifier.find_contradictions(chunks) == []


# ---------------------------------------------------------------------- #
# The 7-signal rubric (item 14)
# ---------------------------------------------------------------------- #


def test_assess_task_all_signals_pass_on_solid_evidence():
    verdict = SufficiencyAssessor().assess_task(
        _task(),
        [
            _chunk("c1", text="penalty is Rs. 500 under section 12", document_type="act"),
            _chunk("c2", text="section 12 prescribes imprisonment", document_type="act"),
        ],
    )
    assert verdict.sufficient
    assert verdict.failures == []
    assert set(verdict.signals) == {
        "coverage",
        "relevance",
        "authority",
        "specificity",
        "completeness",
        "contradiction",
        "temporal",
    }


def test_assess_task_fails_coverage_on_empty_evidence():
    verdict = SufficiencyAssessor().assess_task(_task(), [])
    assert not verdict.sufficient
    assert "coverage" in verdict.failures


def test_assess_task_fails_relevance_on_low_scores():
    verdict = SufficiencyAssessor().assess_task(
        _task(), [_chunk("c1", score=0.2, text="unrelated text about packaging")],
    )
    assert "relevance" in verdict.failures


def test_assess_task_fails_authority_on_low_tier_evidence():
    verdict = SufficiencyAssessor().assess_task(
        _task(),
        [_chunk("c1", text="someone blogged that the fine is 500 rupees", document_type="blog")],
    )
    assert "authority" in verdict.failures


def test_assess_task_fails_temporal_on_superseded_text():
    verdict = SufficiencyAssessor().assess_task(
        _task(),
        [_chunk("c1", text="section 12 was repealed and replaced by the 2021 Act", document_type="act")],
    )
    assert "temporal" in verdict.failures
    assert verdict.signals["temporal"]["detail"]["conflicting_chunks"] == ["c1"]


def test_assess_task_specificity_is_advisory():
    """specificity failures are diagnosed but do not gate synthesis."""
    verdict = SufficiencyAssessor().assess_task(
        _task(),
        [
            _chunk("c1", text="the fine may extend to five hundred rupees", document_type="act"),
            _chunk("c2", text="the officer may impose the penalty", document_type="act"),
        ],
    )
    assert "specificity" in verdict.failures
    # No *gating* signal failed → sufficient for synthesis.
    assert not (set(verdict.failures) & GATING_SIGNALS)


def test_assess_task_detects_conflicting_amounts():
    verdict = SufficiencyAssessor().assess_task(
        _task(),
        [
            _chunk("c1", text="fine of Rs. 500", section_number="12", document_type="act"),
            _chunk("c2", text="fine of Rs. 1000", section_number="12", document_type="act"),
        ],
    )
    assert "contradiction" in verdict.failures
    assert verdict.conflicts


def test_assess_task_temporal_scope_mismatch():
    verdict = SufficiencyAssessor().assess_task(
        _task(temporal_scope="2018"),
        [_chunk("c1", text="as amended with effect from 2021 the fine applies", document_type="act")],
    )
    assert "temporal" in verdict.failures


# ---------------------------------------------------------------------- #
# Aggregation → routing signals
# ---------------------------------------------------------------------- #


def test_aggregate_mixed_verdicts():
    ok = SufficiencyAssessor().assess_task(
        _task("T1"), [_chunk("c1", text="penalty is Rs. 500 under section 12", document_type="act")]
    )
    empty = SufficiencyAssessor().assess_task(_task("T2"), [])
    agg = aggregate_verdicts([ok, empty])
    assert agg["sufficient"] is True  # 1/2 >= 0.5 ratio
    assert agg["critical_failure"] is True  # T2 failed coverage (critical)
    assert "T2" in agg["failed_tasks"]
    assert "INSUFFICIENT_EVIDENCE_COVERAGE" in agg["failure_codes"]


def test_aggregate_all_sufficient():
    ok1 = SufficiencyAssessor().assess_task(
        _task("T1"), [_chunk("c1", text="penalty Rs. 500 under section 12", document_type="act")]
    )
    ok2 = SufficiencyAssessor().assess_task(
        _task("T2"), [_chunk("c2", text="section 12 defines the offence", document_type="act")]
    )
    agg = aggregate_verdicts([ok1, ok2])
    assert agg["sufficient"] is True
    assert agg["critical_failure"] is False
    # Advisory failures (specificity) are diagnosed but do not block.
    assert "MISSING_SPECIFICITY" in agg["failure_codes"]


def test_aggregate_empty():
    agg = aggregate_verdicts([])
    assert agg["sufficient"] is False
    assert agg["verdicts"] == []


def test_signal_to_failure_taxonomy():
    assert signal_to_failure("contradiction") == "EVIDENCE_CONTRADICTION"
    assert signal_to_failure("temporal") == "TEMPORAL_INVALIDITY"
    assert signal_to_failure("authority") == "INSUFFICIENT_AUTHORITY_SCORE"
    assert signal_to_failure("unknown") == "INSUFFICIENT_EVIDENCE_COVERAGE"


def test_claim_groundedness_threshold_exists():
    assert 0.0 < CLAIM_GROUNDEDNESS_THRESHOLD <= 1.0
