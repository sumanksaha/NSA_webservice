"""Celery tasks for the RAG pipeline.

``retrieve_task`` wraps the Phase 1 retrieval pipeline (query
classification -> hybrid retrieval -> reranking -> logging) as a Celery task
so it can be dispatched asynchronously via QStash.

``embed_and_index_task`` wraps the Agent A corpus-ingestion pipeline (chunk ->
embed -> Qdrant upsert) as a Celery task for async batch embedding, following
the same pattern as ``retrieve_task`` / ``app/food_cell/tasks.py``.

Tasks are registered with Celery only when the Celery instance is
available; otherwise they remain plain functions (graceful degradation).
"""""

from __future__ import annotations

import logging
import os
import time
from typing import Any

# Lazy import so the module boots even when Celery isn't installed.
try:
    from celery_app import celery
except ImportError:
    celery = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def run_retrieval_pipeline(
    query: str,
    top_k: int = 10,
    collection_name: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full Phase 1 retrieval pipeline for *query*.

    Dispatches: QueryClassifier -> HybridRetriever -> Reranker -> RetrievalLogger.

    Returns a JSON-serializable dict with ``chunks``, ``query_type``,
    ``latency_ms``, and ``error`` (if any).

    This is the plain (non-Celery) entry point so that tests and routes
    can call it without going through the task wrapper.
    """
    from app.rag.retrieval import (
        DenseRetriever,
        HybridRetriever,
        QueryClassifier,
        QueryParser,
        Reranker,
        SparseRetriever,
    )
    from app.rag.retrieval.logger import RetrievalLogger

    logger.info("run_retrieval_pipeline: starting for query=%r top_k=%s", query, top_k)

    start = time.monotonic()

    # 1. Classify + parse
    classifier = QueryClassifier()
    query_type = classifier.classify(query)
    parser = QueryParser()
    parsed = parser.parse(query, query_type)
    # Merge parsed filters with caller-provided filters
    merged_filters = {**(parsed or {}), **(filters or {})}

    # 2. Dense retriever (Qdrant)
    dense = DenseRetriever(collection_name=collection_name or "")

    # 3. Sparse retriever — real BM25 via Qdrant sparse vectors (fastembed
    #    Qdrant/bm25).  On dense-only collections SparseRetriever degrades to
    #    its in-memory rapidfuzz path (empty corpus -> no sparse results),
    #    which is the pre-BM25 behaviour.
    from app.rag.qdrant_client import QdrantStore
    from app.rag.sparse_embedding import SparseEmbeddingService

    sparse = SparseRetriever(
        corpus={},
        store=QdrantStore(),
        embedder=SparseEmbeddingService(),
    )

    # 4. Reranker
    reranker = Reranker()

    # 5. Hybrid retrieval
    hybrid = HybridRetriever(dense=dense, sparse=sparse, reranker=reranker)
    result = hybrid.retrieve(query, top_k=top_k, filters=merged_filters)

    # 6. Log
    log = RetrievalLogger()
    log_entry = log.log(query=query, query_type=query_type.value, result=result)

    latency_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "run_retrieval_pipeline: completed in %dms, %d chunks",
        latency_ms,
        len(result.chunks),
    )

    return {
        "query": query,
        "query_type": query_type.value,
        "parsed": merged_filters,
        "chunks": [c.to_dict() for c in result.chunks],
        "total": result.total,
        "latency_ms": latency_ms,
        "retrieval_latency_ms": result.latency_ms,
        "error": result.error,
        "log_id": str(log_entry.id) if log_entry else None,
    }


def retrieve_task(
    self,
    query: str,
    top_k: int = 10,
    collection_name: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Celery task wrapper around :func:`run_retrieval_pipeline`.

    Registered with ``bind=True`` so that *self* (the Celery task instance)
    is injected automatically -- following the pattern in
    ``app/food_cell/tasks.py`` and ``app/ai_assistant/tasks.py``.
    """
    return run_retrieval_pipeline(
        query=query,
        top_k=top_k,
        collection_name=collection_name,
        filters=filters,
    )


def run_embed_and_index(
    document_id: str,
    text: str,
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Chunk, embed, and index a legal document into Qdrant (Agent A, Day 3).

    Wires :class:`app.rag.qdrant_indexer.QdrantIndexer` (which composes the
    ``Chunker`` -> ``EmbeddingService`` -> ``QdrantStore`` pipeline) and
    returns the JSON-serializable ``ChunkIngestionResult`` dict.

    Degrades gracefully: when ``qdrant-client`` / ``sentence-transformers``
    are missing or unconfigured, the result dict carries ``ok: False`` and a
    descriptive ``errors`` list instead of raising.

    This is the plain (non-Celery) entry point so tests and routes can call
    it without going through the task wrapper.
    """
    from app.rag.qdrant_indexer import QdrantIndexer

    logger.info("run_embed_and_index: indexing document_id=%r (%d chars)", document_id, len(text or ""))

    doc_meta = dict(document or {})
    # ``document_id`` is the authoritative identifier — always stamp the
    # payload with it so Qdrant filter deletes work reliably.
    doc_meta["document_id"] = document_id

    result = QdrantIndexer().index_document(text, doc_meta)
    logger.info(
        "run_embed_and_index: document_id=%r chunk_count=%s points_upserted=%s errors=%s",
        document_id,
        result.chunk_count,
        result.points_upserted,
        len(result.errors),
    )
    return result.to_dict()


def embed_and_index_task(
    self,
    document_id: str,
    text: str,
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Celery task wrapper around :func:`run_embed_and_index`.

    Registered with ``bind=True`` so *self* (the Celery task instance) is
    injected automatically -- following the pattern in ``app/food_cell/tasks.py``.
    """
    return run_embed_and_index(document_id=document_id, text=text, document=document)


def run_ingest_corpus(
    corpus_dir: str,
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest every supported file under ``corpus_dir`` (Agent A, Day 4).

    Delegates to :func:`app.rag.ingestion.ingest_corpus_dir` and returns the
    JSON-serializable summary dict.  Designed as the QStash-scheduled batch
    ingestion entry point (``rag.ingest_corpus_task``).
    """
    from app.rag.ingestion import ingest_corpus_dir

    logger.info("run_ingest_corpus: scanning corpus_dir=%r", corpus_dir)
    summary = ingest_corpus_dir(corpus_dir, document)
    logger.info(
        "run_ingest_corpus: total=%s indexed=%s duplicates=%s failed=%s",
        summary["total"], summary["indexed"], summary["duplicates"], summary["failed"],
    )
    return summary


def ingest_corpus_task(
    self,
    corpus_dir: str,
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Celery task wrapper around :func:`run_ingest_corpus` (bind=True)."""
    return run_ingest_corpus(corpus_dir=corpus_dir, document=document)




def run_generation_pipeline(
    query: str,
    chunks: list[dict[str, Any]] | None = None,
    query_type: str = "",
    top_k: int = 10,
    collection_name: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full Phase 2 grounded-generation pipeline for *query*.

    If *chunks* is None, runs the Phase 1 retrieval pipeline first
    (via run_retrieval_pipeline) to obtain chunks, then generates
    a grounded response.  If chunks are provided, skips retrieval.
    """
    from dataclasses import asdict
    from app.rag.retrieval.result import RetrievedChunk
    from app.rag.generation import GroundedGenerationService

    start = time.monotonic()

    if chunks is None:
        retrieval_data = run_retrieval_pipeline(
            query=query, top_k=top_k,
            collection_name=collection_name, filters=filters,
        )
        raw_chunks = retrieval_data.get("chunks", [])
        query_type = retrieval_data.get("query_type", query_type)
    else:
        retrieval_data = {}
        raw_chunks = chunks

    chunk_objects: list[RetrievedChunk] = []
    for raw in raw_chunks:
        if isinstance(raw, RetrievedChunk):
            chunk_objects.append(raw)
        elif isinstance(raw, dict):
            chunk_objects.append(RetrievedChunk.from_dict(raw))

    # KG contract fusion (2026-08-12, validated by the offline fusion
    # experiment): when RAG_KG_FUSION is enabled, run the graph-RAG
    # retrieval contract (query -> provisions) and RRF-fuse those provisions
    # into the ranked context — the production equivalent of eval arm G
    # (RRF(dense, sparse, KG-contract)), which showed a significant Recall@10
    # gain over tail-concatenation.  The contract's provisions are
    # independent of the retrieved chunk IDs (query-to-graph, not
    # chunk-to-graph), so they can surface gold provisions vector retrieval
    # missed.  Best-effort by design — never raises, so a missing or
    # unreachable Neo4j keeps the pipeline functional.  When the contract
    # injects provisions, the chunk-expansion block below is skipped (the
    # two KG paths are alternatives — fusing both would re-fuse the list).
    kg_contract: dict[str, Any] | None = None
    if _kg_fusion_enabled() and chunk_objects:
        try:
            from kg.hybrid import (
                provisions_to_retrieved_chunks,
                rrf_fuse_chunks,
            )
            from kg.queries import LegalKGQueries, provisions_for_query

            provisions = provisions_for_query(
                query, LegalKGQueries(), limit=_kg_max_provisions()
            )
            logger.info(
                "run_generation_pipeline: kg_contract provisions=%s", len(provisions)
            )
            kg_chunks = provisions_to_retrieved_chunks(
                provisions, limit=_kg_max_provisions()
            )
            if kg_chunks:
                from app.rag.generation.context_builder import ContextBuilder

                slot_budget = ContextBuilder().max_context_chunks
                chunk_objects = rrf_fuse_chunks(
                    [chunk_objects, kg_chunks], rrf_k=60.0, top_k=slot_budget
                )
                kg_contract = {
                    "provisions": len(provisions),
                    "injected": len(kg_chunks),
                    "fused": True,
                }
                logger.info(
                    "run_generation_pipeline: RRF-fused %d KG contract provisions "
                    "into context (slot budget %d)",
                    len(kg_chunks), slot_budget,
                )
        except Exception as exc:  # noqa: BLE001 - best-effort by design
            logger.warning("run_generation_pipeline: kg contract fusion failed: %s", exc)
            kg_contract = {"error": str(exc), "provisions": 0, "injected": 0, "fused": False}

    # KG graph expansion (Option F — 2026-08-11; wired into generation
    # 2026-08-12): when RAG_KG_EXPANSION is enabled, expand the retrieved
    # chunk IDs through the Neo4j legal KG into structured legal context
    # (provisions, domains, temporal status, authorities, cross-refs) and
    # inject the provisions into the LLM prompt as additional [Source n]
    # blocks. Best-effort by design — never raises, so a missing or
    # unreachable Neo4j keeps the pipeline functional.
    kg_expansion: dict[str, Any] | None = None
    # Skip the chunk-expansion path when contract fusion already injected
    # provisions: the two KG sources are alternatives, and re-fusing the
    # already-fused list would muddle ordering/scores (reviewer fix
    # 2026-08-12).
    if (kg_contract or {}).get("injected", 0) > 0:
        pass
    elif _kg_expansion_enabled() and chunk_objects:
        from kg.hybrid import KGContextExpander, provisions_to_retrieved_chunks

        kg_expansion = KGContextExpander().expand_chunks(c.chunk_id for c in chunk_objects)
        logger.info(
            "run_generation_pipeline: kg_expansion matched_chunks=%s provisions=%s error=%s",
            kg_expansion.get("matched_chunks", 0),
            len(kg_expansion.get("provisions", [])),
            kg_expansion.get("error"),
        )
        kg_provisions = kg_expansion.get("provisions") or []
        if kg_provisions:
            kg_chunks = provisions_to_retrieved_chunks(
                kg_provisions, limit=_kg_max_provisions()
            )
            if kg_chunks:
                # Repaired candidate fusion (2026-08-12): instead of
                # tail-appending KG evidence after the retrieved top-k, fuse
                # the retrieved chunks and the KG provision chunks with
                # Reciprocal Rank Fusion so KG evidence interleaves by merit
                # (its KG retrieval rank) rather than always ranking last.
                # The prompt keeps the same slot budget as
                # ContextBuilder.max_context_chunks.
                from app.rag.generation.context_builder import ContextBuilder
                from kg.hybrid import rrf_fuse_chunks

                slot_budget = ContextBuilder().max_context_chunks
                chunk_objects = rrf_fuse_chunks(
                    [chunk_objects, kg_chunks], rrf_k=60.0, top_k=slot_budget
                )
                logger.info(
                    "run_generation_pipeline: RRF-fused %d KG provisions into context (slot budget %d)",
                    len(kg_chunks), slot_budget,
                )

    service = GroundedGenerationService()
    rag_response = service.generate(query, chunk_objects, query_type)

    total_latency_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "run_generation_pipeline: query=%r chunks=%d groundedness=%s lat=%dms",
        query, len(chunk_objects), rag_response.groundedness_score, total_latency_ms,
    )

    return {
        "query": rag_response.query,
        "query_type": rag_response.query_type,
        "answer": rag_response.answer,
        "citations": [asdict(c) for c in rag_response.citations],
        "retrieved_chunks": [c.to_dict() for c in rag_response.retrieved_chunks],
        "groundedness_score": rag_response.groundedness_score,
        "hallucination_detected": rag_response.hallucination_detected,
        "hallucinated_claims": rag_response.hallucinated_claims,
        "confidence": rag_response.confidence,
        "retrieval_latency_ms": retrieval_data.get("retrieval_latency_ms", 0),
        "generation_latency_ms": rag_response.generation_latency_ms,
        "total_latency_ms": total_latency_ms,
        "llm_model": rag_response.llm_model,
        "prompt_tokens": rag_response.prompt_tokens,
        "completion_tokens": rag_response.completion_tokens,
        "token_usage": rag_response.token_usage,
        "debug": rag_response.debug,
        "kg_expansion": kg_expansion,
        "kg_contract": kg_contract,
    }


def _kg_max_provisions() -> int:
    """Resolve the KG provision cap (Flask config, else env var, default 5)."""
    try:
        from flask import current_app

        if current_app:
            return int(current_app.config.get("RAG_KG_MAX_PROVISIONS", 5))
    except Exception:  # noqa: BLE001 - no app context / not installed
        pass
    try:
        return int(os.environ.get("RAG_KG_MAX_PROVISIONS", "5"))
    except ValueError:
        return 5


def _kg_expansion_enabled() -> bool:
    """Resolve the RAG_KG_EXPANSION flag (Flask config, else env var, default off).

    Mirrors the ``_full_enrichment_enabled()`` pattern in
    ``app/rag/ingestion.py`` — Flask config wins when an app context exists
    (so the value can be toggled per-deploy), otherwise the env var is read.
    """
    return _flag_enabled("RAG_KG_EXPANSION")


def _kg_fusion_enabled() -> bool:
    """Resolve the RAG_KG_FUSION flag (Flask config, else env var, default off)."""
    return _flag_enabled("RAG_KG_FUSION")


def _flag_enabled(name: str) -> bool:
    """Shared bool-flag resolver: Flask config wins, else the env var."""
    try:
        from flask import current_app

        if current_app:
            return bool(current_app.config.get(name, False))
    except Exception:  # noqa: BLE001 - no app context / not installed
        pass
    return os.environ.get(name, "false").lower() == "true"


def generate_task(
    self,
    query: str,
    chunks: list[dict[str, Any]] | None = None,
    query_type: str = "",
    top_k: int = 10,
    collection_name: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Celery task wrapper around run_generation_pipeline (bind=True)."""
    return run_generation_pipeline(
        query=query, chunks=chunks, query_type=query_type,
        top_k=top_k, collection_name=collection_name, filters=filters,
    )


def run_evaluate(dataset, pipeline_fn=None, eval_run_id=None, top_k=10):
    """Run batch RAG evaluation over a dataset (Phase 4)."""
    from app.rag.evaluation import EvalRunner, EvalStorage

    def _default_pipeline(query):
        return run_generation_pipeline(
            query=query, top_k=top_k, collection_name=None, filters=None
        )

    fn = _default_pipeline
    if pipeline_fn:
        try:
            import importlib
            mod_path, _, attr = pipeline_fn.partition(":")
            module = importlib.import_module(mod_path)
            fn = getattr(module, attr)
        except Exception as exc:
            logger.error("run_evaluate: pipeline_fn load failed: %s", exc)
            fn = _default_pipeline

    runner = EvalRunner(pipeline_fn=fn, storage=EvalStorage())
    entries = [dict(d) for d in dataset]
    return runner.evaluate_batch(entries, eval_run_id=eval_run_id, persist=True)


def evaluate_task(self, dataset, pipeline_fn=None, eval_run_id=None, top_k=10):
    """Celery task wrapper around run_evaluate (bind=True)."""
    return run_evaluate(
        dataset=dataset, pipeline_fn=pipeline_fn,
        eval_run_id=eval_run_id, top_k=top_k,
    )

# Register as a Celery task if celery is available
if celery is not None:
    retrieve_task = celery.task(bind=True, name="rag.retrieve_task")(retrieve_task)  # type: ignore[assignment]
    embed_and_index_task = celery.task(bind=True, name="rag.embed_and_index_task")(embed_and_index_task)  # type: ignore[assignment]
    ingest_corpus_task = celery.task(bind=True, name="rag.ingest_corpus_task")(ingest_corpus_task)  # type: ignore[assignment]
    generate_task = celery.task(bind=True, name="rag.generate_task")(generate_task)  # type: ignore[assignment]
    evaluate_task = celery.task(bind=True, name="rag.evaluate_task")(evaluate_task)  # type: ignore[assignment]