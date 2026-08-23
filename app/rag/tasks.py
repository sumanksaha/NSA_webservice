(
    """Celery tasks for the RAG pipeline.

``retrieve_task`` wraps the Phase 1 retrieval pipeline (query
classification -> hybrid retrieval -> reranking -> logging) as a Celery task
so it can be dispatched asynchronously via QStash.

``embed_and_index_task`` wraps the Agent A corpus-ingestion pipeline (chunk ->
embed -> Qdrant upsert) as a Celery task for async batch embedding, following
the same pattern as ``retrieve_task`` / ``app/food_cell/tasks.py``.

Tasks are registered with Celery only when the Celery instance is
available; otherwise they remain plain functions (graceful degradation).
"""
    ""
)

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

# Lazy import so the module boots even when Celery isn't installed.
try:
    from celery_app import celery
except ImportError:
    celery = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Single configuration seam (Pattern A: Flask config in-context → env out).
from app.shared.config import cfg

# ---------------------------------------------------------------------------
# Retrieval cache (Legal_AI_implementation.md §12.1)
# Memoizes the deterministic, LLM-free retrieval step
# (QueryClassifier -> HybridRetriever.retrieve) so repeated identical legal
# questions skip the Qdrant round-trip within the TTL. Gated by
# RAG_RETRIEVAL_CACHE (default off — test-safe; the hash-chained audit is
# unaffected because RetrievalLogger.log still runs on every call, cache hit
# or not). cachetools is an optional dep — if missing the cache is
# silently disabled (graceful), mirroring the lazy-celery pattern above.
# Stdlib TTL + LRU cache (no external dependency): an OrderedDict of
# ``key -> (expires_at, SearchResult)``.  TTL and max-size are enforced on
# write, expiry is checked on read (lazy reaping, no sweeper thread).
_RETRIEVAL_CACHE_TTL = 600
_RETRIEVAL_CACHE_MAX = 512
_RETRIEVAL_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _retrieval_cache_enabled() -> bool:
    """Whether identical retrieval results are memoized (RAG_RETRIEVAL_CACHE).

    Flask config wins when an app context exists (so it can be toggled
    per-deploy); otherwise the env var is read — resolved via ``cfg``.
    """
    return _RETRIEVAL_CACHE_MAX > 0 and cfg.retrieval_cache


def _cache_get(key):
    """Return the cached value for *key*, or ``None`` (miss / expired).

    Fresh hits are promoted to most-recently-used (LRU).  Expiry is checked
    on read so stale entries are lazily reaped without a sweeper.
    """
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _RETRIEVAL_CACHE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if now >= expires_at:
            _RETRIEVAL_CACHE.pop(key, None)
            return None
        _RETRIEVAL_CACHE.move_to_end(key)
        return value


def _cache_put(key, value) -> None:
    """Store *value* under *key* with TTL, evicting the LRU entry if over cap."""
    with _CACHE_LOCK:
        _RETRIEVAL_CACHE[key] = (time.monotonic() + _RETRIEVAL_CACHE_TTL, value)
        if len(_RETRIEVAL_CACHE) > _RETRIEVAL_CACHE_MAX:
            _RETRIEVAL_CACHE.popitem(last=False)


def clear_retrieval_cache() -> None:
    """Drop every cached retrieval result (admin re-ingest, tests)."""
    with _CACHE_LOCK:
        _RETRIEVAL_CACHE.clear()


def _retrieval_cache_key(
    query: str,
    top_k: int,
    collection_name: str | None,
    filters: dict[str, Any] | None,
    query_type: str,
    legal_qt: str | None,
    identifier: dict[str, Any] | None,
) -> tuple | None:
    """Build a hashable cache key, or ``None`` if the inputs are uncacheable."""
    try:
        return (
            query.strip().lower(),
            int(top_k),
            collection_name or "",
            query_type,
            legal_qt or None,
            (identifier or {}).get("form"),
            json.dumps(filters or {}, sort_keys=True, default=str),
        )
    except (TypeError, ValueError):
        return None


