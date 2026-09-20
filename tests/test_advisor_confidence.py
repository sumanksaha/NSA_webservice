"""Calibratable-confidence tests (ADR-0008).

The default ``heuristic_v1`` parameter set must reproduce the ADR-0003
confidence formula *exactly* (payload contract), custom models must flow
through the selector additively, and the PAVA fitter + evaluation metrics
must match hand-derived values on synthetic streams.
"""

from __future__ import annotations

import pytest

from app.rag.advisor.confidence import (
    ConfidenceAssessment,
    HeuristicConfidence,
    fit_isotonic_confidence,
)
from app.rag.advisor.selector import DeterministicActSelector
from app.rag.evaluation.advisor_metrics import (
    abstention_rate,
    brier_score,
    expected_calibration_error,
    reliability_bins,
)


# ---------------------------------------------------------------------------
# heuristic_v1: byte-exact reproduction of the ADR-0003 formula
# ---------------------------------------------------------------------------


def test_heuristic_v1_reproduces_v1_values():
    model = HeuristicConfidence()
    assert model(1, False).value == 0.7
    assert model(2, False).value == 0.8
    assert model(3, False).value == 0.9
    assert model(4, False).value == 0.9  # anchor bonus caps at +0.2
    assert model(1, True).value == 0.8
    assert model(3, True).value == 1.0
    assert model(2, False).param_set == "heuristic_v1"


def test_selector_default_payload_carries_param_set():
    out = DeterministicActSelector().select_act(retrieved_sections=["51"])
    act = out["fso_act"]
    assert act["confidence"] == 0.7
    assert act["confidence_param_set"] == "heuristic_v1"


def test_selector_confidence_scales_exactly_as_v1():
    selector = DeterministicActSelector()
    assert selector.select_act(retrieved_sections=["51", "55"])["fso_act"]["confidence"] == 0.8
    assert selector.select_act(retrieved_sections=["51", "52", "55"], lab_report_available=True)["fso_act"][
        "confidence"
    ] == 1.0
    assert selector.confidence_score(2, True) == 0.9  # compat float accessor


def test_custom_confidence_fn_flows_through_payload():
    def shrunk(n_anchors: int, lab: bool) -> ConfidenceAssessment:
        return ConfidenceAssessment(0.42, "shrinkage_v1")

    selector = DeterministicActSelector(confidence_fn=shrunk)
    act = selector.select_act(retrieved_sections=["51"])["fso_act"]
    assert act["confidence"] == 0.42
    assert act["confidence_param_set"] == "shrinkage_v1"
    assert act["game_theory_basis"]  # legacy fields untouched


def test_compute_wrapper_accepts_confidence_fn():
    from app.rag.advisor import compute_fso_advisory

    out = compute_fso_advisory(["51"], confidence_fn=lambda n, l: ConfidenceAssessment(0.5, "x"))
    assert out["fso_act"]["confidence"] == 0.5
    assert out["fso_act"]["confidence_param_set"] == "x"


# ---------------------------------------------------------------------------
# PAVA isotonic fitter
# ---------------------------------------------------------------------------


def test_fit_isotonic_monotone_records_pass_through():
    iso = fit_isotonic_confidence([(0.7, True), (0.8, True), (0.9, True)], "iso_v1")
    assert iso.breakpoints == ((0.7, 1.0), (0.8, 1.0), (0.9, 1.0))
    assert iso(1, False).value == 1.0  # raw 0.7 → first block
    assert iso.param_set == "iso_v1"


def test_fit_isotonic_pools_violating_blocks():
    """(0.7, ✓), (0.8, ✗) violate monotonicity → pooled to one block at
    mean 0.5; the calibration is then flat 0.5 across the pooled range."""
    iso = fit_isotonic_confidence([(0.7, True), (0.8, False)], "iso_v1")
    assert iso.breakpoints == ((0.75, 0.5),)
    assert iso(1, False).value == 0.5
    assert iso(3, True).value == 0.5


def test_fit_isotonic_hand_derived_with_ties_and_weights():
    """(0.1,✓), (0.2,✗×3), (0.3,✓): the 1.0 and 0.0 blocks violate → merged
    to mean 0.25 at weighted x 0.175; 0.25 ≤ 1.0 so no further merge."""
    iso = fit_isotonic_confidence(
        [(0.1, True), (0.2, False), (0.2, False), (0.2, False), (0.3, True)], "iso_v1"
    )
    assert iso.breakpoints == ((pytest.approx(0.175), 0.25), (0.3, 1.0))


def test_isotonic_clamps_outside_fitted_range():
    iso = fit_isotonic_confidence([(0.7, True), (0.9, False)], "iso_v1")
    # Pooled block (0.8, 0.5); raw 0.7 (below min) and raw 1.0 (above max) both clamp to it.
    assert iso(1, False).value == 0.5
    assert iso(3, True).value == 0.5


def test_fit_isotonic_requires_records():
    with pytest.raises(ValueError):
        fit_isotonic_confidence([], "iso_v1")


def test_isotonic_end_to_end_through_selector():
    iso = fit_isotonic_confidence([(0.7, True), (0.8, False)], "iso_v1")
    act = DeterministicActSelector(confidence_fn=iso).select_act(retrieved_sections=["51"])["fso_act"]
    assert act["confidence"] == 0.5
    assert act["confidence_param_set"] == "iso_v1"


# ---------------------------------------------------------------------------
# Evaluation metrics
# ---------------------------------------------------------------------------


def test_brier_score_hand_derived():
    assert brier_score([(1.0, True), (0.0, False)]) == 0.0  # perfect
    assert brier_score([(0.5, True), (0.5, False)]) == 0.25  # uniform guess
    assert brier_score([(0.9, False)] * 10) == 0.81  # confidently wrong
    with pytest.raises(ValueError):
        brier_score([])
    with pytest.raises(ValueError):
        brier_score([(1.5, True)])  # out of range fails loudly


def test_expected_calibration_error_hand_derived():
    assert expected_calibration_error([(1.0, True), (0.0, False)]) == 0.0
    # Perfectly anti-calibrated single bin: |0 − 0.9|.
    assert expected_calibration_error([(0.9, False)] * 10) == 0.9
    # Both in [0.9, 1.0]: mean conf 1.0 vs rate 0.5 → ECE 0.5.
    assert expected_calibration_error([(1.0, True), (1.0, False)]) == 0.5


def test_reliability_bins_boundaries_and_empties():
    bins = reliability_bins([(0.05, True), (0.15, False), (0.15, True), (0.95, False)], n_bins=10)
    assert bins == [
        {"lower": 0.0, "upper": 0.1, "count": 1, "mean_confidence": 0.05, "empirical_rate": 1.0},
        {"lower": 0.1, "upper": 0.2, "count": 2, "mean_confidence": 0.15, "empirical_rate": 0.5},
        {"lower": 0.9, "upper": 1.0, "count": 1, "mean_confidence": 0.95, "empirical_rate": 0.0},
    ]
    # 1.0 lands in the last bin, not out of range.
    top = reliability_bins([(1.0, True)], n_bins=10)
    assert top[0]["lower"] == 0.9 and top[0]["count"] == 1


def test_abstention_rate_counts_fso_act_none():
    records = [{"fso_act": None}, {"fso_act": {"action": "x"}}, {"fso_act": None}]
    assert abstention_rate(records) == pytest.approx(2 / 3)
    assert abstention_rate([{"fso_act": {"action": "x"}}]) == 0.0
    with pytest.raises(ValueError):
        abstention_rate([])
    with pytest.raises(TypeError):
        abstention_rate([{"no_fso_act": 1}])  # type: ignore[list-item]
