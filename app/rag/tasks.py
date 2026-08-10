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
    }


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