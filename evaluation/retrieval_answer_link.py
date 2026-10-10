"""Retrieval -> answer metric bridge.

Joins *where the gold evidence ranked* to *whether the answer was right*, so
the two halves of the pipeline can be argued about separately.  Retrieval
recall rising while answer correctness stays flat is the central finding of
``docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md`` (R@100 ~88%, binary
~9-12% mechanical / ~61% adjudicated); this module exists to say how much of
that gap retrieval could actually close.

Inputs are the per-question attribution rows produced by
``evaluation.failure_attribution``:

``qid, verdict, bucket, model_action, gold_depth, gold_in_prompt, soft, binary``

The centerpiece is :func:`bridge` -> ``ranker_upside``, which projects a
retrieval fix onto answer correctness **twice, under two different
assumptions**, because the gap between those assumptions is the whole
decision.

``ranker_upside`` — the dual band
---------------------------------
For a cut-off rank ``k``:

``promotable_model_wrong``
    Questions the ranker could plausibly fix: the human audit says the
    *model* was wrong (so the label is trustworthy), the gold provision is
    retrievable (``gold_depth > k``), and it was *not* already in the prompt
    (so there is something to fix).  ``gold_depth is None`` — never retrieved
    at any depth — is excluded: no reordering reaches it.

``projected_binary_gain_optimistic``
    ``promotable / n`` — the share of the population that would flip — times
    ``conversion_in_prompt``: the binary rate the model achieves on questions
    where the gold **was** in the window.  The assumption is that giving a
    model the provision it lacked works as well as giving it one it already
    had.

``projected_binary_gain_causal``
    The same population share, multiplied by the *measured* conversion from
    the context-window A/B (:data:`WINDOW_A_B_CAUSAL`) rather than by the
    in-prompt rate.

Both bands are reported because they disagree, and the gap is the finding:

* The optimistic band says retrieval is worth fixing.
* The causal band says that on the questions where retrieval was the blocker
  and the gold was then handed to the model, binary correctness moved
  **0/7** — in three separate runs.  The model was given the gold provision
  and still answered wrong, every time.

So ``projected_binary_gain_causal`` is not a typo or an empty case; it is the
measured upper bound on what fixing retrieval buys, and it is currently zero.
Anyone quoting only the optimistic band is quoting the assumption and not the
evidence.

Deterministic; no model calls, no network.
"""

from __future__ import annotations

import math
from typing import Any
from collections.abc import Iterable, Sequence

__all__ = [
    "WINDOW_A_B_CAUSAL",
    "bridge",
    "point_biserial",
    "spearman",
]

#: (improved, total) on the causal subset of the context-window A/B: the
#: questions whose gold-in-prompt actually flipped 0 -> 1 when the window was
#: widened, and whose binary correctness moved as a result.  Run three times
#: with identical results, so it is signal rather than jitter.
#:
#: Measured on 89 paired ``model_wrong`` questions with
#: ``poolside/laguna-s-2.1:free``; see the analysis doc, "Evidence presence
#: did not translate into answer quality".
WINDOW_A_B_CAUSAL: tuple[int, int] = (0, 7)

#: Rank cut-offs reported in ``ranker_upside``.  Matches the R@k reported
#: everywhere else in the harness so the bands line up with retrieval tables.
RANK_CUTOFFS: tuple[int, ...] = (5, 10, 20)

#: How ``gold_in_prompt`` partitions the population.  Three states, not two:
#: "gold absent from the pool" is a different problem from "gold present but
#: ranked below the window".
_IN_PROMPT_GROUPS = (
    ("in_prompt", lambda r: bool(r.get("gold_in_prompt"))),
    (
        "gold_in_pool_not_in_prompt",
        lambda r: not r.get("gold_in_prompt") and r.get("gold_depth") is not None,
    ),
    ("never_retrieved", lambda r: not r.get("gold_in_prompt") and r.get("gold_depth") is None),
)


# --------------------------------------------------------------------------- #
# Correlations
# --------------------------------------------------------------------------- #


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _pstdev(xs: Sequence[float]) -> float:
    """Population standard deviation.

    Population, not sample: the inputs are the complete observed population of
    questions, not a draw from a larger one, so the ``n-1`` correction would
    inflate the denominator and bias |r| low.  Pinned by
    ``tests/test_retrieval_answer_link.py::test_point_biserial_sign`` — the
    sample form yields -0.859 and fails the ``< -0.9`` assertion.
    """
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / len(xs))


