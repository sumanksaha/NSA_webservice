"""Calibratable confidence for the FSO advisory (ADR-0008).

The selector emits ``confidence`` interpreted as the probability that the
selected Act survives review. V1 (ADR-0003) shipped a fixed evidence
heuristic — a *score*, not a calibrated probability: no outcome labels
existed to fit against. This module makes the model calibratable without
changing today's behaviour:

- ``HeuristicConfidence`` — the default parameter set
  (``heuristic_v1``); reproduces the ADR-0003 formula exactly.
- ``IsotonicConfidence`` — a monotone (PAVA) calibration table fitted from
  ``(heuristic score, outcome)`` pairs once the outcome-learning loop
  supplies labels; deterministically re-fittable, serializable breakpoints.
- ``expected_calibration_error`` / ``brier_score`` /
  ``reliability_bins`` live in :mod:`app.rag.evaluation.advisor_metrics`
  and score *any* confidence stream against outcomes.

Every assessment carries its ``param_set`` id so the payload records which
model produced a given number and historical outputs stay reproducible.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Callable

__all__ = [
    "ConfidenceAssessment",
    "ConfidenceFn",
    "HeuristicConfidence",
    "IsotonicConfidence",
    "fit_isotonic_confidence",
]


@dataclass(frozen=True)
class ConfidenceAssessment:
    """One confidence output: the value plus the parameter set that made it."""

    value: float
    param_set: str


#: Anything the selector can consult: ``(n_anchors, lab_report_available) →
#: assessment``. The default is :class:`HeuristicConfidence`; a fitted
#: :class:`IsotonicConfidence` is a drop-in replacement.
ConfidenceFn = Callable[[int, bool], ConfidenceAssessment]


class HeuristicConfidence:
    """V1 evidence heuristic, kept exactly (ADR-0003) and versioned.

    Base 0.7 (single grounded anchor) + 0.1 per additional distinct anchor
    (cap +0.2) + 0.1 with a statutory lab report, capped at 1.0. This is an
    *evidence score*: treat it as a calibrated probability only after the
    outcome loop validates it (or replaces it via ``fit_isotonic_confidence``).
    """

    PARAM_SET = "heuristic_v1"

    def __call__(self, n_anchors: int, lab_report_available: bool) -> ConfidenceAssessment:
        score = 0.7 + 0.1 * min(max(n_anchors - 1, 0), 2)
        if lab_report_available:
            score += 0.1
        return ConfidenceAssessment(round(min(score, 1.0), 2), self.PARAM_SET)


@dataclass(frozen=True)
class IsotonicConfidence:
    """Monotone calibration table over the base model's raw score.

    ``breakpoints`` is a sorted list of ``(raw_score, calibrated_probability)``
    block means produced by :func:`fit_isotonic_confidence` (PAVA). Lookup is
    a step function: the probability of the last block whose raw score is ≤
    the query, clamped to the end blocks outside the fitted range.
    """

    breakpoints: tuple[tuple[float, float], ...]
    base: HeuristicConfidence
    param_set: str

    def __call__(self, n_anchors: int, lab_report_available: bool) -> ConfidenceAssessment:
        raw = self.base(n_anchors, lab_report_available).value
        xs = [x for x, _ in self.breakpoints]
        idx = bisect_right(xs, raw) - 1
        idx = max(0, min(idx, len(self.breakpoints) - 1))
        return ConfidenceAssessment(round(self.breakpoints[idx][1], 4), self.param_set)


def fit_isotonic_confidence(
    records: list[tuple[float, bool]],
    param_set: str,
    *,
    base: HeuristicConfidence | None = None,
) -> IsotonicConfidence:
    """Fit a monotone calibration table from ``(raw_score, outcome)`` pairs.

    Pool-adjacent-violators (PAVA) isotonic regression with equal weights,
    over outcomes as 0/1. Records whose raw scores tie are pooled; blocks
    are merged until the sequence of block means is nondecreasing. Requires
    at least one record.
    """
    if not records:
        raise ValueError("fit_isotonic_confidence requires at least one record.")
    base_model = base if base is not None else HeuristicConfidence()

    # Group by raw score (deterministic: sort, then pool exact ties).
    ordered = sorted(records, key=lambda r: r[0])
    blocks: list[list[float]] = []  # [mean_x, sum_y, weight] per block
    i = 0
    while i < len(ordered):
        x = ordered[i][0]
        sum_y = 0.0
        weight = 0
        while i < len(ordered) and ordered[i][0] == x:
            sum_y += 1.0 if ordered[i][1] else 0.0
            weight += 1
            i += 1
        blocks.append([x, sum_y, float(weight)])

    # PAVA: merge adjacent blocks whose means violate monotonicity.
    stack: list[list[float]] = []
    for block in blocks:
        stack.append(block)
        while len(stack) >= 2 and stack[-2][1] / stack[-2][2] > stack[-1][1] / stack[-1][2]:
            upper = stack.pop()
            lower = stack.pop()
            stack.append(
                [
                    (lower[0] * lower[2] + upper[0] * upper[2]) / (lower[2] + upper[2]),
                    lower[1] + upper[1],
                    lower[2] + upper[2],
                ]
            )

    breakpoints = tuple((x, total_y / weight) for x, total_y, weight in stack)
    return IsotonicConfidence(breakpoints=breakpoints, base=base_model, param_set=param_set)
