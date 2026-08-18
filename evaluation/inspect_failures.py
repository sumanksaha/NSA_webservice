"""Inspect specific failure classes from the k500 baseline checkpoint.

For each failing question of a target query type (Offence, Cross-reference,
Prohibition, Authority), re-run retrieval + reranking with full debug capture
in a single file, avoiding the need to re-evaluate all 150 questions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import torch
from dotenv import load_dotenv

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
from app.rag.retrieval.identifier import detect_act, detect_section, identifier_query
from app.rag.retrieval.reranker import EnsembleReranker
from app.rag.sparse_embedding import SparseEmbeddingService
from evaluation.benchmark import load_questions
from evaluation.report_ceiling import load_payload_index
from evaluation.resolution import FamilyMap, matches_gold

torch.set_num_threads(4)
load_dotenv(PROJECT_ROOT / ".env")

TOP_K = 10
POOL_K = 500
CHECKPOINT = PROJECT_ROOT / "evaluation/out/baseline_k500/ensemble_live_k500.checkpoint.jsonl"
OUTPUT = PROJECT_ROOT / "evaluation/out/baseline_k500/failure_debug.json"

TARGET_TYPES = {"Offence", "Cross-reference", "Prohibition", "Authority"}


def _minmax(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo == 0:
        return [0.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


def main() -> int:
    app = create_app()
    with app.app_context():
        payload_index = load_payload_index()
        family_map = FamilyMap()
        questions = {q.question_id: q for q in load_questions()}
        classifier = QueryClassifier()
        parser = QueryParser()

        reranker_on = EnsembleReranker(
            model_name=str(app.config.get("RAG_RERANKER_MODEL", "")),
            ce_head=int(str(app.config.get("RAG_ENSEMBLE_CE_HEAD", "30"))),
            ce_weight=float(str(app.config.get("RAG_ENSEMBLE_CE_WEIGHT", "0.5"))),
        )
        reranker_off = Reranker(model_name=str(app.config.get("RAG_RERANKER_MODEL", "")))

        # Load baseline checkpoint
        baseline_entries: dict[str, dict] = {}
        try:
            with open(CHECKPOINT, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rec = json.loads(line)
                        baseline_entries[rec.get("question_id", "")] = rec
        except FileNotFoundError:
            return 1

        # Identify failing questions for target types
        targets = []
        for qid, entry in baseline_entries.items():
            qtypes = entry.get("query_types", [])
            if not any(t in TARGET_TYPES for t in qtypes):
                continue
            on = entry.get("rerankers", {}).get("ensemble_on", {})
            if "error" in on:
                continue
            hit_at_10 = on.get("unit_hits", {}).get("10", 0)
            if hit_at_10 == 0:
                targets.append((qid, entry, qtypes))

        encoder = reranker_on._get_encoder()
        results = []

        for qid, entry, qtypes in targets:
            q = questions.get(qid)
            if q is None:
                continue

            collection = (q.collections or ["fssai_legal_768"])[0]
            dense = DenseRetriever(collection_name=collection)
            sparse = SparseRetriever(
                corpus={},
                store=QdrantStore(collection_name=collection),
                embedder=SparseEmbeddingService(),
            )
            hybrid = HybridRetriever(dense=dense, sparse=sparse, reranker=None)

            qtype = classifier.classify(q.question)
            parsed = parser.parse(q.question, qtype) or {}
            ident_q, _meta = identifier_query(q.question)
            result = hybrid.retrieve(q.question, top_k=POOL_K, filters=parsed, identifier_query=ident_q)
            raw = result.chunks

            q_sec, _ = detect_section(q.question)
            q_act = detect_act(q.question)

            off_ranked = reranker_off.rerank(q.question, list(raw), top_k=TOP_K)
            on_ranked = reranker_on.rerank(q.question, list(raw), top_k=TOP_K)

            head_chunks = sorted(raw, key=lambda c: c.score, reverse=True)[: reranker_on.ce_head]

            # Determine gold
            rel = q.relevant_units()
            gold_provision = None
            gold_doc_id = None
            for unit in rel:
                for ch in raw:
                    pl = payload_index.get(ch.chunk_id)
                    if pl is not None and matches_gold(pl, unit, family_map):
                        gold_provision = pl
                        gold_doc_id = pl.get("document_id", "?")
                        break
                if gold_provision:
                    break

            # Find gold ranks
            def _find_gold_rank(reranked, gold):
                if gold is None:
                    return None
                for i, ch in enumerate(reranked):
                    pl = payload_index.get(ch.chunk_id)
                    if pl is not None and pl.get("document_id") == gold.get("document_id"):
                        return i
                return None

            gold_raw_rank = None
            for i, ch in enumerate(raw):
                pl = payload_index.get(ch.chunk_id)
                if pl is not None and gold_provision and pl.get("document_id") == gold_provision.get("document_id"):
                    gold_raw_rank = i
                    break

            gold_off_rank = _find_gold_rank(off_ranked, gold_provision)
            gold_on_rank = _find_gold_rank(on_ranked, gold_provision)

            # Head chunk analysis
            head_analysis = []
            for i, c in enumerate(head_chunks):
                pl = payload_index.get(c.chunk_id) or {}
                head_analysis.append({
                    "rank": i,
                    "chunk_id": c.chunk_id,
                    "base_score": round(c.score, 6),
                    "sec_match": reranker_on._section_match(q_sec, c.section_number),
                    "act_match": reranker_on._act_match(q_act, c),
                    "hierarchy_level": c.hierarchy_level,
                    "hierarchy_boost": reranker_on._hierarchy_boost(c.hierarchy_level),
                    "document_id": pl.get("document_id", "?"),
                    "act_name": pl.get("act_name", "?"),
                    "section_number": pl.get("section_number", "?"),
                    "provision_type": pl.get("provision_type", "?"),
                    "authority": pl.get("authority", "?"),
                })

            # CE scores
            ce_vals = None
            if encoder is not None:
                pairs = [(q.question, ch.text) for ch in head_chunks]
                try:
                    ce_vals = [float(s) for s in encoder.predict(pairs)]
                except Exception:
                    ce_vals = None

            if ce_vals:
                for i, cv in enumerate(ce_vals):
                    if i < len(head_analysis):
                        head_analysis[i]["ce_score"] = round(cv, 6)

            result_rec = {
                "question_id": qid,
                "query_types": qtypes,
                "question": q.question,
                "gold": {
                    "document_id": gold_doc_id,
                    "pool_rank": gold_raw_rank,
                    "off_rank": gold_off_rank,
                    "on_rank": gold_on_rank,
                    "pool_cover": entry.get("pool_cover", False),
                },
                "head_chunks": head_analysis,
                "ce_scored": encoder is not None,
            }
            results.append(result_rec)

            # Console output

            if head_analysis:
                ce_norm = _minmax(ce_vals) if ce_vals else None
                for i, c in enumerate(head_chunks[:10]):
                    head_analysis[i].get("ce_score", "?")
                    pl = payload_index.get(c.chunk_id) or {}
                    pl.get("document_id", "?")[:25]
                    "Y" if head_analysis[i]["sec_match"] else "N"
                    "Y" if head_analysis[i]["act_match"] else "N"
                    hier = head_analysis[i]["hierarchy_boost"]
                    final = c.score
                    if ce_norm and i < len(ce_norm):
                        final += reranker_on.ce_weight * ce_norm[i]
                    final += reranker_on._W_HIERARCHY * hier

            if gold_on_rank is not None and gold_on_rank >= 10:
                winner = on_ranked[0]
                payload_index.get(winner.chunk_id) or {}

        try:
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            with open(OUTPUT, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
        except OSError:
            pass

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