def _ranks(xs: Sequence[float]) -> list[float]:
    """Average ranks, ties shared."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman rank correlation, or ``None`` when it is undefined.

    ``None`` for fewer than three paired observations or for a constant
    series — both are cases where any value would be an artifact, and
    reporting a number there is how a correlation table grows noise.
    """
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    sx, sy = _pstdev(rx), _pstdev(ry)
    if sx == 0.0 or sy == 0.0:
        return None
    mx, my = _mean(rx), _mean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    return round(cov / (len(rx) * sx * sy), 6)


def point_biserial(binary: Sequence[int], continuous: Sequence[float]) -> float | None:
    """Point-biserial correlation: a binary against a continuous measure.

    ``None`` when the binary is constant — then the correlation is genuinely
    undefined, not merely large, and callers must not substitute 0.
    """
    n = len(binary)
    if n == 0 or n != len(continuous):
        return None
    ones = [c for b, c in zip(binary, continuous, strict=True) if b]
    zeros = [c for b, c in zip(binary, continuous, strict=True) if not b]
    if not ones or not zeros:
        return None
    sd = _pstdev(continuous)
    if sd == 0.0:
        return None
    p = len(ones) / n
    q = 1.0 - p
    r = ((_mean(ones) - _mean(zeros)) / sd) * math.sqrt(p * q)
    return round(max(-1.0, min(1.0, r)), 6)


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def _is_human_correct(row: dict[str, Any]) -> bool:
    """True when the human audit did **not** record a model failure.

    ``model_wrong`` is the only verdict that indicts the model.  Everything
    else — ``evaluator_miss`` (the grader was wrong) and ``reference_narrow``
    — is a label problem, so folding those into "the model was right" would
    credit the model for the grader's mistakes.  That inversion is exactly
    the §5.3 artifact: 38 evaluator misses read as model error.
    """
    return str(row.get("verdict") or "") != "model_wrong"


