"""RANKING_CEILING_V1 — live retrieval phase.

Usage:
    python -m evaluation.run_ceiling [--arms A_dense,B_sparse,C_hybrid,D_kg,O_dense,O_sparse,X_exact]
                                    [--shard 1/3] [--force]

Arms (protocol §4–§6, §13–§14):
    A_dense    dense Qdrant @500            (production DenseRetriever)
    B_sparse   sparse BM25 @500             (production SparseRetriever + QdrantStore)
    C_hybrid   dense+sparse hybrid @500     (production HybridRetriever — frozen fusion)
    D_kg       KG graph-RAG contract @200   (production kg.queries contract; diagnostic depth)
    O_dense    §13 oracle: gold provision title -> dense @500
    O_sparse   §13 oracle: gold provision title -> sparse @500
    X_exact    §14 exact identifier query (act + section) -> dense@100 + sparse@100

Results are cached per arm under ``evaluation/out/ceiling_v1/raw/<arm>.jsonl``
(append-only, resumable, shardable).  **No LLM is called anywhere in this
phase** — retrieval + embeddings only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval.ceiling.run")


def _cached_ids(arm: str) -> set[str]:
    from evaluation.ceiling_config import RAW_DIR

    p = RAW_DIR / f"{arm}.jsonl"
    if not p.exists():
        return set()
    ids: set[str] = set()
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                ids.add(json.loads(line)["question_id"])
            except Exception:
                continue
    return ids


def _oracle_query(question, registry: dict) -> tuple[str, str]:
    """Gold provision text query (§13).  The registry carries provision
    *titles* (full provision text is not stored in the frozen gold registry);
    the title is used verbatim.  Returns (query, source_id)."""
    units = question.primary_units() or question.relevant_units()
    if not units:
        return question.question, ""
    unit = units[0]
    rec = registry.get(unit.provision_id) or {}
    title = rec.get("title") or ""
    if title:
        return str(title), unit.provision_id
    if unit.section:
        return f"{unit.act} section {unit.section}", unit.provision_id
    return unit.act or question.question, unit.provision_id


def _identifier_query(question, registry: dict) -> tuple[str | None, str | None]:
    """§14 exact legal identifier query: 'Act section N' (lexical probe)."""
    units = question.primary_units() or question.relevant_units()
    for unit in units:
        if unit.section and unit.act:
            return f"{unit.act} section {unit.section}", unit.provision_id
    return None, None


def _kg_provision_public(p: dict) -> dict:
    instrument_title = p.get("instrument_title") or (p.get("instrument") or {}).get("title") or ""
    return {
        "provision_id": p.get("provision_id"),
        "provision_number": p.get("provision_number"),
        "title": p.get("title") or "",
        "instrument_title": instrument_title,
        "legal_domain": p.get("legal_domain") or p.get("domain") or "",
        "status": p.get("status") or "",
    }


def run_arm_d_kg(question: dict, limit: int) -> dict:
    """ARM D — KG graph-RAG retrieval contract at *limit* depth."""
    from kg.queries import LegalKGQueries, provisions_for_query

    start = time.monotonic()
    queries = LegalKGQueries()
    try:
        provisions = provisions_for_query(question["question"], queries, limit=limit)
    except Exception as exc:
        logger.warning("arm D failed for %s: %s", question["question_id"], exc)
        provisions = []
    return {
        "chunk_ids": [],
        "kg_provisions": [_kg_provision_public(p) for p in provisions][:limit],
        "kg_source": "contract",
        "latency_ms": int((time.monotonic() - start) * 1000),
        "error": None,
        "retriever": "kg",
        "query_used": question["question"],
    }


def run_oracle_dense(question: dict, query: str, top_k: int) -> dict:
    from evaluation.arms import _dense

    collection = question["collections"][0]
    result = _dense(collection).search(query, top_k=top_k)
    return {
        "chunk_ids": [c.chunk_id for c in result.chunks],
        "kg_provisions": [],
        "kg_source": None,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "retriever": "dense",
        "query_used": query,
    }


def run_oracle_sparse(question: dict, query: str, top_k: int) -> dict:
    from evaluation.arms import _sparse

    collection = question["collections"][0]
    result = _sparse(collection).retrieve(query, top_k=top_k, threshold=0.0)
    return {
        "chunk_ids": [c.chunk_id for c in result.chunks],
        "kg_provisions": [],
        "kg_source": None,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "retriever": "sparse",
        "query_used": query,
    }


def run_expanded_hybrid(question: dict, query: str, top_k: int) -> dict:
    """V3 — hybrid (dense+sparse, frozen fusion) with an expanded query."""
    from evaluation.arms import _hybrid

    collection = question["collections"][0]
    result = _hybrid(collection, with_reranker=False).retrieve(query, top_k=top_k)
    return {
        "chunk_ids": [c.chunk_id for c in result.chunks],
        "kg_provisions": [],
        "kg_source": None,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "retriever": "hybrid",
        "query_used": query,
    }


def run_exact_identifier(question: dict, query: str, top_k: int) -> dict:
    """§14 — run the exact identifier through dense and sparse; keep both."""
    dense_rec = run_oracle_dense(question, query, top_k)
    sparse_rec = run_oracle_sparse(question, query, top_k)
    return {
        "chunk_ids": dense_rec["chunk_ids"],
        "chunk_ids_sparse": sparse_rec["chunk_ids"],
        "kg_provisions": [],
        "kg_source": None,
        "latency_ms": dense_rec["latency_ms"] + sparse_rec["latency_ms"],
        "error": dense_rec["error"] or sparse_rec["error"],
        "retriever": "exact_identifier",
        "query_used": query,
        "exact_dense_depth": top_k,
        "exact_sparse_depth": top_k,
    }


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from evaluation.benchmark import load_gold_registry, load_questions
    from evaluation.ceiling_config import (
        DENSE_DEPTH,
        EXACT_DEPTH,
        HYBRID_DEPTH,
        KG_DIAGNOSTIC_LIMIT,
        LIVE_ARMS,
        ORACLE_DEPTH,
        OUT_DIR,
        RAW_DIR,
        SPARSE_DEPTH,
        write_freeze,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", default=",".join(LIVE_ARMS))
    parser.add_argument("--shard", default="1/1")
    parser.add_argument("--limit", type=int, default=0, help="smoke test: only the first N questions")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    freeze = write_freeze(app)
    logger.info("experiment %s frozen (hash %s)", freeze["experiment_id"], freeze["freeze_hash"])

    with app.app_context():
        questions = load_questions()
        registry = load_gold_registry()

        idx_str, n_str = args.shard.split("/")
        shard_idx, n_shards = int(idx_str), int(n_str)
        questions = questions[shard_idx - 1 :: n_shards] if n_shards > 1 else questions
        if args.limit:
            questions = questions[: args.limit]
        logger.info("shard %s: %d questions", args.shard, len(questions))

        from evaluation import arms as arms_mod

        arms = [a.strip() for a in args.arms.split(",") if a.strip()]
        for arm in arms:
            cache_path = RAW_DIR / f"{arm}.jsonl"
            done = _cached_ids(arm)
            logger.info("arm %s: %d cached, running rest", arm, len(done))
            started = time.monotonic()
            with open(cache_path, "a", encoding="utf-8") as out:
                for i, q in enumerate(questions, 1):
                    if q.question_id in done and not args.force:
                        continue
                    t0 = time.monotonic()
                    try:
                        if arm == "A_dense":
                            result = arms_mod.arm_a_dense(q.raw, DENSE_DEPTH)
                        elif arm == "B_sparse":
                            result = arms_mod.arm_b_sparse(q.raw, SPARSE_DEPTH)
                        elif arm == "C_hybrid":
                            result = arms_mod.arm_c_hybrid(q.raw, HYBRID_DEPTH)
                        elif arm == "D_kg":
                            result = run_arm_d_kg(q.raw, KG_DIAGNOSTIC_LIMIT)
                        elif arm == "O_dense":
                            query, src = _oracle_query(q, registry)
                            result = run_oracle_dense(q.raw, query, ORACLE_DEPTH)
                            result["oracle_source"] = src
                        elif arm == "O_sparse":
                            query, src = _oracle_query(q, registry)
                            result = run_oracle_sparse(q.raw, query, ORACLE_DEPTH)
                            result["oracle_source"] = src
                        elif arm in ("V3_dense", "V3_sparse", "V3_hybrid", "V4_dense", "V4_sparse", "V4_hybrid"):
                            from evaluation.query_expansion import expand_query

                            # V4 = dedup'd expansion: identifiers already in the
                            # question are not re-appended (retriever diversity).
                            query, meta = expand_query(q.question, dedup=arm.startswith("V4"))
                            if arm.endswith("_dense"):
                                result = run_oracle_dense(q.raw, query, DENSE_DEPTH)
                            elif arm.endswith("_sparse"):
                                result = run_oracle_sparse(q.raw, query, SPARSE_DEPTH)
                            else:
                                result = run_expanded_hybrid(q.raw, query, HYBRID_DEPTH)
                            result["query_used"] = query
                            result["expansion_meta"] = meta
                        elif arm == "X_exact":
                            query, src = _identifier_query(q, registry)
                            if query is None:
                                result = {
                                    "chunk_ids": [],
                                    "chunk_ids_sparse": [],
                                    "kg_provisions": [],
                                    "kg_source": None,
                                    "latency_ms": 0,
                                    "error": "no numeric identifier",
                                    "retriever": "exact_identifier",
                                    "query_used": None,
                                    "oracle_source": None,
                                }
                            else:
                                result = run_exact_identifier(q.raw, query, EXACT_DEPTH)
                                result["oracle_source"] = src
                        else:
                            raise ValueError(f"unknown arm: {arm}")
                        result["arm"] = arm
                        result["question_id"] = q.question_id
                    except Exception as exc:
                        logger.error("arm %s q %s failed: %s", arm, q.question_id, exc)
                        result = {
                            "arm": arm,
                            "question_id": q.question_id,
                            "chunk_ids": [],
                            "kg_provisions": [],
                            "kg_source": None,
                            "latency_ms": int((time.monotonic() - t0) * 1000),
                            "error": f"{type(exc).__name__}: {exc}",
                            "retriever": "error",
                        }
                    out.write(json.dumps(result, ensure_ascii=False) + "\n")
                    out.flush()
                    if i % 10 == 0 or i == len(questions):
                        logger.info(
                            "arm %s %d/%d (%.0fs elapsed)",
                            arm,
                            i,
                            len(questions),
                            time.monotonic() - started,
                        )
            logger.info("arm %s complete", arm)
    logger.info("live retrieval phase done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
