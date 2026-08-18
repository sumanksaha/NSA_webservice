"""Phase 2 — LLM answer generation: oracle + retrieved conditions.

Usage:
    python -m evaluation.run_generation [--condition oracle] [--shard 1/8]

Conditions (protocol §12, extended 2026-08-12):
    oracle        — LLM given the gold evidence chunks (perfect retrieval)
    retrieved     — LLM given the ARM F (dense+sparse+KG+rerank) evidence
    retrieved_kg  — ARM F evidence + KG retrieval-contract provisions
                    RRF-fused into the prompt (measures the true
                    answer-level value of the KG contract fusion promoted
                    into production behind ``RAG_KG_FUSION``)

All three use the production ``GroundedGenerationService`` unchanged (real
LLM, default prompt, temperature 0.1).  ``retrieved_kg`` additionally injects
KG provisions via the production ``provisions_to_retrieved_chunks`` helper
and records their synthetic payloads so answer grading can resolve
KG-derived citations.  Results cache per shard to
``evaluation/out/raw/gen_<condition>_s<shard>.jsonl`` and are resumable.  A
small inter-call delay avoids OpenRouter free-tier rate limits.
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
logger = logging.getLogger("eval.generation")

DELAY_SECONDS = 3.0


def _load_arm_f(question_id: str) -> dict | None:
    from evaluation.config import RAW_DIR

    p = RAW_DIR / "F_dense_sparse_kg_rerank.jsonl"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue  # tolerate a corrupt line from a past concurrent run
            if rec.get("question_id") == question_id:
                return rec
    return None


def _chunk_from_payload(pid: str, payload: dict):
    from app.rag.retrieval.result import RetrievedChunk

    return RetrievedChunk(
        chunk_id=str(pid),
        score=1.0,
        text=str(payload.get("chunk_text") or ""),
        section_number=payload.get("section_number"),
        document_title=payload.get("document_title") or "",
        document_type=payload.get("document_type") or "",
        authority=payload.get("authority") or "",
        chunk_index=payload.get("chunk_index") or 0,
        hierarchy_level=payload.get("hierarchy_level") or 0,
        parent_chunk_id=payload.get("parent_chunk_id"),
    )


def _oracle_chunks(question: Any, payload_index: dict, family_map) -> list:  # noqa: F821 - typing.Any
    """Gold evidence chunks: one covering point per relevant gold unit."""
    from evaluation.resolution import gold_in_corpus

    units = question.relevant_units()
    corpus = gold_in_corpus(units, payload_index, family_map)
    chunks = []
    for unit in units:
        pts = corpus["unit_points"].get(unit.provision_id, [])
        if pts:
            chunks.append(_chunk_from_payload(pts[0], payload_index[pts[0]]))
        if len(chunks) >= 8:
            break
    return chunks


def _retrieved_chunks(arm_f: dict, payload_index: dict) -> list:
    chunks = []
    for cid in arm_f.get("chunk_ids", [])[:20]:
        payload = payload_index.get(str(cid))
        if payload is not None:
            chunks.append(_chunk_from_payload(cid, payload))
    return chunks


def _kg_evidence(arm_f: dict, payload_index: dict, query: str = ""):
    """ARM F chunks + KG provisions (mirrors production wiring).

    Mirrors ``app/rag/tasks.py::run_generation_pipeline``: the KG retrieval
    *contract* (query -> provisions via ``kg.queries.provisions_for_query``)
    is RRF-fused into the retrieved top-k — the production equivalent of
    eval arm G.  Returns ``(chunks, kg_payloads, kg_meta)`` where
    ``kg_payloads`` maps each KG chunk id to a synthetic payload
    (act_name / section_number / chunk_text) so the answer grader can
    resolve KG-derived citations against gold exactly like real payload
    chunks.
    """
    from evaluation.config import KG_CONTEXT_SLOTS
    from kg.hybrid import provisions_to_retrieved_chunks, rrf_fuse_chunks

    retrieved = _retrieved_chunks(arm_f, payload_index)
    try:
        from kg.queries import LegalKGQueries, provisions_for_query

        provisions = provisions_for_query(query, LegalKGQueries(), limit=KG_CONTEXT_SLOTS)
    except Exception as exc:
        return retrieved, {}, {"error": str(exc), "provisions": 0}
    from app.rag.generation.context_builder import ContextBuilder

    kg_chunks = provisions_to_retrieved_chunks(provisions, limit=KG_CONTEXT_SLOTS)
    # Repaired candidate fusion (2026-08-12): RRF-fuse the retrieved chunks
    # and the KG provision chunks (mirrors the production wiring in
    # app/rag/tasks.py) so KG evidence interleaves by merit instead of being
    # tail-appended after the retrieved top-k.
    slot_budget = ContextBuilder().max_context_chunks
    chunks = rrf_fuse_chunks([retrieved, kg_chunks], rrf_k=60.0, top_k=slot_budget)

    kg_payloads = {}
    for c in kg_chunks:
        kg_payloads[c.chunk_id] = {
            "act_name": c.document_title,
            "section_number": c.section_number,
            "chunk_text": c.text,
            "authority": c.authority,
            "document_type": c.document_type,
        }
    kg_meta = {
        "provisions": len(provisions),
        "injected": len(kg_chunks),
        "error": None,
    }
    return chunks, kg_payloads, kg_meta


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from evaluation.benchmark import load_questions
    from evaluation.config import OUT_DIR, RAW_DIR, write_run_config
    from evaluation.resolution import FamilyMap, build_payload_index

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--condition", default="both",
        choices=["oracle", "retrieved", "retrieved_kg", "both"],
    )
    parser.add_argument("--shard", default="1/1", help="e.g. 2/4 -> questions 38..75")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_run_config()

    conditions = ["oracle", "retrieved", "retrieved_kg"] if args.condition == "both" else [args.condition]

    with app.app_context():
        questions = load_questions()
        collections = [
            app.config.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
            app.config.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
        ]
        payload_index = build_payload_index(
            lambda coll: _store(coll), list(dict.fromkeys(collections))
        )
        family_map = FamilyMap()

        # shard slice
        idx_str, n_str = args.shard.split("/")
        shard_idx, n_shards = int(idx_str), int(n_str)
        qs = questions[shard_idx - 1::n_shards] if n_shards > 1 else questions
        logger.info("shard %s: %d questions", args.shard, len(qs))

        from app.rag.generation import GroundedGenerationService

        service = GroundedGenerationService()
        for condition in conditions:
            # Shard-private cache file: parallel shards never append to the
            # same file (avoids the concurrent-append corruption seen in the
            # retrieval phase).  The report phase globs gen_*.jsonl.
            cache_path = RAW_DIR / f"gen_{condition}_s{shard_idx}.jsonl"
            done: set[str] = set()
            if cache_path.exists() and not args.force:
                with open(cache_path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            done.add(json.loads(line)["question_id"])
                        except Exception:
                            continue
            with open(cache_path, "a", encoding="utf-8") as out:
                for q in qs:
                    if q.question_id in done:
                        continue
                    kg_payloads: dict = {}
                    kg_meta: dict | None = None
                    if condition == "oracle":
                        chunks = _oracle_chunks(q, payload_index, family_map)
                    elif condition == "retrieved_kg":
                        arm_f = _load_arm_f(q.question_id)
                        chunks, kg_payloads, kg_meta = _kg_evidence(arm_f or {}, payload_index, query=q.question)
                    else:
                        arm_f = _load_arm_f(q.question_id)
                        chunks = _retrieved_chunks(arm_f or {}, payload_index)
                    t0 = time.monotonic()
                    try:
                        resp = service.generate(q.question, chunks, query_type="")
                        rec = {
                            "question_id": q.question_id,
                            "condition": condition,
                            "answer": resp.answer,
                            "citations": [c.chunk_id for c in resp.citations],
                            "evidence_chunk_ids": [c.chunk_id for c in chunks],
                            "n_evidence_chunks": len(chunks),
                            "kg_payloads": kg_payloads,
                            "kg_expansion": kg_meta,
                            "llm_model": resp.llm_model,
                            "groundedness_score": resp.groundedness_score,
                            "hallucination_detected": resp.hallucination_detected,
                            "hallucinated_claims": resp.hallucinated_claims,
                            "generation_latency_ms": resp.generation_latency_ms,
                            "error": (resp.debug or {}).get("error"),
                        }
                    except Exception as exc:
                        rec = {
                            "question_id": q.question_id,
                            "condition": condition,
                            "answer": "",
                            "citations": [],
                            "evidence_chunk_ids": [c.chunk_id for c in chunks],
                            "n_evidence_chunks": len(chunks),
                            "kg_payloads": kg_payloads,
                            "kg_expansion": kg_meta,
                            "llm_model": "",
                            "groundedness_score": 0.0,
                            "hallucination_detected": False,
                            "hallucinated_claims": [],
                            "generation_latency_ms": int((time.monotonic() - t0) * 1000),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out.flush()
                    logger.info(
                        "gen %s %s done (%.1fs, err=%s)",
                        condition, q.question_id, time.monotonic() - t0, rec.get("error"),
                    )
                    time.sleep(DELAY_SECONDS)
    logger.info("generation phase done")
    return 0


def _store(collection: str):
    from app.rag.qdrant_client import QdrantStore

    return QdrantStore(collection_name=collection)


if __name__ == "__main__":
    raise SystemExit(main())
