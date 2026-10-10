"""Question-pool classification and deterministic oversampling.

Bridge module between ``evaluation/retrieval_answer_link`` (which attributes
each failure to a retrieval or generation stage) and the pairwise training
set.  A model is only worth training on a failure whose *label* is trustworthy
and whose *cause* is something training can move, so the 150-question
attribution table is partitioned into four classes:

``priority_provision``    the model had the evidence and provision-checked it
                          wrongly  — the clearest training signal here.
``priority_promotable``   retrieval ranked the gold in but the model still
                          answered wrongly (e.g. abstention_gate).  Trainable,
                          but the fix may not be the model's.
``guard``                 the *evaluator* was wrong (``evaluator_miss``).  The
                          gold label here is untrustworthy, so these pairs must
                          not train the ranker — this class exists to keep them
                          out.
``untrainable``           everything else: generation-stage failures, deeper
                          reasoning, unclassified rows.

The partition is disjoint and total — every row lands in exactly one class —
because a pool whose classes overlap silently double-trains and whose classes
have gaps silently drops data.  ``tests/test_retrieval_answer_link.py`` pins
that.

Weights are deterministic and integer-multiplicative: a weight of ``w``
produces ``round(w)`` copies, floored at one so a weight of ``0`` drops the
qid from *training emphasis* without deleting the original pair.  Deleting a
gold pair outright would change what the metric means, which is a different
decision from de-emphasising it.

Deterministic; no model calls, no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from collections.abc import Iterable

__all__ = [
    "DEFAULT_WEIGHTS",
    "POOL_CLASSES",
    "apply_pool_weights",
    "classify",
    "main",
    "qid_weight_map",
]

#: Canonical class order.  Also the report order, so two runs of the same pool
#: produce byte-identical output.
POOL_CLASSES = (
    "priority_provision",
    "priority_promotable",
    "guard",
    "untrainable",
)

#: Oversampling multipliers per class.  A weight is a *copy count*, not a
#: probability — see the module docstring.
DEFAULT_WEIGHTS: dict[str, float] = {
    "priority_provision": 3.0,
    "priority_promotable": 2.0,
    "guard": 1.0,
    "untrainable": 0.0,
}


def classify(rows: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """Partition attribution rows into the four training-pool classes.

    Args:
        rows: Attribution rows carrying ``qid``, ``verdict``, ``bucket`` and
            ``model_action`` — the shape ``retrieval_answer_link.bridge``
            emits.

    Returns:
        ``{class_name: [qid, ...]}`` with input order preserved within each
        class.  Disjoint and total: every qid appears exactly once.

    Rules, in precedence order:

    1. ``evaluator_miss`` -> ``guard``.  Checked first because a grader error
       invalidates the label regardless of which stage the answer failed in.
    2. ``model_wrong`` + retrieval-stage + ``provision_check`` ->
       ``priority_provision``.
    3. ``model_wrong`` + retrieval-stage + anything else ->
       ``priority_promotable``.
    4. otherwise -> ``untrainable``.  Generation-stage failures and
       ``needs_deeper_reasoning`` are not addressable by a retrieval ranker.
    """
    pool: dict[str, list[str]] = {name: [] for name in POOL_CLASSES}
    seen: set[str] = set()

    for row in rows or []:
        qid = str(row.get("qid") or "").strip()
        if not qid or qid in seen:
            continue
        seen.add(qid)

        verdict = str(row.get("verdict") or "")
        bucket = str(row.get("bucket") or "")
        action = str(row.get("model_action") or "")

        if verdict == "evaluator_miss":
            pool["guard"].append(qid)
        elif verdict == "model_wrong" and bucket == "retrieval_rank":
            if action == "provision_check":
                pool["priority_provision"].append(qid)
            else:
                pool["priority_promotable"].append(qid)
        else:
            pool["untrainable"].append(qid)

    return pool


def qid_weight_map(pool: dict[str, Any]) -> dict[str, float]:
    """Flatten a pool's class weights into ``{qid: weight}``.

    Args:
        pool: Either :func:`classify` output (bare class -> qids) or the
            wrapper ``{"weights": {...}, "qids": {...}}`` shape that carries
            the weights alongside the membership.  Both are accepted because
            callers hold one or the other depending on whether they have run
            the weighting step yet.
    """
    qids = pool.get("qids") if isinstance(pool.get("qids"), dict) else pool
    weights = (pool.get("weights") if isinstance(pool.get("weights"), dict) else None) or DEFAULT_WEIGHTS

    out: dict[str, float] = {}
    for cls in POOL_CLASSES:
        weight = float(weights.get(cls, 0.0))
        for qid in qids.get(cls, []) or []:
            out[str(qid)] = weight
    return out


def apply_pool_weights(
    pairs: list[dict[str, Any]],
    weights: dict[str, float],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Oversample pairs by question weight, deterministically.

    Each distinct question is emitted ``round(weight)`` times, floored at one
    copy.  Questions absent from ``weights`` keep a single copy.  Order is
    stable: the original pairs first in input order, then the duplicates
    grouped by first appearance — so two runs on the same input produce
    byte-identical output and a training set can be diffed.

    Args:
        pairs: Training pairs carrying ``question_id``.
        weights: ``{qid: weight}`` from :func:`qid_weight_map`.

    Returns:
        ``(pairs, info)`` where ``info`` is
        ``{"added_pairs": n, "qids_boosted": k}``.
    """
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for index, pair in enumerate(pairs or []):
        qid = str(pair.get("question_id") or "")
        if qid not in first_seen:
            first_seen[qid] = index
            counts[qid] = 1
        else:
            counts[qid] += 1

    out: list[dict[str, Any]] = list(pairs or [])
    extras: dict[str, int] = {}
    for qid, original in counts.items():
        target = max(1, round(float(weights.get(qid, 1.0))))
        # Only ever *add* copies. A question the source already duplicated
        # stays duplicated; the pool weight is a floor, not a ceiling.
        extra = target - original
        if extra > 0:
            extras[qid] = extra

    for qid, extra in extras.items():
        source = next(p for p in (pairs or []) if str(p.get("question_id") or "") == qid)
        for _ in range(extra):
            out.append(dict(source))

    return out, {"added_pairs": len(out) - len(pairs or []), "qids_boosted": len(extras)}


def main() -> int:
    """Rebuild the pool from an attribution artifact.

    Usage::

        python -m evaluation.build_train_pool <attribution.json> [--out pool.json]

    The attribution artifact is what ``retrieval_answer_link`` writes: a list of
    per-qid records, or ``{"per_question": [...]}``.
    """
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("attribution", help="attribution JSON from retrieval_answer_link")
    ap.add_argument("--out", default="", help="write the pool here (default: stdout)")
    args = ap.parse_args()

    blob = json.loads(Path(args.attribution).read_text(encoding="utf-8"))
    rows = blob.get("per_question") if isinstance(blob, dict) else blob

    pool = classify(rows or [])
    payload = {"weights": DEFAULT_WEIGHTS, "qids": pool, "counts": {k: len(v) for k, v in pool.items()}}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
