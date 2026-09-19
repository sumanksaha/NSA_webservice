"""Standalone evaluation of the legal_ce_v2_K500 checkpoint on the 150-question
legal RAG benchmark, compared against the legal_ce_v1 baseline and the untuned
ce_base.

This script is READ-ONLY with respect to training files, checkpoints, configs,
and processes.  It only *reads* existing model files and cached arm data, then
writes results to a NEW file in evaluation/out/.

Metrics: R@1, R@20, R@50, R@100 (unit-level recall + any-hit recall),
         MRR, NDCG@10.

Usage:
    python -m evaluation.eval_v2_checkpoint
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

import torch

torch.set_num_threads(4)

from evaluation.benchmark import load_questions
from evaluation.config import CACHE_DIR
from evaluation.resolution import FamilyMap, matches_gold, build_payload_index
from evaluation.rerank_legal import build_pool, rerank, rrf_scores, rank_of
from evaluation.ceiling_config import DEPTHS

# ---------------------------------------------------------------------------
# Model paths
# ---------------------------------------------------------------------------
MODELS_DIR = PROJECT_ROOT / "evaluation" / "out" / "models"
CE_V1 = MODELS_DIR / "legal_ce_v1"
CE_V2 = MODELS_DIR / "legal_ce_v2_K500"
CE_BASE = "cross-encoder/ms-marco-MiniLM-L-6-v2"
POOL_HEAD = 150
CE_BATCH = 64
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "ce_v2_checkpoint_eval.json"


def load_payload_index_simple() -> dict[str, dict]:
    """Load cached payload index directly (bypasses Flask app creation)."""
    cache_file = CACHE_DIR / "payload_index.jsonl"
    index: dict[str, dict] = {}
    with open(cache_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            index[rec["id"]] = rec["payload"]
    return index


def load_raw(arm: str) -> dict[str, dict]:
    """Load cached raw arm results from ceiling_v5/raw."""
    raw_dir = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "raw"
    p = raw_dir / f"{arm}.jsonl"
    recs: dict[str, dict] = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs


def load_jsonl(path: Path) -> dict[str, dict]:
    recs = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs


def score_pool(items: list[dict], query: str, ce) -> list[dict]:
    """Rerank a pool of candidate items with a CrossEncoder."""
    pairs = [
        (
            query,
            str(it["payload"].get("chunk_text") or it["payload"].get("text") or ""),
        )
        for it in items
    ]
    if not pairs:
        return items
    scores = ce.predict(pairs, batch_size=CE_BATCH)
    scored = sorted(zip(scores, items, strict=False), key=lambda x: float(x[0]), reverse=True)
    return [it for _, it in scored]


def compute_metrics(ranked_items: list[dict], question, payload_index, family_map) -> dict:
    """Compute R@1, R@20, R@50, R@100, MRR, NDCG@10 for a single reranked list."""
    rel = question.relevant_units()  # primary + acceptable
    all_units = question.recall_units()  # all gold (incl. supporting)

    # Find first-hit rank of each gold unit
    unit_ranks: dict[str, int | None] = {}
    for unit in all_units:
        r = rank_of(ranked_items, unit, payload_index, family_map)
        unit_ranks[unit.provision_id] = r

    # Relevant unit ranks only
    rel_ranks = []
    rel_hit_ranks = []
    for unit in rel:
        r = unit_ranks.get(unit.provision_id)
        if r is not None:
            rel_hit_ranks.append(r)
    rel_ranks = [r for r in [unit_ranks.get(u.provision_id) for u in rel]]

    n_rel = len(rel)
    if n_rel == 0:
        return {
            "R@1": 0.0,
            "R@20": 0.0,
            "R@50": 0.0,
            "R@100": 0.0,
            "recall_unit@1": 0.0,
            "recall_unit@20": 0.0,
            "recall_unit@50": 0.0,
            "recall_unit@100": 0.0,
            "MRR": 0.0,
            "NDCG@10": 0.0,
            "n_gold": 0,
            "n_pool": len(ranked_items),
        }

    # Unit-level Recall@K (fraction of relevant units found in top-K)
    def recall_unit(K: int) -> float:
        hits = sum(1 for r in rel_ranks if r is not None and r <= K)
        return hits / n_rel

    # Any-hit Recall@K (at least one relevant unit in top-K)
    def recall_any(K: int) -> float:
        return 1.0 if any(r is not None and r <= K for r in rel_ranks) else 0.0

    # MRR (1/rank of first relevant hit)
    min_rank = min((r for r in rel_ranks if r is not None), default=None)
    mrr = 1.0 / min_rank if min_rank else 0.0

    # NDCG@10 (primary=2, acceptable=1, supporting=0)
    gains_by_rank: dict[int, float] = {}
    for unit in rel:
        r = unit_ranks.get(unit.provision_id)
        if r is not None and r <= 10:
            gain = 2.0 if unit.role == "primary" else 1.0
            gains_by_rank[r] = gains_by_rank.get(r, 0.0) + gain
    ideal = sorted([(2.0 if u.role == "primary" else 1.0) for u in rel], reverse=True)

    def idcg(gs: list[float], k: int) -> float:
        return sum(gs[i] / math.log2(i + 2) for i in range(min(k, len(gs)))) or 1e-9

    dcg = sum(g / math.log2(r + 1) for r, g in gains_by_rank.items() if r <= 10)
    ndcg = dcg / idcg(ideal, 10)

    return {
        "R@1": recall_any(1),
        "R@20": recall_any(20),
        "R@50": recall_any(50),
        "R@100": recall_any(100),
        "recall_unit@1": recall_unit(1),
        "recall_unit@20": recall_unit(20),
        "recall_unit@50": recall_unit(50),
        "recall_unit@100": recall_unit(100),
        "MRR": mrr,
        "NDCG@10": ndcg,
        "n_gold": n_rel,
        "n_pool": len(ranked_items),
    }


def main() -> int:
    print("[1/5] Loading cached data (payload index, arms, benchmark questions)...")
    payload_index = load_payload_index_simple()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}

    raw_dir = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "raw"
    dense = load_jsonl(raw_dir / "A_dense.jsonl")
    sparse = load_jsonl(raw_dir / "B_sparse.jsonl")
    kg = load_jsonl(raw_dir / "D_kg.jsonl")
    ident = load_jsonl(PROJECT_ROOT / "evaluation" / "out" / "cache" / "v55_ident" / "sparse_identifier.jsonl")

    print(f"  payload_index={len(payload_index)} points, questions={len(questions)}")

    # ---- Build per-question union pool (top-150 by base RRF) ----
    print("[2/5] Building candidate pools (dense@500 | sparse@500 | KG@500 | ident@500, head-150)...", flush=True)
    per_q: dict[str, tuple] = {}
    for qid, q in questions.items():
        d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
        if not (d and s and k):
            continue
        pool = build_pool(d, s, k, payload_index, family_map, slice_depth=500, kg_slice=500)
        rrf = rrf_scores([
            [{"key": c} for c in d.get("chunk_ids", [])[:500]],
            [{"key": c} for c in s.get("chunk_ids", [])[:500]],
            [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:200]],
        ])
        rec = ident.get(qid, {})
        ids = [str(c) for c in rec.get("chunk_ids", [])[:500]]
        if ids:
            rrf = rrf_scores([
                [{"key": c} for c in d.get("chunk_ids", [])[:500]],
                [{"key": c} for c in s.get("chunk_ids", [])[:500]],
                [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
                [{"key": c} for c in ids],
            ])
        base = rerank(pool, q.question, family_map, rrf, {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})
        head = base[:POOL_HEAD]
        if not head:
            continue
        per_q[qid] = (q, head)

    print(f"  Built pools for {len(per_q)} questions", flush=True)

    # ---- Load CE models ----
    print("[3/5] Loading CrossEncoder models...", flush=True)
    from sentence_transformers import CrossEncoder

    ce_models = {
        "ce_v1_legal": CrossEncoder(CE_V1.as_posix(), max_length=256),
        "ce_v2_K500": CrossEncoder(CE_V2.as_posix(), max_length=256),
    }
    print("  Models loaded (v1 + v2).", flush=True)

    # ---- Score each pool with each model ----
    print("[4/5] Scoring pools with each model...", flush=True)
    results: dict[str, list[dict]] = {name: [] for name in ce_models}
    n_q = len(per_q)
    for i, (qid, (q, head)) in enumerate(sorted(per_q.items()), 1):
        for name, ce in ce_models.items():
            reranked = score_pool(head, q.question, ce)
            metrics = compute_metrics(reranked, q, payload_index, family_map)
            results[name].append({"question_id": qid, **metrics})
        if i % 10 == 0 or i == n_q:
            print(f"  {i}/{n_q} questions scored", flush=True)

    # ---- Aggregate ----
    agg: dict[str, dict] = {}
    for name in ce_models:
        rows = results[name]
        n = len(rows)
        agg[name] = {
            "n": n,
            "R@1": round(sum(r["R@1"] for r in rows) / n, 4),
            "R@20": round(sum(r["R@20"] for r in rows) / n, 4),
            "R@50": round(sum(r["R@50"] for r in rows) / n, 4),
            "R@100": round(sum(r["R@100"] for r in rows) / n, 4),
            "MRR": round(sum(r["MRR"] for r in rows) / n, 4),
            "NDCG@10": round(sum(r["NDCG@10"] for r in rows) / n, 4),
            "recall_unit@1": round(sum(r["recall_unit@1"] for r in rows) / n, 4),
            "recall_unit@20": round(sum(r["recall_unit@20"] for r in rows) / n, 4),
            "recall_unit@50": round(sum(r["recall_unit@50"] for r in rows) / n, 4),
            "recall_unit@100": round(sum(r["recall_unit@100"] for r in rows) / n, 4),
            "avg_n_gold": round(sum(r["n_gold"] for r in rows) / n, 2),
        }

    # ---- Write results ----
    print("[5/5] Writing results...", flush=True)
    output = {
        "model_comparison": {
            "ce_v1_legal": {
                "description": "legal_ce_v1 (previously fine-tuned on ~2,131 mined pairs)",
                "path": str(CE_V1),
            },
            "ce_v2_K500": {
                "description": "legal_ce_v2_K500 (fine-tuned on 18,756 pairs, 3-epoch curriculum T1->T2->T3, 1,292 optimizer steps)",
                "path": str(CE_V2),
                "final_train_loss": 0.09919,
                "best_val_loss": 0.71813,
            },
        },
        "methodology": {
            "benchmark": "benchmark_v1.0.jsonl (150 questions)",
            "pool": "dense@500 | sparse@500 | KG@500 | question-ident@500, head-150 by base RRF",
            "rerank_method": "CrossEncoder reranks the top-150 pool (sec_act weights all 0, pure RRF base)",
            "max_length": 256,
            "batch_size": CE_BATCH,
            "device": "cpu",
            "threads": 4,
        },
        "results": agg,
        "per_question": results,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to: {OUT_FILE}", flush=True)

    # --- Console summary ---
    print("\n" + "=" * 70)
    print("150-QUESTION LEGAL RAG BENCHMARK — CE RERANKER EVALUATION")
    print("=" * 70)
    print(f"{'Model':<20} {'R@1':>7} {'R@20':>7} {'R@50':>7} {'R@100':>7} {'MRR':>7} {'NDCG@10':>8}")
    print("-" * 70)
    for name in ce_models:
        a = agg[name]
        print(
            f"{name:<20} {a['R@1']:>7.4f} {a['R@20']:>7.4f} {a['R@50']:>7.4f} {a['R@100']:>7.4f} {a['MRR']:>7.4f} {a['NDCG@10']:>8.4f}"
        )

    print("\n--- Unit-level Recall@K (fraction of gold units in top-K) ---")
    print(f"{'Model':<20} {'R@1':>7} {'R@20':>7} {'R@50':>7} {'R@100':>7}")
    print("-" * 50)
    for name in ce_models:
        a = agg[name]
        print(
            f"{name:<20} {a['recall_unit@1']:>7.4f} {a['recall_unit@20']:>7.4f} {a['recall_unit@50']:>7.4f} {a['recall_unit@100']:>7.4f}"
        )

    print("\n--- vs legal_ce_v1 baseline deltas (legal_ce_v2_K500) ---")
    a = agg["ce_v2_K500"]
    b = agg["ce_v1_legal"]
    for metric in ["R@1", "R@20", "R@50", "R@100", "MRR", "NDCG@10"]:
        delta = a[metric] - b[metric]
        arrow = "UP" if delta > 0 else ("DOWN" if delta < 0 else "=")
        print(f"    {metric:<10} {a[metric]:>7.4f} vs {b[metric]:>7.4f}  {arrow} {delta:+.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
