"""CE V2 ERROR ANALYSIS - per-query regression table + failure taxonomy (Step 0 rebuild).

Rebuild of the deleted scratch script whose output is preserved in
``evaluation/out/ce_v2_error_analysis.log`` (the original crashed with a
cp1252 ``UnicodeEncodeError`` on the non-ASCII delta glyph; this version emits
ASCII only).  Committed, reproducible, and shares the score cache written by
``evaluation.ce_v2_eval`` so it does not re-score.

Per-query table:  QID Dom Diff Pairs V1_rk V2_rk Type RetFail RerankFail
  - Dom      = majority gold-unit family of the question's test pairs
  - Diff     = normalized benchmark difficulty (the original's easy/medium
               labels are unrecoverable - see ce_v2_eval docstring)
  - V1_rk/V2_rk = rank of the best-ranked gold chunk (1-based) per model
  - Type     = v2-based failure classification:
                 correct               v2 puts a gold at rank 1
                 same_section_hard_neg rank-1 non-gold shares a section with a gold
                 hierarchy_version     rank-1 non-gold is same family + adjacent
                                       section or same document
                 other                 anything else
  - RetFail  = X when a gold chunk is absent from the candidate pool
  - RerankFail = X when no gold is in the model's top-10

Failure taxonomy: per-category counts, % of total/failures, mean R@10/MRR/nDCG
delta (v2 - v1) over the category's queries, and V2+ / V2- (queries where v2's
MRR@10 improved / regressed vs v1).

Output: evaluation/out/cache/ce_v2_error_analysis.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.config import CACHE_DIR

from evaluation.ce_v2_eval import (
    MODELS,
    _MODEL_OVERRIDES,
    _score_key,
    build_candidates,
    difficulty_of,
    domain_of,
    load_mining,
    load_questions,
    load_test_pairs,
    ranking_metrics,
    score_pairs_cached,
)

ERR_OUT = CACHE_DIR / "ce_v2_error_analysis.json"
PAYLOAD_INDEX_FILE = CACHE_DIR / "payload_index.jsonl"

RERANK_K = 10


# --------------------------------------------------------------------------- #
# Payload index + failure classification
# --------------------------------------------------------------------------- #
def load_payload_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if PAYLOAD_INDEX_FILE.exists():
        for line in PAYLOAD_INDEX_FILE.open(encoding="utf-8"):
            line = line.strip()
            if line:
                rec = json.loads(line)
                index[str(rec.get("id") or "")] = rec.get("payload") or {}
    return index


def _norm_sec(value: Any) -> str | None:
    """Normalize a section value to its base number ('16(2)(ii)' -> '16')."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    import re

    m = re.match(r"(\d{1,4})", s)
    return m.group(1) if m else None


def _families_of(act_name: str, family_map: Any) -> set[str]:
    source = str(act_name or "")
    if not source:
        return set()
    try:
        return set(family_map.family_s_for_act(source))
    except Exception:
        return set()


def classify_failure(
    top_cid: str,
    gold_cids: list[str],
    payload_index: dict[str, dict[str, Any]],
    family_map: Any,
) -> str:
    """Classify a non-correct v2 outcome using the rank-1 non-gold chunk."""
    top_payload = payload_index.get(top_cid)
    if not top_payload:
        return "other"
    top_sec = _norm_sec(top_payload.get("section_number"))
    top_fams = _families_of(
        top_payload.get("act_name") or top_payload.get("document_title"), family_map
    )
    top_doc = str(top_payload.get("document_title") or "").lower()

    for cid in gold_cids:
        gp = payload_index.get(cid)
        if not gp:
            continue
        g_sec = _norm_sec(gp.get("section_number"))
        g_fams = _families_of(gp.get("act_name") or gp.get("document_title"), family_map)
        same_family = bool(top_fams & g_fams)
        same_section = bool(top_sec and g_sec and top_sec == g_sec)
        same_doc = bool(top_doc and str(gp.get("document_title") or "").lower() == top_doc)

        # Section proximity: 1.0 same, 0.7 adjacent (<=2), 0.4 (<=5), 0.1 else.
        proximity = 0.0
        if top_sec and g_sec:
            try:
                diff = abs(int(top_sec) - int(g_sec))
                proximity = 1.0 if diff == 0 else 0.7 if diff <= 2 else 0.4 if diff <= 5 else 0.1
            except ValueError:
                proximity = 0.0

        if same_family and same_section:
            return "same_section_hard_neg"
        if same_family and (proximity >= 0.7 or same_doc):
            return "hierarchy_version"
    return "other"


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _fmt(x: float) -> str:
    return f"{x:.4f}"


