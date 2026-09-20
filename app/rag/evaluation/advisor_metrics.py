"""Advisory calibration metrics — scoring confidence streams against outcomes.

Pure, offline, dependency-free. Consumes ``(predicted_confidence, observed_outcome)``
pairs — where the outcome records whether the advisory's selected Act
survived review (adjudication upheld the Act, FBO complied without
successful contest, etc.). Today no outcome labels are collected yet
(ADR-0008 defers the learning loop); these functions are the harness the
loop will feed, and the unit tests pin their math on hand-derived synthetic
streams so any confidence model — the ``heuristic_v1`` default or a fitted
isotonic table — can be validated the day labels exist.
"""

from __future__ import annotations

from typing import Iterable

__all__ = [
    "abstention_rate",
    "brier_score",
    "expected_calibration_error",
    "reliability_bins",
]

#: Confidence streams are probabilities in [0, 1]; refuse anything else so a
#: mis-scaled score column fails loudly instead of silently "calibrating".
def _validate_pairs(pairs: Iterable[tuple[float, bool | float]]) -> list[tuple[float, float]]:
    validated: list[tuple[float, float]] = []
    for confidence, outcome in pairs:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence {confidence!r} outside [0, 1].")
        validated.append((float(confidence), 1.0 if outcome else 0.0))
    return validated


def brier_score(pairs: Iterable[tuple[float, bool | float]]) -> float:
    """Mean squared error of the confidence stream: ``mean((p − y)²)``."""
    validated = _validate_pairs(pairs)
    if not validated:
        raise ValueError("brier_score requires at least one pair.")
    return round(sum((p - y) ** 2 for p, y in validated) / len(validated), 6)


def reliability_bins(
    pairs: Iterable[tuple[float, bool | float]],
    n_bins: int = 10,
) -> list[dict[str, float | int]]:
    """Equal-width reliability bins over [0, 1] (last bin includes 1.0).

    Each bin dict: ``lower`` / ``upper`` edges, ``count``, ``mean_confidence``
    and ``empirical_rate`` (observed fraction of survived outcomes). Empty
    bins are omitted.
    """
    if n_bins < 1:
        raise ValueError("n_bins must be ≥ 1.")
    validated = _validate_pairs(pairs)
    width = 1.0 / n_bins
    sums: list[list[float]] = [[0.0, 0.0, 0.0] for _ in range(n_bins)]  # [conf, outcome, count]
    for confidence, outcome in validated:
        idx = min(int(confidence / width), n_bins - 1)
        sums[idx][0] += confidence
        sums[idx][1] += outcome
        sums[idx][2] += 1
    bins: list[dict[str, float | int]] = []
    for idx, (conf_sum, outcome_sum, count) in enumerate(sums):
        if not count:
            continue
        bins.append(
            {
                "lower": round(idx * width, 10),
                "upper": round((idx + 1) * width, 10),
                "count": int(count),
                "mean_confidence": round(conf_sum / count, 6),
                "empirical_rate": round(outcome_sum / count, 6),
            }
        )
    return bins


def expected_calibration_error(pairs: Iterable[tuple[float, bool | float]], n_bins: int = 10) -> float:
    """ECE: ``Σ_b (n_b / N) · |accuracy_b − confidence_b|`` over reliability bins."""
    validated = _validate_pairs(pairs)
    if not validated:
        raise ValueError("expected_calibration_error requires at least one pair.")
    total = len(validated)
    ece = 0.0
    for bin_ in reliability_bins(validated, n_bins):
        gap = abs(float(bin_["empirical_rate"]) - float(bin_["mean_confidence"]))
        ece += (float(bin_["count"]) / total) * gap
    return round(ece, 6)


def abstention_rate(advisories: Iterable[dict]) -> float:
    """Share of advisory payloads that abstained (``fso_act is None``).

    Records are selector/graph payloads: dicts carrying ``fso_act`` (dict or
    None). Malformed records raise ``TypeError`` via the attribute check so a
    wrong column fails loudly.
    """
    records = list(advisories)
    if not records:
        raise ValueError("abstention_rate requires at least one advisory payload.")
    abstained = 0
    for record in records:
        if not isinstance(record, dict) or "fso_act" not in record:
            raise TypeError("each advisory record must be a dict with an 'fso_act' key.")
        if record["fso_act"] is None:
            abstained += 1
    return round(abstained / len(records), 6)