def run_retrieval_pipeline(
    query: str,
    top_k: int = 10,
    collection_name: str | None = None,
    filters: dict[str, Any] | None = None,
    pipeline: str | None = None,
) -> dict[str, Any]:
    """Run the full Phase 1 retrieval pipeline for *query*.

    Dispatches: QueryClassifier -> HybridRetriever -> Reranker -> RetrievalLogger.

    Returns a JSON-serializable dict with ``chunks``, ``query_type``,
    ``latency_ms``, and ``error`` (if any).

    *pipeline* stamps the ``RAGQueryLog`` row with the calling pipeline
    (``"legacy"`` / ``"agent"``) for the rollout §8 A/B comparison —
    ``None`` leaves it unstamped.

    This is the plain (non-Celery) entry point so that tests and routes
    can call it without going through the task wrapper.
    """
    from app.rag.retrieval import QueryClassifier, QueryParser
    from app.rag.retrieval.factory import build_hybrid_retriever
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

    # 2. Classify the query into a legal query type for query-type-aware
    # reranking (CE_RERANK_REVIEW, STEP 7).  Different query types benefit
    # from different weight configurations: e.g., prohibition regresses with
    # hierarchy boosting (0.0 hierarchy weight), authority needs more CE head
    # coverage, cross-reference needs identifier/graph recovery.
    legal_qt = None
    if cfg.legal_query_typing:
        from app.rag.retrieval.legal_query_classifier import classify_legal_query

        legal_qt = classify_legal_query(query)

    # 5. Identifier route (2026-08-13, validated by the V5/V5.5 evaluation
    #    arc): build a lexical "{Act} section {N}" query from the identifiers
    #    detected in the question text, and hand it to the hybrid retriever
    #    as a parallel additive arm.  This is the production form of the
    #    single decisive lever measured offline (+13.3pp candidate-pool
    #    ceiling; after the section-stamp backfill it lifted the pool to
    #    100%).  Best-effort: no identifiers -> no arm; retrieval failure
    #    degrades to the plain hybrid result.
    identifier = None
    identifier_query = None
    if cfg.identifier_route:
        from app.rag.retrieval.identifier import identifier_query as build_ident

        identifier_query, identifier = build_ident(query)

    # 4. Retrieval stack — built by the composition root
    #    (app/rag/retrieval/factory.py): collection-aware dense, Qdrant-BM25
    #    sparse, ensemble/plain reranker, fused hybrid. One module owns the
    #    wiring; the historical inline assembly (and the wrong-collection bug
    #    class it bred) lives there now. Cached (§12.1): retrieval is
    #    deterministic and LLM-free, so an identical query repeated within
    #    the TTL skips the Qdrant round-trip; a fresh copy is returned on
    #    hits so the cached object is never mutated.
    hybrid = build_hybrid_retriever(collection_name)
    cache_key = (
        _retrieval_cache_key(
            query,
            top_k,
            collection_name,
            merged_filters,
            query_type.value,
            legal_qt,
            identifier,
        )
        if _retrieval_cache_enabled()
        else None
    )
    cached = None
    if cache_key is not None:
        cached = _cache_get(cache_key)
    if cached is not None:
        from app.rag.retrieval.result import SearchResult

        # Rebuild rather than return the shared object: copy the chunks list
        # (chunks themselves are read-only downstream) and flag the hit in
        # source/latency for the audit log + UI gauges.
        result = SearchResult(
            query=cached.query,
            query_type=cached.query_type,
            chunks=list(cached.chunks),
            total=cached.total,
            latency_ms=0,
            source="cache",
            error=cached.error,
        )
        logger.info("run_retrieval_pipeline: CACHE HIT query=%r top_k=%s", query, top_k)
    else:
        result = hybrid.retrieve(
            query,
            top_k=top_k,
            filters=merged_filters,
            identifier_query=identifier_query,
            query_type=legal_qt,
        )
        if cache_key is not None:
            _cache_put(cache_key, result)

    # 7. Log (runs on every call — cache hits included — so the hash-chained
    #    audit trail records each query invocation, not just misses).
    log = RetrievalLogger()
    log_entry = log.log(
        query=query,
        query_type=query_type.value,
        result=result,
        pipeline=pipeline,
    )

    latency_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "run_retrieval_pipeline: completed in %dms, %d chunks (identifier=%s)",
        latency_ms,
        len(result.chunks),
        (identifier or {}).get("form"),
    )

    # --- Parallel legal-structure & evidence layer (feature-flagged) ---
    # Applies legal identity parsing, cross-reference expansion, and
    # evidence-set selection to the retrieval result.  All are opt-in and
    # degrade gracefully — the production baseline (CE reranker) is
    # unchanged when all flags are off.
    legal_identities: list[dict[str, Any]] = []
    evidence_set_data: dict[str, Any] | None = None
    expanded_candidates: list[str] = []

    from app.rag.retrieval.legal_identity import _legal_identity_enabled, parse_legal_identity
    from app.rag.retrieval.reference_graph import _reference_expansion_enabled

    if _legal_identity_enabled() and result.chunks:
        legal_identities = [parse_legal_identity(c).to_dict() for c in result.chunks]

    # Optional: cross-reference candidate expansion (graph-based)
    if _reference_expansion_enabled() and result.chunks:
        try:
            from app.rag.retrieval.reference_graph import expand_candidates

            expanded_candidates = expand_candidates(result.chunks, top_k=10, depth=1)
            logger.info(
                "run_retrieval_pipeline: reference expansion found %d candidates",
                len(expanded_candidates),
            )
        except Exception as exc:
            logger.warning("run_retrieval_pipeline: reference expansion failed: %s", exc)

    # Optional: evidence-set selection
    if cfg.evidence_selector and result.chunks:
        try:
            from app.rag.retrieval.evidence_selector import select_evidence_set

            es = select_evidence_set(query, result.chunks, max_size=5, min_size=2)
            evidence_set_data = es.to_dict()
            logger.info(
                "run_retrieval_pipeline: evidence set selected %d items (%s)",
                len(es.items),
                [it["evidence_type"] for it in evidence_set_data["items"]],
            )
        except Exception as exc:
            logger.warning("run_retrieval_pipeline: evidence selector failed: %s", exc)

    return {
        "query": query,
        "query_type": query_type.value,
        "parsed": merged_filters,
        "identifier": identifier,
        "chunks": [c.to_dict() for c in result.chunks],
        "total": result.total,
        "latency_ms": latency_ms,
        "retrieval_latency_ms": result.latency_ms,
        "error": result.error,
        "log_id": str(log_entry.id) if log_entry else None,
        "legal_identities": legal_identities,
        "expanded_candidates": expanded_candidates,
        "evidence_set": evidence_set_data,
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
        summary["total"],
        summary["indexed"],
        summary["duplicates"],
        summary["failed"],
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
    pipeline: str | None = None,
) -> dict[str, Any]:
    """Run the full Phase 2 grounded-generation pipeline for *query*.

    If *chunks* is None, runs the Phase 1 retrieval pipeline first
    (via run_retrieval_pipeline) to obtain chunks, then generates
    a grounded response.  If chunks are provided, skips retrieval.

    *pipeline* is forwarded to the internal retrieval run (stamping the
    ``RAGQueryLog`` row) and echoed back in the result dict under
    ``"pipeline"`` (rollout §8 A/B).
    """
    from dataclasses import asdict

    from app.rag.generation import GroundedGenerationService
    from app.rag.retrieval.result import RetrievedChunk

    start = time.monotonic()

    if chunks is None:
        retrieval_data = run_retrieval_pipeline(
            query=query,
            top_k=top_k,
            collection_name=collection_name,
            filters=filters,
            pipeline=pipeline,
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
    if cfg.kg_fusion and chunk_objects:
        try:
            from kg.hybrid import (
                provisions_to_retrieved_chunks,
                rrf_fuse_chunks,
            )
            from kg.queries import LegalKGQueries, provisions_for_query

            provisions = provisions_for_query(query, LegalKGQueries(), limit=cfg.kg_max_provisions)
            logger.info("run_generation_pipeline: kg_contract provisions=%s", len(provisions))
            kg_chunks = provisions_to_retrieved_chunks(provisions, limit=cfg.kg_max_provisions)
            if kg_chunks:
                from app.rag.generation.context_builder import ContextBuilder

                slot_budget = ContextBuilder().max_context_chunks
                chunk_objects = rrf_fuse_chunks([chunk_objects, kg_chunks], rrf_k=60.0, top_k=slot_budget)
                kg_contract = {
                    "provisions": len(provisions),
                    "injected": len(kg_chunks),
                    "fused": True,
                }
                logger.info(
                    "run_generation_pipeline: RRF-fused %d KG contract provisions into context (slot budget %d)",
                    len(kg_chunks),
                    slot_budget,
                )
        except Exception as exc:
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
    elif cfg.kg_expansion and chunk_objects:
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
            kg_chunks = provisions_to_retrieved_chunks(kg_provisions, limit=cfg.kg_max_provisions)
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
                chunk_objects = rrf_fuse_chunks([chunk_objects, kg_chunks], rrf_k=60.0, top_k=slot_budget)
                logger.info(
                    "run_generation_pipeline: RRF-fused %d KG provisions into context (slot budget %d)",
                    len(kg_chunks),
                    slot_budget,
                )

    service = GroundedGenerationService()
    rag_response = service.generate(query, chunk_objects, query_type)

    # Phase 3 claim-level verification on the live path (2026-08-23): when
    # RAG_HALLUCINATION_DETECTOR is enabled (default), run the
    # HallucinationDetector chain (claims → evidence → citations → score)
    # over the generated answer and merge its verdict into the response.
    # Augments (never replaces) the heuristic ResponseSanitizer: sanitizer
    # flags are always kept, and claim-level hallucinations the sanitizer
    # missed are *escalated* into the top-level fields.  Best-effort by
    # design — never raises, so a detector failure cannot break a query.
    verification: dict[str, Any] | None = None
    if cfg.hallucination_detector and rag_response.answer and chunk_objects:
        try:
            from app.rag.verification import HallucinationDetector

            report = HallucinationDetector().detect(
                rag_response.answer,
                chunk_objects,
                citations=list(rag_response.citations),
            )
            extra_claims = [
                claim for claim in report.hallucinated_claims if claim not in rag_response.hallucinated_claims
            ]
            verification = {
                "enabled": True,
                "detected": report.detected,
                "groundedness_score": report.groundedness_score,
                "claims_total": len(report.claims),
                "claims_verified": len(report.verified_claims),
                "claims_unverified": len(report.unverified_claims),
                "llm_verified": report.llm_verified,
                "confidence": report.confidence,
                "escalated_claims": len(extra_claims),
            }
            # Escalate only — never de-escalate a sanitizer flag.
            if extra_claims:
                rag_response.hallucinated_claims = [
                    *rag_response.hallucinated_claims,
                    *extra_claims,
                ]
                rag_response.hallucination_detected = True
        except Exception as exc:
            logger.warning("run_generation_pipeline: hallucination detection failed: %s", exc)
            verification = {"enabled": True, "error": str(exc)}

    total_latency_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "run_generation_pipeline: query=%r chunks=%d groundedness=%s lat=%dms",
        query,
        len(chunk_objects),
        rag_response.groundedness_score,
        total_latency_ms,
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
        "verification": verification,
        "pipeline": pipeline or "legacy",
    }


