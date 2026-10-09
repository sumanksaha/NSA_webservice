"""SPEC-3: Verifier-gated retry replacement guard.

The retry answer is adopted only when measurably better than baseline;
otherwise the baseline answer is kept.  This prevents flip-downs like Q132
where the retry arm regressed.

The selector is a pure function so it can be unit-tested and called from both
the production finalize path and the eval mirror.
"""

from __future__ import annotations

from typing import Any

from app.rag.agent.thresholds import (
    RETRY_ADOPT_GROUNDEDNESS_SLACK,
    RETRY_ADOPT_SOFT_DELTA_AT_LEAST,
)

__all__ = ["OBSERVABLE_METRICS", "capture_arm", "observe_retry_arms", "select_arm"]


#: Production-observable signals for a retry-vs-baseline comparison.
#:
#: Deliberately NOT ``binary_correct`` / ``answer_correctness``: those are
#: gold-referenced and do not exist on the serving path.  :func:`select_arm`
#: needs them, so the adopt rule cannot run in production — see
#: :func:`observe_retry_arms` for what runs there instead.
OBSERVABLE_METRICS = (
    "groundedness_score",
    "claim_groundedness",
    "n_citations",
    "answer_length",
)


def capture_arm(
    state: dict[str, Any],
    result: dict[str, Any],
    claim_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stash this generation as the baseline or retry arm for SPEC-3 shadow.

    The linear path runs ``generate_node`` again after a retry and the second
    run *overwrites* ``state["answer"]`` / ``state["response"]``, so the
    pre-retry answer does not survive to ``finalize_node``. Without this
    capture there is no pair to compare and the guard cannot be evaluated at
    all.

    The first generation (``retry_count == 0``) is the baseline; every later
    generation overwrites the retry arm. Returns the state keys to merge.
    """
    answer = str(result.get("answer") or "")
    citations = result.get("citations") or []
    scorecard = {
        "groundedness_score": float(result.get("groundedness_score", 0.0) or 0.0),
        "claim_groundedness": float((claim_report or {}).get("claim_groundedness", 0.0) or 0.0),
        "n_citations": len(citations),
        "answer_length": len(answer),
        "hallucination_detected": bool(result.get("hallucination_detected", False)),
    }
    retry_count = int(state.get("retry_count", 0) or 0)
    if retry_count == 0:
        return {
            "retry_guard_baseline": scorecard,
            "retry_guard_retry": None,
        }
    return {"retry_guard_retry": scorecard}


def observe_retry_arms(baseline: dict[str, Any] | None, retry: dict[str, Any] | None) -> dict[str, Any]:
    """Shadow comparison of a retry against the answer it replaced.

    **This never adopts.**  SPEC-3's adopt rule keys on ``binary_correct`` and
    ``answer_correctness``, which are computed against a gold reference and are
    therefore unavailable on the serving path. Inventing a stand-in would
    produce a decision the evidence cannot support, so production records the
    observable comparison and leaves the served answer alone until SPEC-4
    calibration decides whether the gold-dependent rule should gate anything.

    Reports whether the retry looks observably worse than the answer it
    replaced, which is the signal an operator needs to decide that.
    """
    if not baseline or not retry:
        return {"available": False, "reason": "no paired arms (retry did not regenerate)"}

    deltas = {
        m: round(float(retry.get(m, 0.0)) - float(baseline.get(m, 0.0)), 4)
        for m in OBSERVABLE_METRICS
    }
    return {
        "available": True,
        "adopted": False,
        "reason": "shadow_only_no_gold_referenced_score",
        "deltas": deltas,
        # A retry that lowers groundedness or claim support is the case worth
        # alerting on; citation-breadth growth is the intended direction.
        "observably_worse": bool(
            deltas["groundedness_score"] < 0 or deltas["claim_groundedness"] < 0,
        ),
        "baseline": {m: baseline.get(m) for m in OBSERVABLE_METRICS},
        "retry": {m: retry.get(m) for m in OBSERVABLE_METRICS},
        "hallucination_delta": int(bool(retry.get("hallucination_detected", False)))
        - int(bool(baseline.get("hallucination_detected", False))),
    }


def select_arm(
    baseline: dict[str, Any],
    retry: dict[str, Any],
    *,
    soft_delta_at_least: float = RETRY_ADOPT_SOFT_DELTA_AT_LEAST,
    groundedness_slack: float = RETRY_ADOPT_GROUNDEDNESS_SLACK,
) -> dict[str, Any]:
    """Decide whether to adopt the retry answer or keep baseline.

    Args:
        baseline: Baseline scorecard (must contain ``binary_correct``,
            ``answer_correctness``, ``citation_precision``, ``groundedness_score``).
        retry: Retry scorecard (same schema).
        soft_delta_at_least: Minimum ``answer_correctness`` improvement to adopt
            retry when binary is tied.
        groundedness_slack: Maximum ``groundedness_score`` regression tolerated.

    Returns:
        A dict with ``selected_arm`` in ``{"baseline", "retry"}`` plus the
        deciding deltas.  The selector never produces a result worse than the
        best arm: if retry is better it is selected, otherwise baseline is kept.

    """
    b_binary = int(baseline.get("binary_correct", 0))
    r_binary = int(retry.get("binary_correct", 0))

    b_soft = float(baseline.get("answer_correctness", 0.0))
    r_soft = float(retry.get("answer_correctness", 0.0))

    b_prec = float(baseline.get("citation_precision", 0.0))
    r_prec = float(retry.get("citation_precision", 0.0))

    b_ground = float(baseline.get("groundedness_score", 0.0))
    r_ground = float(retry.get("groundedness_score", 0.0))

    soft_delta = round(r_soft - b_soft, 4)
    prec_delta = round(r_prec - b_prec, 4)
    ground_delta = round(r_ground - b_ground, 4)

    # Rule 1: retry is strictly better on binary correctness
    if r_binary > b_binary:
        return {
            "selected_arm": "retry",
            "reason": "binary_correct",
            "soft_delta": soft_delta,
            "precision_delta": prec_delta,
            "groundedness_delta": ground_delta,
        }

    # Rule 2: binary tied (both 0 or both 1), but retry is substantially better
    # on soft score without regressing on precision or groundedness.
    # A binary regression (1 -> 0) is never tolerated, even if soft improves.
    if (
        r_binary == b_binary
        and soft_delta >= soft_delta_at_least
        and prec_delta >= 0
        and ground_delta >= -groundedness_slack
    ):
        return {
            "selected_arm": "retry",
            "reason": "soft_delta",
            "soft_delta": soft_delta,
            "precision_delta": prec_delta,
            "groundedness_delta": ground_delta,
        }

    # Default: keep baseline (ties, regressions, or insufficient improvement)
    return {
        "selected_arm": "baseline",
        "reason": "baseline_preferred",
        "soft_delta": soft_delta,
        "precision_delta": prec_delta,
        "groundedness_delta": ground_delta,
    }
