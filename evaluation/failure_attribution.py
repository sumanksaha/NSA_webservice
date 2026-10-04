"""Why do the audited model_wrong questions fail? Measured, not inferred.

The analysis doc attributes the residual failures to interpretation and
application ("retrieved legal evidence -> interpretation -> application").
That is an inference from failure volume. This attributes them by measuring
where the gold evidence actually sits in the cached candidate pool.

Three buckets, by how deep the first gold hit ranks:

  ``retrieval_rank``   gold is in the pool but below the context window —
                       a ranking problem the reranker can fix
  ``generation``       gold is inside the window and the answer is still
                       wrong — an interpretation/application problem
  ``never_retrieved``  gold never appears in the pool at all

The split matters because the two buckets have opposite fixes. Raising the
context window helps the first and dilutes the second (which is what the P0-1
A/B observed). Picking a fix without this split is how effort gets spent on
the wrong bucket.

Usage:
    python -m evaluation.failure_attribution
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.bench_p0_1_packing import RAW_DIR, _load_jsonl, _load_payload_index
from evaluation.benchmark import load_questions
from evaluation.resolution import FamilyMap, matches_gold

TABULATION = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "full_review_tabulation.json"
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "failure_attribution.json"
ARM = "C_hybrid"

#: Effective context windows actually used. ``ContextBuilder._QUERY_TYPE_BUDGETS``
#: overrides RAG_CONTEXT_MAX_CHUNKS for every typed query, so the .env value of
#: 20 is NOT what most queries get.
EFFECTIVE_K = {
    "prohibition": 8,
    "definition": 8,
    "penalty": 10,
    "general": 10,
    "procedure": 10,
    "cross_reference": 12,
    "case_law": 12,
}


def _gold_in_prompt(qid: str, question, arms: dict, payload_index: dict, fm) -> bool:
    """Whether the gold provision actually survives ContextBuilder for this qid.

    This asks the real builder, not an assumed K. An earlier revision bucketed
    on a hardcoded rank<=10, which understated the window: most benchmark query
    types ("Obligation", "Authority", "Direct provision", ...) never match a
    _QUERY_TYPE_BUDGETS key, so they already used the configured 20-chunk
    ceiling. Only 74 of 284 query-type entries match the table.
    """
    from evaluation.bench_p0_1_packing import _chunks_for
    from app.rag.generation.context_builder import ContextBuilder

    ids = arms.get(qid) or []
    chunks = _chunks_for(ids, payload_index)
    if not chunks:
        return False
    qt = question.question_types[0] if question.question_types else ""
    built = ContextBuilder(query_type=qt).build(question.question, chunks, qt)
    admitted = {c["chunk_id"] for c in built.citations}
    units = question.recall_units()
    return any(
        cid in payload_index and any(matches_gold(payload_index[cid], u, fm) for u in units) for cid in admitted
    )


def _gold_depth(chunk_ids: list[str], payload_index: dict, units, fm) -> int | None:
    for i, cid in enumerate(chunk_ids, start=1):
        payload = payload_index.get(cid)
        if payload is None:
            continue
        for u in units:
            try:
                if matches_gold(payload, u, fm):
                    return i
            except Exception:
                continue
    return None


def main() -> int:
    if not TABULATION.exists():
        print(f"missing: {TABULATION}", file=sys.stderr)
        return 1

    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}
    fm = FamilyMap()
    arms = {
        r["question_id"]: [str(c) for c in (r.get("chunk_ids") or [])] for r in _load_jsonl(RAW_DIR / f"{ARM}.jsonl")
    }
    tab = json.loads(TABULATION.read_text(encoding="utf-8"))

    per_q: list[dict[str, Any]] = []
    for rec in tab.get("records") or []:
        qid, verdict = rec.get("qid"), rec.get("verdict")
        if not qid or not verdict:
            continue
        q, ids = questions.get(qid), arms.get(qid)
        if not q or not ids:
            continue
        depth = _gold_depth(ids, payload_index, q.recall_units(), fm)
        in_prompt = _gold_in_prompt(qid, q, arms, payload_index, fm)
        if depth is None:
            bucket = "never_retrieved"
        elif in_prompt:
            bucket = "generation"
        else:
            bucket = "retrieval_rank"
        per_q.append({"qid": qid, "verdict": verdict, "model_action": rec.get("model_action"),
            "gold_depth": depth, "gold_in_prompt": in_prompt, "bucket": bucket,
            "query_types": q.question_types,
        })

    mw = [r for r in per_q if r["verdict"] == "model_wrong"]
    counts: dict[str, int] = defaultdict(int)
    for r in mw:
        counts[r["bucket"]] += 1

    depths = [r["gold_depth"] for r in mw if r["gold_depth"]]
    recall = {}
    for k in (5, 10, 20, 50, 100, 150):
        hit = sum(1 for r in mw if r["gold_depth"] and r["gold_depth"] <= k)
        recall[f"R@{k}"] = {"hits": hit, "n": len(mw), "rate": round(hit / max(len(mw), 1), 4)}

    act = defaultdict(lambda: defaultdict(int))
    for r in mw:
        act[r["model_action"]][r["bucket"]] += 1

    out = {
        "version": "failure-attribution-1",
        "date": "2026-10-04",
        "arm": ARM,
        "method": (
            "For each audited model_wrong question, find the rank of the first gold "
            "chunk in the cached candidate pool. Rank<=10 = evidence was in the "
            "prompt window and the answer was still wrong (generation); rank>10 = "
            "evidence was retrievable but not surfaced (retrieval ranking)."
        ),
        "limits": [
            "Measures presence of gold evidence, not whether the model USED it — a "
            "question can have gold in the window and still fail for reading reasons.",
            "Uses the C_hybrid cached arm; other arms were not re-measured.",
            "Effective K differs from RAG_CONTEXT_MAX_CHUNKS because "
            "ContextBuilder._QUERY_TYPE_BUDGETS overrides it per query type.",
        ],
        "model_wrong_total": len(mw),
        "buckets": {
            "retrieval_rank": counts.get("retrieval_rank", 0),
            "generation": counts.get("generation", 0),
            "never_retrieved": counts.get("never_retrieved", 0),
        },
        "bucket_share": {k: round(v / max(len(mw), 1), 4) for k, v in counts.items()},
        "recall_by_k": recall,
        "gold_depth_stats": {
            "found_in_pool": len(depths),
            "median": statistics.median(depths) if depths else None,
            "p90": sorted(depths)[int(len(depths) * 0.9) - 1] if depths else None,
        },
        "effective_context_windows": EFFECTIVE_K,
        "by_model_action": {a: dict(b) for a, b in act.items()},
        "per_question": per_q,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 76)
    print(f"Why the {len(mw)} model_wrong questions fail  (arm {ARM})")
    print("=" * 76)
    for b in ("retrieval_rank", "generation", "never_retrieved"):
        n = counts.get(b, 0)
        print(f"  {b:<18} {n:>4}  {n / max(len(mw), 1):>6.1%}")
    print()
    print("  recall of gold evidence in the candidate pool:")
    for k, v in recall.items():
        print(f"    {k:<7} {v['hits']:>3}/{v['n']}  {v['rate']:>6.1%}")
    print()
    print("  by audit model_action (bucket counts):")
    for a, b in sorted(act.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"    {a!s:<24} {dict(b)}")
    print()
    print(f"  gold depth when found: median={out['gold_depth_stats']['median']}, p90={out['gold_depth_stats']['p90']}")
    print()
    for lim in out["limits"]:
        print(f"  ! {lim}")
    print()
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
