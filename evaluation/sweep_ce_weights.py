"""Post-hoc CE weight sweep — tune the cross-encoder weight WITHOUT re-running retrieval.

Reads a completed ensemble_live checkpoint and re-simulates the EnsembleReranker's
final stage (primary + CE bonus) at different CE weight / head-size combinations.

Two modes:
  • Fast mode (default): uses debug data stored in the checkpoint by the
    patched measure_ensemble_live.py.  Only the top-K within the head changes
    when CE weight varies — sweep is instant.
  • Refetch mode (--refetch): re-fetches raw chunks from Qdrant and re-runs
    the full EnsembleReranker at each weight.  Slower but exact; works on
    old checkpoints without debug data.

Usage:
    python -m evaluation.sweep_ce_weights
    python -m evaluation.sweep_ce_weights --refetch --weights "0.3,0.4,0.5,0.6,0.7" --heads "10,15,20,30"
    python -m evaluation.sweep_ce_weights --checkpoint ensemble_live_k500.checkpoint.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from itertools import product
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
W_SEC, W_ACT, W_EXACT, W_HIER = 2.0, 1.5, 1.0, 0.2


def minmax(scores: list[float]) -> list[float]:
    """Min-max normalize to [0, 1]; all-equal → zeros."""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    span = hi - lo
    if span <= 0:
        return [0.0] * len(scores)
    return [(s - lo) / span for s in scores]


def parse_checkpoint(path: Path) -> dict[str, dict]:
    """Load checkpoint records, skipping malformed lines."""
    done: dict[str, dict] = {}
    if path.exists():
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            qid = rec.get("question_id")
            if qid:
                done[qid] = rec
    return done


def parse_weights_heads(weights_str: str, heads_str: str) -> tuple[list[float], list[int]]:
    """Parse weight/head CLI args with validation."""
    try:
        weights = [float(w) for w in weights_str.split(",")]
    except ValueError:
        weights = [0.5]
    try:
        heads = [int(h) for h in heads_str.split(",")]
    except ValueError:
        heads = [20]
    return weights, heads


def compute_r10_for_weight(
    debug: dict,
    ce_weight: float,
    ce_head: int,
    payload_index: dict,
    gold_units: dict[str, list],
    family_map,
    matches_gold_fn,
) -> float:
    """Recompute R@10 for a single question at a given CE weight/head.

    Uses the stored debug data: head_chunks (base_score + feature flags) and
    ce_scores.  Non-head chunks keep their primary score (no CE bonus).
    """
    head_chunks = debug.get("head_chunks", [])
    ce_scores = debug.get("ce_scores")
    if not head_chunks or ce_scores is None:
        return 0.0  # CE was skipped — can't sweep

    primaries = [
        hc["base_score"] + W_SEC * hc["sec"] + W_ACT * hc["act"] + W_EXACT * hc["exact"] + W_HIER * hc["hierarchy"]
        for hc in head_chunks
    ]

    # Re-select head by primary (stable; same as EnsembleReranker)
    indexed = list(range(len(head_chunks)))
    head_idx = sorted(indexed, key=lambda i: primaries[i], reverse=True)[:ce_head]
    head_ce = [ce_scores[i] for i in head_idx]
    ce_norm = minmax(head_ce)

    # Final = primary + ce_weight * minmax(ce_scores) for head chunks
    finals = [(primaries[i] + ce_weight * ce_norm[r], i) for r, i in enumerate(head_idx)]
    finals.sort(key=lambda x: x[0], reverse=True)

    # Top-K chunk indices within the head
    top10_chunk_ids = [head_chunks[i]["chunk_id"] for _, i in finals[:10]]

    # Count gold hits
    hits = 0
    n_rel = 0
    for unit in gold_units.get(debug.get("question_id", ""), []):
        n_rel += 1
        for cid in top10_chunk_ids:
            pl = payload_index.get(cid)
            if pl is not None and matches_gold_fn(pl, unit, family_map):
                hits += 1
                break
    return hits / max(n_rel, 1)


def sweep_fast(args, weights, heads):
    """Fast post-hoc sweep using stored debug data (no Qdrant re-fetch)."""
    done = parse_checkpoint(args.checkpoint)

    debug_recs = {qid: rec for qid, rec in done.items() if "debug" in rec.get("rerankers", {}).get("ensemble_on", {})}

    if not debug_recs:
        return


    # Load gold units + payload_index for hit checking
    from evaluation.benchmark import load_questions
    from evaluation.report_ceiling import load_payload_index
    from evaluation.resolution import FamilyMap, matches_gold

    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}
    gold_units = {qid: q.relevant_units() for qid, q in questions.items()}

    results = []
    for w, h in product(weights, heads):
        r10_sum = 0.0
        n = 0
        for qid, rec in debug_recs.items():
            debug = rec["rerankers"]["ensemble_on"]["debug"]
            debug["question_id"] = qid
            r10 = compute_r10_for_weight(debug, w, h, payload_index, gold_units, family_map, matches_gold)
            r10_sum += r10
            n += 1
        if n > 0:
            avg_r10 = r10_sum / n
            results.append({"ce_weight": w, "ce_head": h, "R@10": round(avg_r10, 4), "n": n})

    # Write results
    suffix = f"w{'_'.join(str(w) for w in weights)}h{'_'.join(str(h) for h in heads)}"
    sweep_file = OUT / f"sweep_{args.checkpoint.stem}_{suffix}.csv"
    with sweep_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ce_weight", "ce_head", "R@10", "n_questions"])
        for r in sorted(results, key=lambda x: x["R@10"], reverse=True):
            writer.writerow([r["ce_weight"], r["ce_head"], r["R@10"], r["n"]])

    max(results, key=lambda x: x["R@10"])


def sweep_refetch(args, weights, heads):
    """Full sweep: re-fetch raw pools, score CE once per head-size, sweep all weights."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from app import create_app
    from app.rag.qdrant_client import QdrantStore
    from app.rag.retrieval import (
        DenseRetriever,
        HybridRetriever,
        QueryClassifier,
        QueryParser,
        SparseRetriever,
    )
    from app.rag.retrieval.identifier import detect_act, detect_section, identifier_query
    from app.rag.retrieval.reranker import EnsembleReranker
    from app.rag.sparse_embedding import SparseEmbeddingService
    from evaluation.benchmark import load_questions
    from evaluation.report_ceiling import load_payload_index
    from evaluation.resolution import FamilyMap, matches_gold

    app = create_app()
    with app.app_context():
        payload_index = load_payload_index()
        family_map = FamilyMap()
        questions = {q.question_id: q for q in load_questions()}
        classifier = QueryClassifier()
        parser = QueryParser()

        done = parse_checkpoint(args.checkpoint)
        try:
            pool_k = int(os.environ.get("MEASURE_POOL_K", "500"))
        except ValueError:
            pool_k = 500

        dense_cache: dict[str, DenseRetriever] = {}
        sparse_cache: dict[str, SparseRetriever] = {}

        def get_hybrid(collection: str) -> HybridRetriever:
            if collection not in dense_cache:
                dense_cache[collection] = DenseRetriever(collection_name=collection)
            if collection not in sparse_cache:
                sparse_cache[collection] = SparseRetriever(
                    corpus={},
                    store=QdrantStore(collection_name=collection),
                    embedder=SparseEmbeddingService(),
                )
            return HybridRetriever(
                dense=dense_cache[collection],
                sparse=sparse_cache[collection],
                reranker=None,
            )

        # 1. Re-fetch all raw pools once (shared across all weight/head combos)
        raw_pools: dict[str, tuple] = {}
        time.monotonic()
        for qid, rec in done.items():
            if rec.get("error"):
                continue
            q = questions[qid]
            collection = (q.collections or ["fssai_legal_768"])[0]
            try:
                hybrid = get_hybrid(collection)
                qtype = classifier.classify(q.question)
                parsed = parser.parse(q.question, qtype) or {}
                ident_q, _meta = identifier_query(q.question)
                result = hybrid.retrieve(q.question, top_k=pool_k, filters=parsed, identifier_query=ident_q)
                raw_pools[qid] = (q, result.chunks)
            except Exception:
                continue

        # 2. For each question, compute primary scores (sec+act+exact+hier)
        #    and store per-chunk data for the full pool (needed for top-10 simulation).
        #    Score CE once per head size, reuse across all weights.
        # Use hardcoded weights (matches EnsembleReranker._W_SEC etc.)
        _W_SEC, _W_ACT, _W_EXACT, _W_HIER = 2.0, 1.5, 1.0, 0.2

        pool_data: dict[str, dict] = {}  # qid -> {chunks: [...], ce_per_head: {head: scores}}
        encoder = EnsembleReranker()._get_encoder()
        for qid, (q, raw) in raw_pools.items():
            q_sec, _ = detect_section(q.question)
            q_act = detect_act(q.question)

            chunks_data = []
            for ch in raw:
                sec = EnsembleReranker._section_match(q_sec, ch.section_number)
                act = EnsembleReranker._act_match(q_act, ch)
                exact = 1.0 if (sec and act) else 0.0
                hier = EnsembleReranker._hierarchy_boost(ch.hierarchy_level)
                primary = ch.score + _W_SEC * sec + _W_ACT * act + _W_EXACT * exact + _W_HIER * hier
                chunks_data.append({
                    "chunk_id": ch.chunk_id,
                    "text": ch.text,
                    "primary": primary,
                    "sec": sec,
                    "act": act,
                    "exact": exact,
                    "hierarchy": hier,
                })
            # Sort by primary descending
            chunks_data.sort(key=lambda c: c["primary"], reverse=True)

            # Score CE once per head size
            ce_per_head: dict[int, list[float] | None] = {}
            if encoder is not None:
                for h in heads:
                    head = chunks_data[:h]
                    # Check skip_ce
                    skip = (
                        EnsembleReranker(encoder=None).skip_ce_when_confident
                        and q_sec is not None
                        and q_act is not None
                        and all(c["exact"] for c in head)
                    )
                    if skip:
                        ce_per_head[h] = None  # CE skipped
                    else:
                        pairs = [(q.question, c["text"]) for c in head]
                        try:
                            ce_per_head[h] = [float(s) for s in encoder.predict(pairs)]
                        except Exception:
                            ce_per_head[h] = None
            else:
                ce_per_head = {h: None for h in heads}

            pool_data[qid] = {"chunks": chunks_data, "ce_per_head": ce_per_head, "q": q}

        # 3. Sweep all weight/head combos (instant — just arithmetic)
        results = []
        for w, h in product(weights, heads):
            time.monotonic()
            r10_sum = 0.0
            any10_sum = 0
            n = 0
            for qid, pd in pool_data.items():
                q = pd["q"]
                chunks_d = pd["chunks"]
                ce_scores = pd["ce_per_head"].get(h)

                # Compute final score for top-K
                finals = []
                for i, c in enumerate(chunks_d[: max(heads)]):
                    score = c["primary"]
                    if ce_scores is not None and i < h:
                        # minmax normalize within head
                        head_ce = ce_scores[:h]
                        norm = minmax(head_ce)
                        score += w * norm[i] if i < len(norm) else 0.0
                    finals.append((score, c["chunk_id"], c))

                # Also include non-head chunks (primary only) up to top-K
                for i, c in enumerate(chunks_d):
                    if i >= max(heads):
                        finals.append((c["primary"], c["chunk_id"], c))

                finals.sort(key=lambda x: x[0], reverse=True)
                top10 = finals[:10]

                rel = q.relevant_units()
                unit_hits = 0
                any_hit = 0
                for unit in rel:
                    for _, cid, _ in top10:
                        pl = payload_index.get(cid)
                        if pl is not None and matches_gold(pl, unit, family_map):
                            unit_hits += 1
                            any_hit = 1
                            break
                n_rel = max(len(rel), 1)
                r10_sum += unit_hits / n_rel
                any10_sum += any_hit
                n += 1

            if n > 0:
                avg_r10 = r10_sum / n
                avg_any10 = any10_sum / n
                results.append({
                    "ce_weight": w,
                    "ce_head": h,
                    "R@10": round(avg_r10, 4),
                    "any_hit_R@10": round(avg_any10, 4),
                    "n": n,
                })

        # Write results
        suffix = f"w{'_'.join(str(w) for w in weights)}h{'_'.join(str(h) for h in heads)}"
        sweep_file = OUT / f"sweep_{args.checkpoint.stem}_{suffix}.csv"
        with sweep_file.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ce_weight", "ce_head", "R@10", "any_hit_R@10", "n_questions"])
            for r in sorted(results, key=lambda x: x["R@10"], reverse=True):
                writer.writerow([r["ce_weight"], r["ce_head"], r["R@10"], r.get("any_hit_R@10", ""), r["n"]])

        if not results:
            return

        best = max(results, key=lambda x: x["R@10"])

        json_file = sweep_file.with_suffix(".json")
        json_data = {
            "weights": weights,
            "heads": heads,
            "best": {"ce_weight": best["ce_weight"], "ce_head": best["ce_head"], "R@10": best["R@10"]},
            "results": results,
        }
        json_file.write_text(json.dumps(json_data, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-hoc CE weight sweep")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=OUT / "ensemble_live_k500.checkpoint.jsonl",
        help="Checkpoint JSONL file to sweep (relative paths resolve under OUT/)",
    )
    parser.add_argument(
        "--weights",
        type=str,
        default="0.3,0.4,0.5,0.6,0.7",
        help="Comma-separated CE weights to sweep",
    )
    parser.add_argument(
        "--heads",
        type=str,
        default="10,15,20,30",
        help="Comma-separated CE head sizes to sweep",
    )
    parser.add_argument(
        "--refetch",
        action="store_true",
        help="Re-fetch raw chunks from Qdrant and re-run EnsembleReranker (slower, exact, works on old checkpoints)",
    )
    args = parser.parse_args()

    # Resolve relative checkpoint paths against OUT/
    if not args.checkpoint.is_absolute() and not (Path.cwd() / args.checkpoint).exists():
        args.checkpoint = OUT / args.checkpoint

    weights, heads = parse_weights_heads(args.weights, args.heads)

    if args.refetch:
        sweep_refetch(args, weights, heads)
    else:
        sweep_fast(args, weights, heads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
