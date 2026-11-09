"""Tests for abstain_node audit enrichment (S4).

The abstain audit entry must record which sufficiency signals drove the
refusal (coverage, relevance, authority, contradiction, temporal) plus the
budget-exhausted flag, so operators can tell whether a missed abstention was
retrieval- or sufficiency-driven.
"""

from __future__ import annotations

from app.rag.agent.nodes.dag import abstain_node


def _state(**overrides):
    base = {
        "audit_trail": [],
        "task_sufficiency": [],
        "budget_exhausted": False,
        "abstain_required": False,
        "evidence_coverage": 0.0,
    }
    base.update(overrides)
    return base


def test_abstain_node_records_reason_and_abstained():
    state = _state()
    result = abstain_node(state)
    assert result["abstained"] is True
    assert result["groundedness"] == 0.0
    assert result["hallucination_detected"] is False
    entry = result["audit_trail"][-1]
    assert entry["node"] == "abstain"
    assert entry["detail"]["reason"] == "insufficient_evidence"


def test_abstain_node_records_drivers_from_task_sufficiency():
    state = _state(
        task_sufficiency=[
            {"task_id": "t1", "failures": ["coverage", "relevance"]},
            {"task_id": "t2", "failures": ["authority"]},
        ],
        abstain_required=True,
        evidence_coverage=0.2,
    )
    result = abstain_node(state)
    entry = result["audit_trail"][-1]
    detail = entry["detail"]
    assert detail["reason"] == "insufficient_evidence"
    assert "coverage" in detail["abstain_drivers"]
    assert "relevance" in detail["abstain_drivers"]
    assert "authority" in detail["abstain_drivers"]
    assert detail["abstain_required"] is True
    assert detail["evidence_coverage"] == 0.2


def test_abstain_node_records_budget_exhausted_driver():
    state = _state(
        task_sufficiency=[],
        budget_exhausted=True,
        abstain_required=True,
        evidence_coverage=0.0,
    )
    result = abstain_node(state)
    entry = result["audit_trail"][-1]
    assert "BUDGET_EXHAUSTED" in entry["detail"]["abstain_drivers"]


def test_abstain_node_dedupes_drivers():
    state = _state(
        task_sufficiency=[
            {"task_id": "t1", "failures": ["coverage", "coverage"]},
            {"task_id": "t2", "failures": ["coverage"]},
        ],
    )
    result = abstain_node(state)
    drivers = result["audit_trail"][-1]["detail"]["abstain_drivers"]
    assert drivers == ["coverage"]


def test_abstain_node_no_drivers_when_clean():
    state = _state(task_sufficiency=[{"task_id": "t1", "failures": []}])
    result = abstain_node(state)
    detail = result["audit_trail"][-1]["detail"]
    assert detail["reason"] == "insufficient_evidence"
    assert "abstain_drivers" not in detail
