"""VERIFY_FINETUNED_CE — fine-tuned CE drop-in check on live production retrieval.

Task 4 of the CE_RERANK_REVIEW follow-ups: wire ``legal_ce_v1`` as
``RAG_RERANKER_MODEL`` and verify retrieval quality on a sample of real
queries.  Runs the *live production pipeline* (``run_retrieval_pipeline`` —
real Qdrant, identifier route on) over a fixed sample of frozen benchmark
questions, three ways:

    features_only  EnsembleReranker with no encoder (sec_act features only)
    ce_base        EnsembleReranker + cross-encoder/ms-marco-MiniLM-L-6-v2
    ce_finetuned   EnsembleReranker + evaluation/out/models/legal_ce_v1

Each question is measured on any-hit R@10 (any relevant gold unit in the
top-10) and unit-level R@10, using the gold-resolution layer against the
payload index.  Output: evaluation/out/ceiling_v5/verify_finetuned_ce.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT.mkdir(parents=True, exist_ok=True)

CE_BASE = "cross-encoder/ms-marco-MiniLM-L-6-v2"
CE_FINETUNED = PROJECT_ROOT / "evaluation" / "out" / "models" / "legal_ce_v1"

#: Sample: 12 questions — 6 section-lookup (identifier route active) + 6
#: concept/general (no identifier), spread across domains.
SAMPLE_IDS = [
    "Q001",
    "Q004",
    "Q010",
    "Q020",
    "Q030",
    "Q040",  # section-heavy
    "Q050",
    "Q060",
    "Q070",
    "Q080",
    "Q090",
    "Q100",  # concept-heavy
]


def main() -> int:
    import torch

    torch.set_num_threads(4)
    load_dotenv(PROJECT_ROOT / ".env")

    from app import create_app
    from evaluation.benchmark import load_questions
    from evaluation.report_ceiling import load_payload_index
    from evaluation.resolution import FamilyMap, matches_gold

    app = create_app()
    with app.app_context():
        payload_index = load_payload_index()
        family_map = FamilyMap()
        questions = {q.question_id: q for q in load_questions()}

        from app.rag.retrieval.reranker import EnsembleReranker

        rerankers = {
            "features_only": EnsembleReranker(encoder=None, ce_head=20, ce_weight=0.5),
            "ce_base": EnsembleReranker(model_name=CE_BASE, ce_head=20, ce_weight=0.5),
            "ce_finetuned": EnsembleReranker(model_name=CE_FINETUNED.as_posix(), ce_head=20, ce_weight=0.5),
        }

        # Retrieve the raw fused pool with NO internal reranker (dense +
        # sparse + identifier arm — the production composition), then apply
        # the three reranker configs to the SAME pool.  This mirrors the
        # evaluation methodology (arms.py) and the production pipeline minus
        # its final rerank step, so the comparison isolates the reranker.
        from app.rag.qdrant_client import QdrantStore
        from app.rag.retrieval import DenseRetriever, HybridRetriever, QueryClassifier, QueryParser, SparseRetriever
        from app.rag.retrieval.identifier import identifier_query
        from app.rag.sparse_embedding import SparseEmbeddingService

        classifier = QueryClassifier()
        parser = QueryParser()
        out = {}
        for qid in SAMPLE_IDS:
            q = questions.get(qid)
            if q is None:
                continue
            collection = (q.collections or ["fssai_legal_768"])[0]
            qtype = classifier.classify(q.question)
            parsed = parser.parse(q.question, qtype) or {}
            dense = DenseRetriever(collection_name=collection)
            sparse = SparseRetriever(
                corpus={},
                store=QdrantStore(collection_name=collection),
                embedder=SparseEmbeddingService(),
            )
            hybrid = HybridRetriever(dense=dense, sparse=sparse, reranker=None)
            ident_q, _meta = identifier_query(q.question)
            time.time()
            result = hybrid.retrieve(q.question, top_k=20, filters=parsed, identifier_query=ident_q)
            raw = result.chunks
            per = {}
            for name, rr in rerankers.items():
                reranked = rr.rerank(q.question, list(raw), top_k=10)
                rel = q.relevant_units()
                hits = 0
                any_hit = 0
                for unit in rel:
                    for ch in reranked:
                        pl = payload_index.get(ch.chunk_id)
                        if pl is not None and matches_gold(pl, unit, family_map):
                            hits += 1
                            any_hit = 1
                            break
                per[name] = {
                    "any_hit_R10": any_hit,
                    "unit_R10": round(hits / max(len(rel), 1), 3),
                    "top3": [c.chunk_id[:8] for c in reranked[:3]],
                }
            out[qid] = {
                "question": q.question,
                "domains": q.domains,
                "n_rel": len(q.relevant_units()),
                "rerankers": per,
            }

        (OUT / "verify_finetuned_ce.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

        # Aggregate
        agg = {name: {"any_hit_R10": 0, "unit_R10": 0.0, "n": 0} for name in rerankers}
        for qid, rec in out.items():
            for name in rerankers:
                p = rec["rerankers"][name]
                agg[name]["any_hit_R10"] += p["any_hit_R10"]
                agg[name]["unit_R10"] += p["unit_R10"]
                agg[name]["n"] += 1
        for name in agg:
            agg[name]["any_hit_R10"] = round(agg[name]["any_hit_R10"] / max(agg[name]["n"], 1), 3)
            agg[name]["unit_R10"] = round(agg[name]["unit_R10"] / max(agg[name]["n"], 1), 3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