def _build_reranker():
    """Backward-compatible shim — construction moved to the composition root."""
    from app.rag.retrieval.factory import build_reranker

    return build_reranker()


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
        query=query,
        chunks=chunks,
        query_type=query_type,
        top_k=top_k,
        collection_name=collection_name,
        filters=filters,
    )


def run_evaluate(dataset, pipeline_fn=None, eval_run_id=None, top_k=10):
    """Run batch RAG evaluation over a dataset (Phase 4)."""
    from app.rag.evaluation import EvalRunner, EvalStorage

    def _default_pipeline(query):
        return run_generation_pipeline(query=query, top_k=top_k, collection_name=None, filters=None)

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
        dataset=dataset,
        pipeline_fn=pipeline_fn,
        eval_run_id=eval_run_id,
        top_k=top_k,
    )


# Register as a Celery task if celery is available
if celery is not None:
    retrieve_task = celery.task(bind=True, name="rag.retrieve_task")(retrieve_task)  # type: ignore[assignment]
    embed_and_index_task = celery.task(bind=True, name="rag.embed_and_index_task")(embed_and_index_task)  # type: ignore[assignment]
    ingest_corpus_task = celery.task(bind=True, name="rag.ingest_corpus_task")(ingest_corpus_task)  # type: ignore[assignment]
    generate_task = celery.task(bind=True, name="rag.generate_task")(generate_task)  # type: ignore[assignment]
    evaluate_task = celery.task(bind=True, name="rag.evaluate_task")(evaluate_task)  # type: ignore[assignment]