def main() -> int:
    from evaluation.resolution import FamilyMap

    parser = argparse.ArgumentParser(description="CE v2 per-query error analysis (Step 0 rebuild)")
    parser.add_argument("--model-v1", type=Path, default=None,
                        help="Override the v1 (frozen control) model directory")
    parser.add_argument("--model-v2", type=Path, default=None,
                        help="Override the v2 (candidate) model directory")
    args = parser.parse_args()
    for key, arg in (("v1", args.model_v1), ("v2", args.model_v2)):
        if arg is not None:
            _MODEL_OVERRIDES[key] = arg

    pairs = load_test_pairs()
    questions = load_questions()
    mining = load_mining()
    if not pairs:
        return 1

    # Group test pairs by question.
    pairs_by_qid: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        pairs_by_qid[p["question_id"]].append(p)
    test_qids = sorted(pairs_by_qid)
    pair_count = Counter(p["question_id"] for p in pairs)

    # Score via the shared cache (scores if the eval script has not run).
    scores: dict[str, dict[str, float]] = {}
    for key in MODELS:
        scores[key] = score_pairs_cached(pairs, key)

    cands = build_candidates(pairs_by_qid, mining, mode="pairs")
    payload_index = load_payload_index()
    family_map = FamilyMap()

    per_query: list[dict[str, Any]] = []
    for qid in test_qids:
        cand = cands[qid]
        gold_cids = [cid for cid, _t in cand["golds"]]
        gold_ids = cand["golds_ids"]
        q = questions.get(qid)
        query = q.question if q is not None else pairs_by_qid[qid][0]["query"]

        fams = Counter(domain_of(p) for p in pairs_by_qid[qid])
        dom = fams.most_common(1)[0][0] if fams else "?"

        # Ranked orders per model.
        orders: dict[str, list[str]] = {}
        mets: dict[str, dict[str, float]] = {}
        for key in MODELS:
            m = ranking_metrics(qid, cand, scores[key], query)
            mets[key] = m
            ranked = []
            for cid, text in cand["candidates"]:
                sc = scores[key].get(
                    _score_key({"question_id": qid}, "pos" if cid in gold_ids else "neg", text)
                )
                if sc is not None:
                    ranked.append((sc, cid))
            ranked.sort(key=lambda x: x[0], reverse=True)
            orders[key] = [cid for _sc, cid in ranked]

        def best_rank(order: list[str]) -> int | None:
            for i, cid in enumerate(order):
                if cid in gold_ids:
                    return i + 1
            return None

        v1_rk = best_rank(orders["v1"])
        v2_rk = best_rank(orders["v2"])

        ret_fail = "-" if gold_cids else "X"
        rerank_fail = "-"
        for key in MODELS:
            if not any(cid in gold_ids for cid in orders[key][:RERANK_K]):
                rerank_fail = "X"

        # v2-based classification.
        if v2_rk == 1:
            qtype = "correct"
        else:
            top = orders["v2"][0] if orders["v2"] else None
            qtype = classify_failure(top, gold_cids, payload_index, family_map) if top else "other"

        per_query.append({
            "qid": qid,
            "domain": dom,
            "difficulty": difficulty_of(q),
            "pairs": pair_count[qid],
            "v1_rank": v1_rk,
            "v2_rank": v2_rk,
            "type": qtype,
            "ret_fail": ret_fail,
            "rerank_fail": rerank_fail,
            "n_candidates": len(cand["candidates"]),
            "n_gold": len(gold_cids),
            "metrics": {
                key: {"r10": mets[key]["r_at"].get(10, 0), "mrr": mets[key]["mrr"],
                      "ndcg": mets[key]["ndcg"]}
                for key in MODELS
            },
        })

    # -------- failure taxonomy --------
    failures = [r for r in per_query if r["v2_rank"] != 1]
    n_fail = len(failures)
    n_total = len(per_query)
    cats: list[str] = ["correct", "hierarchy_version", "same_section_hard_neg", "other"]
    taxonomy: list[dict[str, Any]] = []
    for cat in cats:
        recs = [r for r in per_query if r["type"] == cat]
        if not recs:
            taxonomy.append({"category": cat, "count": 0, "pct_total": 0.0, "pct_fail": 0.0,
                             "r10_delta": 0.0, "mrr_delta": 0.0, "ndcg_delta": 0.0,
                             "v2_plus": 0, "v2_minus": 0})
            continue
        d = {"r10": [], "mrr": [], "ndcg": []}
        plus = minus = 0
        for r in recs:
            m1, m2 = r["metrics"]["v1"], r["metrics"]["v2"]
            d["r10"].append(m2["r10"] - m1["r10"])
            d["mrr"].append(m2["mrr"] - m1["mrr"])
            d["ndcg"].append(m2["ndcg"] - m1["ndcg"])
            if m2["mrr"] > m1["mrr"]:
                plus += 1
            elif m2["mrr"] < m1["mrr"]:
                minus += 1
        taxonomy.append({
            "category": cat,
            "count": len(recs),
            "pct_total": round(len(recs) / n_total, 4) if n_total else 0.0,
            "pct_fail": round(len(recs) / n_fail, 4) if n_fail else 0.0,
            "r10_delta": round(sum(d["r10"]) / len(d["r10"]), 4),
            "mrr_delta": round(sum(d["mrr"]) / len(d["mrr"]), 4),
            "ndcg_delta": round(sum(d["ndcg"]) / len(d["ndcg"]), 4),
            "v2_plus": plus,
            "v2_minus": minus,
        })

    result: dict[str, Any] = {
        "n_test_queries": n_total,
        "failures_total": n_fail,
        "categories": {r["category"]: r["count"] for r in taxonomy},
        "per_query": per_query,
        "failure_taxonomy": taxonomy,
        "classification_note": (
            "v2-based; rank-1 non-gold chunk classified vs gold payloads via "
            "section/family/document (see module docstring)"
        ),
    }
    ERR_OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # -------- console (ASCII only; fixes the cp1252 crash) --------
    print("\n=== Per-Query Analysis ===\n")
    print(f"{'QID':<6} {'Dom':<11} {'Diff':<12} {'Pairs':>6} {'V1_rk':>6} {'V2_rk':>6} "
          f"{'Type':<22} {'RetFail':>7} {'RerankFail':>10}")
    print("-" * 90)
    for r in per_query:
        print(f"{r['qid']:<6} {r['domain']:<11} {r['difficulty']:<12} {r['pairs']:>6} "
              f"{r['v1_rank']!s:>6} {r['v2_rank']!s:>6} {r['type']:<22} "
              f"{r['ret_fail']:>7} {r['rerank_fail']:>10}")

    print("\n=== Failure Taxonomy ===\n")
    print(f"Total queries: {n_total}")
    print(f"Total failures (pos not at rank 1): {n_fail}")
    print(f"{'Category':<22} {'Count':>5} {'%Total':>7} {'%Fail':>6} {'R10_delta':>10} "
          f"{'MRR_delta':>10} {'NDCG_delta':>11} {'V2+':>4} {'V2-':>4}")
    print("-" * 90)
    for t in taxonomy:
        print(f"{t['category']:<22} {t['count']:>5} {t['pct_total']:>7.4f} {t['pct_fail']:>6.4f} "
              f"{t['r10_delta']:>+10.4f} {t['mrr_delta']:>+10.4f} {t['ndcg_delta']:>+11.4f} "
              f"{t['v2_plus']:>4} {t['v2_minus']:>4}")
    print(f"\nOutput: {ERR_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