def _agg(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts and rates over a set of attribution rows.

    Rates are ``None`` for an empty set rather than ``0.0``: "no questions"
    and "zero of N were correct" are different facts, and collapsing them
    makes an empty slice look like a total failure.
    """
    n = len(rows or [])
    if n == 0:
        return {
            "n": 0,
            "human_correct_rate": None,
            "binary_rate": None,
            "soft": None,
            "n_model_wrong": 0,
            "gold_in_prompt": 0,
        }
    return {
        "n": n,
        "human_correct_rate": round(sum(1 for r in rows if _is_human_correct(r)) / n, 6),
        "binary_rate": round(sum(float(r.get("binary") or 0.0) for r in rows) / n, 6),
        "soft": round(sum(float(r.get("soft") or 0.0) for r in rows) / n, 6),
        "n_model_wrong": sum(1 for r in rows if not _is_human_correct(r)),
        "gold_in_prompt": sum(1 for r in rows if r.get("gold_in_prompt")),
    }


# --------------------------------------------------------------------------- #
# Bridge
# --------------------------------------------------------------------------- #


def _promotable(rows: Sequence[dict[str, Any]], rank: int) -> list[str]:
    """Questions a ranker could plausibly fix at cut-off *rank*.

    ``model_wrong`` (trustworthy label) AND ``gold_depth > rank`` (reachable by
    reordering) AND not already in the prompt (something to fix).
    ``gold_depth is None`` never qualifies — no reordering reaches evidence
    that was not retrieved at all.
    """
    out = []
    for row in rows:
        if _is_human_correct(row):
            continue
        depth = row.get("gold_depth")
        if depth is None or row.get("gold_in_prompt"):
            continue
        if float(depth) > float(rank):
            out.append(str(row.get("qid") or ""))
    return out


def ranker_upside(
    rows: Sequence[dict[str, Any]],
    *,
    conversion_in_prompt: float | None,
    causal_conversion: float | None,
) -> dict[str, dict[str, Any]]:
    """Project a retrieval fix onto answer correctness, two ways.

    Args:
        rows: Attribution rows.
        conversion_in_prompt: binary rate on questions whose gold was already
            in the prompt.  Drives the optimistic band.
        causal_conversion: measured conversion from the window A/B — the rate
            at which handing the model a gold provision it had been missing
            actually changed the answer.  Drives the causal band.

    Returns:
        ``{f"R@{k}": {...}}`` for each cut-off in :data:`RANK_CUTOFFS`.
        ``projected_*`` is the projected change in *population* binary rate, so
        it is directly comparable to the measured ``binary_rate``.

    Both bands are the same formula — ``population_share * conversion`` — so
    they are directly comparable and the gap between them is a measurement,
    not an artifact of two unrelated formulas.  The *expected* ordering
    (``causal <= optimistic``) is an assumption about the two inputs, not a
    property of the formula: both are floats and nothing stops a caller
    passing them the wrong way round.  An inversion is recorded in
    ``band_ordering`` rather than silently producing a "causal" band above the
    "optimistic" one, which would invert the module's conclusion.
    """
    total = len(rows or [])
    inverted = (
        conversion_in_prompt is not None and causal_conversion is not None and causal_conversion > conversion_in_prompt
    )
    bands: dict[str, dict[str, Any]] = {}
    for rank in RANK_CUTOFFS:
        promotable = _promotable(rows, rank)
        share = (len(promotable) / total) if total else 0.0
        bands[f"R@{rank}"] = {
            "rank": rank,
            "promotable_model_wrong": len(promotable),
            "promotable_qids": promotable,
            "population_share": round(share, 6),
            "conversion_in_prompt": conversion_in_prompt,
            "causal_conversion": causal_conversion,
            "projected_binary_gain_optimistic": (
                round(share * conversion_in_prompt, 6) if conversion_in_prompt is not None else None
            ),
            "projected_binary_gain_causal": (
                round(share * causal_conversion, 6) if causal_conversion is not None else None
            ),
            "band_ordering": "inverted" if inverted else "as_expected",
        }
    bands["conversion_source"] = {
        "conversion_in_prompt": "observed binary rate where gold was already in the prompt",
        "causal_conversion": f"window A/B causal subset: {WINDOW_A_B_CAUSAL[0]}/{WINDOW_A_B_CAUSAL[1]}",
        "band_ordering": "inverted" if inverted else "as_expected",
        "note": (
            "Optimistic assumes a gained provision converts as well as one the "
            "model already had. The causal band uses the measured rate, which is "
            "0/7 across three runs. Quote the causal band."
            + (
                " WARNING: causal_conversion exceeds conversion_in_prompt, so the "
                "'causal' band is above the 'optimistic' one. The inputs are "
                "almost certainly swapped."
                if inverted
                else ""
            )
        ),
    }
    return bands


def bridge(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Join gold depth to answer correctness.

    Returns:
        ``binary_by_gold_in_prompt`` — correctness conditioned on where the
        gold evidence sat, which is what separates a retrieval failure from a
        generation failure.
        ``ranker_upside`` — the dual band described in the module docstring.
        ``by_bucket_model_wrong`` / ``n_model_wrong_joined`` — how the model's
        failures distribute across stages.  Restricted to ``model_wrong`` so
        the buckets describe the model rather than the grader.
        ``aggregate`` — over the whole population.
    """
    rows = list(rows or [])
    overall = _agg(rows)

    by_presence = {name: _agg([r for r in rows if predicate(r)]) for name, predicate in _IN_PROMPT_GROUPS}

    model_wrong = [r for r in rows if not _is_human_correct(r)]
    by_bucket: dict[str, Any] = {}
    for row in model_wrong:
        by_bucket.setdefault(str(row.get("bucket") or "unattributed"), []).append(row)
    by_bucket = {name: _agg(group) for name, group in sorted(by_bucket.items())}

    improved, total = WINDOW_A_B_CAUSAL
    causal_conversion = (improved / total) if total else None
    conversion_in_prompt = by_presence["in_prompt"]["binary_rate"]

    return {
        "aggregate": overall,
        "binary_by_gold_in_prompt": by_presence,
        "by_bucket_model_wrong": by_bucket,
        "n_model_wrong_joined": len(model_wrong),
        "ranker_upside": ranker_upside(
            rows,
            conversion_in_prompt=conversion_in_prompt,
            causal_conversion=causal_conversion,
        ),
    }
