"""Calibrate the decision selector: sweep τ / top-k over cached pools.

Plan step 2.  For every benchmark question with gold units it:

1. builds the cached ``C_hybrid`` retrieval pool,
2. dedupes it into the candidate reservoir (``merge_reservoir``, the same
   function the shipped seam uses),
3. scores the reservoir ONCE with the decision model (the sanctioned
   ``typesafe/jev-1.13``) via :mod:`evaluation.decision_scores`,
   caching ``{chunk_id: P(keep)}``,
4. re-derives the selection at each (τ, top-k) with the shipped pure walk
   ``select_from_scores`` — so what is calibrated is exactly what is served,
5. scores each setting against gold evidence (unit-level precision / recall /
   F1, gold-in-prompt rate, abstains, duplicate-unit rate, kept size).

The score cache makes the sweep free after the first pass: rerun with
different τ grids without another model call.

Usage:
    python -m evaluation.tau_sweep                    # full grid, all questions
    python -m evaluation.tau_sweep --limit 20         # smoke run
    python -m evaluation.tau_sweep --refresh          # re-score (model drift check)
    python -m evaluation.tau_sweep --model typesafe/jev-1.13   # the other sanctioned model

Writes ``evaluation/out/decision/tau_sweep_<model>_v1.json`` (per model —
one file per scorer) and prints a table.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.decision_scores import (  # noqa: E402
    ALLOWED_DECISION_MODELS,
    DEFAULT_ARM,
    decision_model,
    ensure_scores,
    gold_chunk_ids,
    load_pools,
    model_wrong_qids,
)

def out_file(model: str):
    """Per-model output path — one calibration file per scorer."""
    slug = model.replace("/", "_").replace(":", "-")
    return PROJECT_ROOT / "evaluation" / "out" / "decision" / f"tau_sweep_{slug}_v1.json"

DEFAULT_TAUS = (0.3, 0.4, 0.5, 0.6, 0.7)
DEFAULT_TOPKS = (6,)


def _unit_rate(kept: list[str], gold: set[str]) -> tuple[float, float, float]:
    """(precision, recall, f1) of the kept set against pool-restricted gold."""
    if not kept and not gold:
        return 1.0, 1.0, 1.0
    hit = len([c for c in kept if c in gold])
    precision = hit / len(kept) if kept else 0.0
    recall = hit / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--taus", nargs="+", type=float, default=list(DEFAULT_TAUS))
    ap.add_argument("--top-k", nargs="+", type=int, default=list(DEFAULT_TOPKS))
    ap.add_argument("--reservoir", type=int, default=50, help="candidate reservoir (RAG_DECISION_RESERVOIR)")
    ap.add_argument("--scope", choices=("all", "model_wrong"), default="all")
    ap.add_argument("--arm", default=DEFAULT_ARM)
    ap.add_argument(
        "--model",
        default=None,
        help=f"decision model to score with (default: RAG_DECISION_MODEL or typesafe/jev-1.13); one of {ALLOWED_DECISION_MODELS}",
    )
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true", help="re-score every pool (model drift check)")
    args = ap.parse_args()
    if args.model is not None and args.model not in ALLOWED_DECISION_MODELS:
        ap.error(f"--model must be one of {ALLOWED_DECISION_MODELS}")

    from app.rag.retrieval.decision_selector import merge_reservoir, select_from_scores
    from evaluation.resolution import FamilyMap

    pools, questions, payload_index = load_pools(args.arm)
    family_map = FamilyMap()
    mw = model_wrong_qids()

    qids = [
        qid
        for qid in sorted(pools)
        if qid in questions
        and len(pools[qid]) >= 2
        and questions[qid].recall_units()
        and (args.scope == "all" or qid in mw)
    ]
    if args.limit:
        qids = qids[: args.limit]
    if not qids:
        print("no questions in scope", file=sys.stderr)
        return 1

    # The reservoir is what gets scored (never the full 500-chunk pool —
    # that would blow past max_tokens and truncate the keep-map).
    reservoirs: dict[str, list] = {qid: merge_reservoir(pools[qid], args.reservoir) for qid in qids}

    sel_model = args.model or decision_model()
    print(f"scoring {len(qids)} pools with {sel_model} (cached ones are reused)...", flush=True)
    cache, n_scored = ensure_scores(reservoirs, questions, qids=qids, refresh=args.refresh, model=sel_model)
    print(f"scored {n_scored} now; {len(qids) - n_scored} from cache", flush=True)

    golds: dict[str, set[str]] = {
        qid: gold_chunk_ids(questions[qid], pools[qid], payload_index, family_map) for qid in qids
    }

    scored_qids = [qid for qid in qids if cache.get(qid) and not cache[qid].get("error")]
    errors = [qid for qid in qids if not cache.get(qid) or cache[qid].get("error")]
    # Precision/recall are only meaningful where the pool actually contains
    # gold (achievable recall); questions without gold in pool are counted,
    # not averaged into a guaranteed-zero row.
    calib_qids = [qid for qid in scored_qids if golds[qid]]
    print(
        f"{len(scored_qids)} scorable ({len(calib_qids)} with gold in pool), "
        f"{len(errors)} errored/skipped",
        file=sys.stderr,
    )

    rows: list[dict[str, Any]] = []
    for qid in calib_qids:
        reservoir = reservoirs[qid]
        scores = cache[qid]["scores"]
        probs = {i: scores[c.chunk_id] for i, c in enumerate(reservoir) if c.chunk_id in scores}
        for tau in args.taus:
            for k in args.top_k:
                _evidence, core = select_from_scores(questions[qid].question, reservoir, probs, threshold=tau, top_k=k)
                kept = [c for c in (core["kept"] or []) if c]
                p, r, f1 = _unit_rate(kept, golds[qid])
                rows.append(
                    {
                        "qid": qid,
                        "tau": tau,
                        "top_k": k,
                        "kept": len(kept),
                        "abstain": int(core["abstain"]),
                        "gold_in_prompt": int(bool(set(kept) & golds[qid])),
                        "precision": round(p, 4),
                        "recall": round(r, 4),
                        "f1": round(f1, 4),
                    }
                )

    # Heuristic reference (select_evidence_set over the same reservoirs) —
    # the fail-closed fallback and the yardstick: the decision model must
    # beat this to justify its call cost.
    from app.rag.retrieval.evidence_selector import select_evidence_set

    ref_rows = []
    for qid in calib_qids:
        try:
            es = select_evidence_set(questions[qid].question, reservoirs[qid])
            kept = list(es.chunk_ids or [])
        except Exception:
            kept = []
        p, r, f1 = _unit_rate(kept, golds[qid])
        ref_rows.append(
            {
                "kept": len(kept),
                "gold_in_prompt": int(bool(set(kept) & golds[qid])),
                "precision": round(p, 4),
                "recall": round(r, 4),
                "f1": round(f1, 4),
            }
        )
    heuristic_ref = {
        "n": len(ref_rows),
        "precision": round(statistics.mean(x["precision"] for x in ref_rows), 4) if ref_rows else None,
        "recall": round(statistics.mean(x["recall"] for x in ref_rows), 4) if ref_rows else None,
        "f1": round(statistics.mean(x["f1"] for x in ref_rows), 4) if ref_rows else None,
        "gold_in_prompt_rate": round(statistics.mean(x["gold_in_prompt"] for x in ref_rows), 4) if ref_rows else None,
        "mean_kept": round(statistics.mean(x["kept"] for x in ref_rows), 2) if ref_rows else None,
    }

    grid: list[dict[str, Any]] = []
    for tau in args.taus:
        for k in args.top_k:
            sel = [x for x in rows if x["tau"] == tau and x["top_k"] == k]
            if not sel:
                continue
            grid.append(
                {
                    "tau": tau,
                    "top_k": k,
                    "n": len(sel),
                    "precision": round(statistics.mean(x["precision"] for x in sel), 4),
                    "recall": round(statistics.mean(x["recall"] for x in sel), 4),
                    "f1": round(statistics.mean(x["f1"] for x in sel), 4),
                    "gold_in_prompt_rate": round(statistics.mean(x["gold_in_prompt"] for x in sel), 4),
                    "mean_kept": round(statistics.mean(x["kept"] for x in sel), 2),
                    "abstain_rate": round(statistics.mean(x["abstain"] for x in sel), 4),
                }
            )
    if not grid:
        print("ABORT: nothing scored", file=sys.stderr)
        return 1

    # Recommend: max evidence F1; ties break on gold-in-prompt, then fewer abstains.
    best = max(grid, key=lambda g: (g["f1"], g["gold_in_prompt_rate"], -g["abstain_rate"]))

    out = {
        "model": cache[scored_qids[0]]["model"] if scored_qids else None,
        "resolved_models": sorted({cache[q]["resolved_model"] for q in scored_qids if cache[q].get("resolved_model")}),
        "scope": args.scope,
        "arm": args.arm,
        "reservoir": args.reservoir,
        "n_questions": len(qids),
        "n_scored": len(scored_qids),
        "n_gold_in_pool": len(calib_qids),
        "n_errors": len(errors),
        "error_qids": errors,
        "grid": grid,
        "heuristic_reference": heuristic_ref,
        "recommended": {"tau": best["tau"], "top_k": best["top_k"], "f1": best["f1"]},
        "notes": [
            "precision/recall are unit-level against pool-restricted gold (achievable recall).",
            "gold_in_prompt_rate counts questions where ANY kept chunk resolves to a gold unit.",
            "The shipped select_from_scores walk produced every selection — no reimplementation.",
            "Duplicate-unit rate is 0 by construction (reservoir + walk both dedupe); not reported.",
        ],
    }

    OUT_PATH = out_file(sel_model)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=" * 76)
    print(f"Decision-selector tau sweep  scope={args.scope}  n={len(calib_qids)}  model={out['model']}")
    print("=" * 76)
    print(f"{'tau':>5} {'k':>3} {'precision':>10} {'recall':>8} {'f1':>8} {'gold_in@':>9} {'kept':>6} {'abstain':>8}")
    print("-" * 60)
    for g in grid:
        mark = " <-- recommended" if (g["tau"], g["top_k"]) == (best["tau"], best["top_k"]) else ""
        print(
            f"{g['tau']:>5.2f} {g['top_k']:>3} {g['precision']:>10.4f} {g['recall']:>8.4f} "
            f"{g['f1']:>8.4f} {g['gold_in_prompt_rate']:>9.4f} {g['mean_kept']:>6.2f} {g['abstain_rate']:>8.4f}{mark}"
        )
    print()
    if heuristic_ref.get("f1") is not None:
        print(
            f"heuristic reference (select_evidence_set, same reservoirs): f1={heuristic_ref['f1']} "
            f"precision={heuristic_ref['precision']} recall={heuristic_ref['recall']} "
            f"gold_in@={heuristic_ref['gold_in_prompt_rate']} kept={heuristic_ref['mean_kept']}"
        )
    print()
    print(f"written: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
