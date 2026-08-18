"""MEASURE_ENSEMBLE_LIVE — live Qdrant re-measure of the ensemble reranker.

Task 1 of the CE_RERANK_REVIEW follow-ups: re-measure the sec_act + CE
ensemble reranker against **live Qdrant payloads** on the frozen 150-question
benchmark — the production equivalent of the offline head-150 eval.

For each question the raw fused candidate pool is retrieved ONCE through the
production retrieval composition (dense + sparse + identifier arm, no internal
reranker, per-question collection — mirrors ``run_retrieval_pipeline`` minus
its final rerank step), then both production reranker configs are applied to
the SAME pool:

    ensemble_off  plain Reranker (cross-encoder, configured RAG_RERANKER_MODEL)
    ensemble_on   EnsembleReranker (sec_act features primary + CE head bonus)

Retrieving once and applying both rerankers is the apples-to-apples measure —
the two configs differ only in the reranker, so the retrieval is shared (this
also halves the live Qdrant load vs running the pipeline twice per question).

Metrics: unit-level R@10/20 (fraction of relevant gold units covered) and
any-hit R@10 (per-question: any relevant unit in the top-10), via the gold
resolution layer (``matches_gold``) against the payload index.

Resumable: per-question results append to a checkpoint JSONL; a rerun skips
done questions.  The pool depth is taken from ``MEASURE_POOL_K`` (default 20)
so the pool-depth ablation can run at 20/50/100 without clobbering prior
runs — checkpoints and aggregates are keyed by depth
(``ensemble_live_k<k>.checkpoint.jsonl`` / ``ensemble_live_k<k>.json``).

Each per-question record also carries ``pool_cover``: whether any relevant
gold unit appears in the raw pool at ANY rank — the pool-ceiling signal that
decomposes the no-hit gap into pool depth vs ranking.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import contextlib

from dotenv import load_dotenv

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT.mkdir(parents=True, exist_ok=True)

TOP_K = 10
POOL_K = 20
with contextlib.suppress(ValueError):
    POOL_K = int(os.environ.get("MEASURE_POOL_K", "20"))
CHECKPOINT = OUT / f"ensemble_live_k{POOL_K}.checkpoint.jsonl"
AGGREGATE = OUT / f"ensemble_live_k{POOL_K}.json"


def _is_int_str(s: str) -> bool:
    """Check if a string is a valid integer (for safe key coercion)."""
    return s.lstrip("-").isdigit()


def _safe_int(val, default: int = 0) -> int:
    """Convert to int, falling back to *default* on failure."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _safe_float(val, default: float = 0.0) -> float:
    """Convert to float, falling back to *default* on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def load_checkpoint() -> dict[str, dict]:
    done: dict[str, dict] = {}
    if CHECKPOINT.exists():
        for line in CHECKPOINT.open(encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                # JSON serializes int dict keys to strings ("10"/"20");
                # coerce back so the aggregate (which indexes with ints)
                # works on checkpoint-loaded records.
                for rr in rec.get("rerankers", {}).values():
                    if isinstance(rr, dict) and "error" not in rr:
                        rr["unit_hits"] = {
                            _safe_int(k): v for k, v in rr.get("unit_hits", {}).items() if _is_int_str(k)
                        }
                        rr["any_hit"] = {_safe_int(k): v for k, v in rr.get("any_hit", {}).items() if _is_int_str(k)}
                done[rec.get("question_id", "")] = rec
    return done


def append_checkpoint(rec: dict) -> None:
    with CHECKPOINT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    import torch

    torch.set_num_threads(4)
    load_dotenv(PROJECT_ROOT / ".env")

    from app import create_app
    from app.rag.qdrant_client import QdrantStore
    from app.rag.retrieval import (
        DenseRetriever,
        HybridRetriever,
        QueryClassifier,
        QueryParser,
        Reranker,
        SparseRetriever,
    )
    from app.rag.retrieval.identifier import identifier_query
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

        # Retriever caches per collection (TLS + model load once).
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
            return HybridRetriever(dense=dense_cache[collection], sparse=sparse_cache[collection], reranker=None)

        reranker_off = Reranker(model_name=app.config.get("RAG_RERANKER_MODEL", ""))
        reranker_on = EnsembleReranker(
            model_name=app.config.get("RAG_RERANKER_MODEL", ""),
            ce_head=_safe_int(app.config.get("RAG_ENSEMBLE_CE_HEAD", 20), 20),
            ce_weight=_safe_float(app.config.get("RAG_ENSEMBLE_CE_WEIGHT", 0.5), 0.5),
        )

        def measure(reranked, q) -> dict:
            rel = q.relevant_units()
            unit_hits = {10: 0, 20: 0}
            any_hit = {10: 0, 20: 0}
            for unit in rel:
                for i, ch in enumerate(reranked):
                    pl = payload_index.get(ch.chunk_id)
                    if pl is not None and matches_gold(pl, unit, family_map):
                        for kk in (10, 20):
                            if i < kk:
                                unit_hits[kk] += 1
                                any_hit[kk] = 1
                        break
            return {
                "n_rel": len(rel),
                "unit_hits": unit_hits,
                "any_hit": any_hit,
            }

        def pool_covers(raw, q) -> bool:
            """Whether any relevant gold unit is in the pool at ANY rank."""
            rel = q.relevant_units()
            for unit in rel:
                for ch in raw:
                    pl = payload_index.get(ch.chunk_id)
                    if pl is not None and matches_gold(pl, unit, family_map):
                        return True
            return False

        done = load_checkpoint()
        todo = [qid for qid in questions if qid not in done]

        time.time()
        len(done)
        for qid in todo:
            q = questions[qid]
            collection = (q.collections or ["fssai_legal_768"])[0]
            _q_t0 = time.monotonic()
            try:
                hybrid = get_hybrid(collection)
                qtype = classifier.classify(q.question)
                parsed = parser.parse(q.question, qtype) or {}
                ident_q, _meta = identifier_query(q.question)
                result = hybrid.retrieve(q.question, top_k=POOL_K, filters=parsed, identifier_query=ident_q)
            except Exception as exc:
                append_checkpoint({"question_id": qid, "error": str(exc)})
                continue
            raw = result.chunks
            rec = {
                "question_id": qid,
                "query_types": q.question_types,
                "collections": q.collections,
                "pool_k": len(raw),
                "pool_cover": pool_covers(raw, q),
                "pool_latency_ms": round((time.monotonic() - _q_t0) * 1000),
                "rerankers": {},
            }
            try:
                _r_t0 = time.monotonic()
                off = reranker_off.rerank(q.question, list(raw), top_k=TOP_K)
                off_ms = round((time.monotonic() - _r_t0) * 1000)
                rec["rerankers"]["ensemble_off"] = {**measure(off, q), "latency_ms": off_ms}
            except Exception as exc:
                rec["rerankers"]["ensemble_off"] = {"error": str(exc)}
            try:
                _r_t0 = time.monotonic()
                on = reranker_on.rerank(q.question, list(raw), top_k=TOP_K)
                on_ms = _safe_int((time.monotonic() - _r_t0) * 1000)
                m = {**measure(on, q), "latency_ms": on_ms}
                # Capture head-chunk debug data for post-hoc CE weight sweep.
                # Only the ce_head chunks matter; sec_act features are binary
                # and identical across weight configs; only CE weight + head
                # size vary.  Storing chunk_id + base_score + feature flags +
                # CE scores lets the sweep script re-rank without re-fetching.
                from app.rag.retrieval.identifier import detect_act as _detect_act
                from app.rag.retrieval.identifier import detect_section

                q_sec_dbg, _ = detect_section(q.question)
                q_act_dbg = _detect_act(q.question)
                head_chunks = sorted(raw, key=lambda c: c.score, reverse=True)[: reranker_on.ce_head]
                debug_chunks = []
                for ch in head_chunks:
                    sec = reranker_on._section_match(q_sec_dbg, ch.section_number)
                    act = reranker_on._act_match(q_act_dbg, ch)
                    exact = 1.0 if (sec and act) else 0.0
                    hier = reranker_on._hierarchy_boost(ch.hierarchy_level)
                    debug_chunks.append({
                        "chunk_id": ch.chunk_id,
                        "base_score": round(ch.score, 6),
                        "sec": sec,
                        "act": act,
                        "exact": exact,
                        "hierarchy": hier,
                    })
                ce_scores: list[float] | None = None
                ce_scored = False
                encoder = reranker_on._get_encoder()
                skip_ce = (
                    reranker_on.skip_ce_when_confident
                    and q_sec_dbg is not None
                    and q_act_dbg is not None
                    and all(
                        reranker_on._section_match(q_sec_dbg, ch.section_number)
                        and reranker_on._act_match(q_act_dbg, ch)
                        for ch in head_chunks
                    )
                )
                ce_scored = encoder is not None and not skip_ce
                if ce_scored and reranker_on._encoder is not None:
                    pairs = [(q.question, ch.text) for ch in head_chunks]
                    try:
                        ce_scores = [float(s) for s in reranker_on._encoder.predict(pairs)]
                    except Exception:
                        ce_scores = None
                m["debug"] = {
                    "ce_head": reranker_on.ce_head,
                    "ce_weight": reranker_on.ce_weight,
                    "ce_scored": ce_scored,
                    "head_chunks": debug_chunks,
                    "ce_scores": ce_scores,
                }
                rec["rerankers"]["ensemble_on"] = m
            except Exception as exc:
                rec["rerankers"]["ensemble_on"] = {"error": str(exc)}

            done[qid] = rec
            append_checkpoint(rec)

        def aggregate(name: str) -> dict:
            recall = {10: 0.0, 20: 0.0}
            any_hits = {10: 0, 20: 0}
            n = 0
            covered = 0
            lat_sum = 0
            lat_n = 0
            by_type: dict[str, dict] = {}
            for _qid, rec in done.items():
                rr = rec.get("rerankers", {}).get(name)
                if not rr or "error" in rr:
                    continue
                n += 1
                if rec.get("pool_cover"):
                    covered += 1
                n_rel = max(rr["n_rel"], 1)
                for kk in (10, 20):
                    recall[kk] += rr["unit_hits"][kk] / n_rel
                    any_hits[kk] += rr["any_hit"][kk]
                if "latency_ms" in rr:
                    lat_sum += rr["latency_ms"]
                    lat_n += 1
                qtypes = rec.get("query_types", [])
                primary_type = qtypes[0] if qtypes else "unknown"
                bt = by_type.setdefault(primary_type, {"recall10": 0.0, "any10": 0, "n": 0})
                bt["recall10"] += rr["unit_hits"][10] / n_rel
                bt["any10"] += rr["any_hit"][10]
                bt["n"] += 1
            by_type_agg = {
                t: {
                    "R@10": round(d["recall10"] / max(d["n"], 1), 4),
                    "any_hit_R@10": round(d["any10"] / max(d["n"], 1), 4),
                    "n": d["n"],
                }
                for t, d in sorted(by_type.items())
            }
            result = (
                {f"R@{kk}": round(recall[kk] / max(n, 1), 4) for kk in (10, 20)}
                | {f"any_hit_R@{kk}": round(any_hits[kk] / max(n, 1), 4) for kk in (10, 20)}
                | {
                    "pool_ceiling": round(covered / max(n, 1), 4),
                    "n": n,
                }
            )
            if lat_n:
                result["latency_ms_avg"] = round(lat_sum / lat_n, 1)
            if by_type_agg:
                result["by_query_type"] = by_type_agg
            return result

        results = {
            "ensemble_off": aggregate("ensemble_off"),
            "ensemble_on": aggregate("ensemble_on"),
            "_meta": {
                "pool": f"live Qdrant, dense+sparse+identifier @{POOL_K} per question collection, no internal reranker",
                "top_k": TOP_K,
                "reranker_off": "plain Reranker (RAG_RERANKER_MODEL)",
                "reranker_on": f"EnsembleReranker (sec_act primary + CE head {app.config.get('RAG_ENSEMBLE_CE_HEAD', 20)} @ w={app.config.get('RAG_ENSEMBLE_CE_WEIGHT', 0.5)})",
                "reranker_model": app.config.get("RAG_RERANKER_MODEL", ""),
                "checkpoint": CHECKPOINT.name,
            },
        }
        AGGREGATE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
