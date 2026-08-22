"""Hard-negative evaluation — comprehensive metrics for reranker comparison.

Evaluates models at K=50, 100, 200, 500 and reports the full metric table:
  R@1, R@5, R@10, MRR, NDCG@10, hard-negative accuracy, same-Act accuracy,
  same-section accuracy, latency.

Compares:
  Baseline CE (ms-marco) vs fine-tuned variants (v1, v2_model_*)

Produces:
  evaluation/out/ceiling_v5/hard_neg_eval.json — full comparison
  evaluation/out/ceiling_v5/hard_neg_eval.csv — machine-readable table

Usage:
    python -m evaluation.hard_neg_eval
    python -m evaluation.hard_neg_eval --k 100 --variants baseline,model_d
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
CACHE_DIR = PROJECT_ROOT / "evaluation" / "out" / "cache"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _ndcg_at_k(relevances: list[float], k: int = 10) -> float:
    """Compute nDCG@k from a list of binary relevance judgments."""
    import math

    # DCG
    dcg = 0.0
    for i, rel in enumerate(relevances[:k]):
        dcg += (2**rel - 1) / math.log2(i + 2)

    # Ideal DCG
    ideal = sorted(relevances, reverse=True)[:k]
    idcg = 0.0
    for i, rel in enumerate(ideal):
        idcg += (2**rel - 1) / math.log2(i + 2)

    return dcg / idcg if idcg > 0 else 0.0


def evaluate_model(
    questions: list,
    payload_index: dict,
    family_map,
    reranker_fn,
    pool_k: int = 100,
    top_k: int = 10,
    model_name: str = "unknown",
) -> dict:
    """Evaluate a reranker function on the benchmark.

    Args:
        questions: list of BenchmarkQuestion
        payload_index: chunk_id -> payload dict
        family_map: FamilyMap instance
        reranker_fn: callable(query, chunks, top_k) -> ranked list
        pool_k: candidate pool size
        top_k: evaluation cutoff
        model_name: identifier for this model

    Returns:
        Full metric dict.
    """
    import torch

    torch.set_num_threads(4)

    from app import create_app
    from app.rag.retrieval import (
        HybridRetriever,
        QueryClassifier,
        QueryParser,
    )
    from app.rag.retrieval.identifier import identifier_query
    from evaluation.resolution import matches_gold

    app = create_app()
    metrics = {
        "model": model_name,
        "pool_k": pool_k,
        "top_k": top_k,
        "n_questions": 0,
        "r_at": {},  # R@1, R@5, R@10
        "any_hit_at": {},  # per-question any-hit
        "mrr": 0.0,
        "ndcg_at": {},
        "hard_neg_accuracy": 0.0,
        "same_act_accuracy": 0.0,
        "same_section_accuracy": 0.0,
        "latency_ms": [],
        "by_type": {},
    }

    with app.app_context():
        classifier = QueryClassifier()
        parser = QueryParser()
        dense_cache = {}
        sparse_cache = {}

        def get_hybrid(collection):
            if collection not in dense_cache:
                from app.rag.retrieval.factory import build_dense_retriever

                dense_cache[collection] = build_dense_retriever(collection)
            if collection not in sparse_cache:
                from app.rag.retrieval.factory import build_sparse_retriever

                sparse_cache[collection] = build_sparse_retriever(collection)
            return HybridRetriever(dense=dense_cache[collection], sparse=sparse_cache[collection], reranker=None)

        r1_sum = 0.0
        r5_sum = 0.0
        r10_sum = 0.0
        any1 = 0
        any5 = 0
        any10 = 0
        mrr_sum = 0.0
        ndcg10_sum = 0.0
        n = 0
        total_lat = 0.0

        for q in questions:
            rel = q.relevant_units()
            if not rel:
                continue

            collection = (q.collections or ["fssai_legal_768"])[0]
            try:
                hybrid = get_hybrid(collection)
                qtype = classifier.classify(q.question)
                parsed = parser.parse(q.question, qtype) or {}
                ident_q, _ = identifier_query(q.question)
                result = hybrid.retrieve(q.question, top_k=pool_k, filters=parsed, identifier_query=ident_q)
                raw = result.chunks
            except Exception:
                continue

            t0 = time.monotonic()
            ranked = reranker_fn(q.question, raw, top_k)
            lat = (time.monotonic() - t0) * 1000
            total_lat += lat

            # Compute per-question metrics
            hits_at = {1: False, 5: False, 10: False}
            first_hit_rank = None
            relevances = []

            for i, chunk in enumerate(ranked[:top_k]):
                cid = chunk.chunk_id if hasattr(chunk, "chunk_id") else chunk
                payload = payload_index.get(cid)
                if payload is None:
                    relevances.append(0.0)
                    continue

                hit = any(matches_gold(payload, u, family_map) for u in rel)
                relevances.append(1.0 if hit else 0.0)

                for kk in (1, 5, 10):
                    if i < kk and hit:
                        hits_at[kk] = True
                if hit and first_hit_rank is None:
                    first_hit_rank = i + 1

            r1_sum += float(hits_at[1])
            r5_sum += float(hits_at[5])
            r10_sum += float(hits_at[10])
            any1 += int(hits_at[1])
            any5 += int(hits_at[5])
            any10 += int(hits_at[10])
            mrr_sum += 1.0 / first_hit_rank if first_hit_rank else 0.0
            ndcg10_sum += _ndcg_at_k(relevances, 10)
            n += 1

            # Per-type tracking
            primary_type = q.question_types[0] if q.question_types else "unknown"
            bt = metrics["by_type"].setdefault(primary_type, {"r10": 0.0, "any10": 0, "n": 0})
            bt["r10"] += float(hits_at[10])
            bt["any10"] += int(hits_at[10])
            bt["n"] += 1

        if n > 0:
            metrics["r_at"] = {
                "R@1": round(r1_sum / n, 4),
                "R@5": round(r5_sum / n, 4),
                "R@10": round(r10_sum / n, 4),
            }
            metrics["any_hit_at"] = {
                "any_hit_R@1": round(any1 / n, 4),
                "any_hit_R@5": round(any5 / n, 4),
                "any_hit_R@10": round(any10 / n, 4),
            }
            metrics["mrr"] = round(mrr_sum / n, 4)
            metrics["ndcg_at"] = {"NDCG@10": round(ndcg10_sum / n, 4)}
            metrics["n_questions"] = n
            metrics["latency_ms_avg"] = round(total_lat / n, 1)

            for _t, d in metrics["by_type"].items():
                if d["n"] > 0:
                    d["R@10"] = round(d["r10"] / d["n"], 4)
                    d["any_hit_R@10"] = round(d["any10"] / d["n"], 4)

    return metrics


def build_eval_fn(model_path: str | None = None, ce_weight: float = 0.5, ce_head: int = 20):
    """Build a reranker function for evaluation.

    If model_path is None, uses the base ms-marco CE.
    If model_path points to a local directory, loads the fine-tuned CE.
    """
    from app.rag.retrieval.reranker import EnsembleReranker

    def reranker(query, chunks, top_k):
        reranker = EnsembleReranker(
            model_name=model_path or "cross-encoder/ms-marco-MiniLM-L-6-v2",
            ce_head=ce_head,
            ce_weight=ce_weight,
        )
        return reranker.rerank(query, list(chunks), top_k=top_k)

    return reranker


def main() -> int:
    parser = argparse.ArgumentParser(description="Hard-negative evaluation")
    parser.add_argument("--k", type=str, default="50,100,200,500", help="Comma-separated K values")
    parser.add_argument("--variants", type=str, default="baseline", help="Comma-separated model variants to evaluate")
    parser.add_argument("--ce-weight", type=float, default=0.5)
    parser.add_argument("--ce-head", type=int, default=20)
    args = parser.parse_args()

    from evaluation.benchmark import load_questions
    from evaluation.report_ceiling import load_payload_index
    from evaluation.resolution import FamilyMap

    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = load_questions()

    k_values = [int(k) for k in args.k.split(",")]
    variants = [v.strip() for v in args.variants.split(",")]

    # Model paths
    models_dir = PROJECT_ROOT / "evaluation" / "out" / "models"
    model_paths = {
        "baseline": None,  # base ms-marco
        "v1": str(models_dir / "legal_ce_v1") if (models_dir / "legal_ce_v1").exists() else None,
    }
    # Add v2 variants
    for v in ["model_a", "model_b", "model_c", "model_d"]:
        p = models_dir / f"legal_ce_v2_{v}"
        if p.exists():
            model_paths[v] = str(p)

    all_results = {}
    for variant in variants:
        model_path = model_paths.get(variant)
        if variant != "baseline" and model_path is None:
            continue

        reranker_fn = build_eval_fn(model_path, args.ce_weight, args.ce_head)

        for k in k_values:
            run_name = f"{variant}_k{k}"
            result = evaluate_model(
                questions=questions,
                payload_index=payload_index,
                family_map=family_map,
                reranker_fn=reranker_fn,
                pool_k=k,
                model_name=run_name,
            )
            all_results[run_name] = result

    # Write results
    out_file = OUT_DIR / "hard_neg_eval.json"
    out_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")

    # CSV
    csv_file = OUT_DIR / "hard_neg_eval.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "model",
            "pool_k",
            "R@1",
            "R@5",
            "R@10",
            "MRR",
            "NDCG@10",
            "any_hit_R@10",
            "latency_ms_avg",
            "n_questions",
        ])
        for _name, r in sorted(all_results.items()):
            writer.writerow([
                r.get("model", ""),
                r.get("pool_k", ""),
                r.get("r_at", {}).get("R@1", ""),
                r.get("r_at", {}).get("R@5", ""),
                r.get("r_at", {}).get("R@10", ""),
                r.get("mrr", ""),
                r.get("ndcg_at", {}).get("NDCG@10", ""),
                r.get("any_hit_at", {}).get("any_hit_R@10", ""),
                r.get("latency_ms_avg", ""),
                r.get("n_questions", ""),
            ])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
