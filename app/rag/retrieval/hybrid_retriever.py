"""Hybrid retriever — dense + sparse retrieval with Reciprocal Rank Fusion.

Combines the results of :class:`DenseRetriever` (Qdrant vector search) and
:class:`SparseRetriever` (rapidfuzz fuzzy matching) using Reciprocal Rank
Fusion (RRF), a standard technique that is parameter-light and robust to
score-scale differences between the two methods.

RRF formula::

    score(chunk) = sum( 1 / (rank(chunk, method) + k) )

where ``k`` is the RRF constant (default 60 — standard choice).

This mirrors the "dense + sparse fallback" concept from
``app/search/indexer.py::``search`` where exact FTS5 results fall back to
fuzzy matching when no hits are returned.
"""

from __future__ import annotations

import logging
from typing import Any

from app.rag.retrieval.dense_retriever import DenseRetriever
from app.rag.retrieval.result import RetrievedChunk, SearchResult
from app.rag.retrieval.sparse_retriever import SparseRetriever

logger = logging.getLogger(__name__)

#: RRF constant — controls how much lower-ranked results contribute.
#: Standard value from the original RRF paper (Cormack et al., 2009).
_RRF_K = 60.0


class HybridRetriever:
    """Fuse dense and sparse retrieval results via Reciprocal Rank Fusion.

    Args:
        dense: :class:`DenseRetriever` instance.
        sparse: :class:`SparseRetriever` instance.
        reranker: Optional :class:`Reranker` applied after fusion.
        rrf_k: RRF constant (default 60).
    """

    def __init__(
        self,
        dense: DenseRetriever,
        sparse: SparseRetriever,
        reranker: Any | None = None,
        rrf_k: float = _RRF_K,
    ) -> None:
        self.dense = dense
        self.sparse = sparse
        self.reranker = reranker
        self._rrf_k = rrf_k

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
        filters: dict[str, Any] | None = None,
        identifier_query: str | None = None,
        query_type: str | None = None,
    ) -> SearchResult:
        """Retrieve chunks by fusing dense and sparse results.

        Args:
            query: User query text.
            top_k: Maximum number of results to return.
            dense_weight: Not currently used in RRF (kept for API compatibility).
            sparse_weight: Same — RRF is rank-based, not score-based.
            filters: Optional payload filters passed to both retrievers.
            identifier_query: Optional identifier-route query (e.g. ``"Indian
                Contract Act, 1872 section 73"``) run through the sparse
                retriever as a *parallel additive* arm and RRF-fused with the
                dense + sparse results.  This is the production form of the
                V5.5 identifier route — a lexical query built from the
                detected Act/section in the user's question — which recovered
                gold provisions vector retrieval missed (+13.3pp pool
                ceiling).  Runs without payload ``filters`` (its value is
                lexical text matching, not payload filtering).  Ignored when
                the server-side fusion path is taken (the single-roundtrip
                dense + sparse RRF stays the fast path for plain queries).

        Returns:
            A :class:`SearchResult` with fused, optionally re-ranked chunks.
        """
        import time

        start = time.monotonic()

        # Server-side fusion: when the sparse retriever is backed by a Qdrant
        # store with BM25 sparse vectors AND both sides can embed the query,
        # fuse dense + sparse on the cluster with prefetch + RRF in a single
        # round trip (``QdrantStore.hybrid_search``).  Any failure falls back
        # to the client-side RRF below.  Skipped when an identifier arm is
        # requested — the three-way (dense + sparse + identifier) client-side
        # RRF below fuses everything in one place.
        if identifier_query is None:
            sparse_store = getattr(self.sparse, "store", None)
            dense_embed = getattr(self.dense, "embed_query", None)
            sparse_embed = getattr(self.sparse, "embed_query", None)
            if sparse_store is not None and callable(dense_embed) and callable(sparse_embed):
                try:
                    has_sparse = getattr(sparse_store, "has_sparse_vectors", None)
                    sparse_capable = bool(callable(has_sparse) and has_sparse())
                except Exception as exc:
                    logger.warning("HybridRetriever: sparse capability check failed (%s)", exc)
                    sparse_capable = False
                if sparse_capable:
                    try:
                        dense_vector = dense_embed(query)
                        if getattr(self.sparse, "server_bm25", False):
                            # Qdrant-side BM25: the sparse arm is the raw query
                            # text — the cluster computes the BM25 vector.
                            hybrid = getattr(sparse_store, "hybrid_search_text", None)
                            if not callable(hybrid):
                                raise RuntimeError("store lacks hybrid_search_text (server BM25 requires qdrant-client >= 1.12)")
                            points = hybrid(dense_vector, query, top_k=top_k, filters=filters)
                        else:
                            sparse_vector = sparse_embed(query)
                            points = sparse_store.hybrid_search(dense_vector, sparse_vector, top_k=top_k, filters=filters)
                        from app.rag.retrieval.dense_retriever import DenseRetriever

                        fused = [DenseRetriever._payload_to_chunk(p) for p in points]
                        return SearchResult(
                            query=query,
                            query_type="",
                            chunks=fused,
                            total=len(fused),
                            latency_ms=int((time.monotonic() - start) * 1000),
                            source="hybrid",
                        )
                    except Exception as exc:
                        logger.warning(
                            "HybridRetriever: server-side RRF fusion failed (%s) — using client-side RRF",
                            exc,
                        )

        dense_result = self.dense.search(query, top_k=top_k, filters=filters)
        sparse_result = self.sparse.retrieve(query, top_k=top_k, filters=filters)

        # Identifier route arm (V5.5-validated): a lexical identifier query
        # run through the sparse retriever as a parallel additive arm — the
        # production form of the evaluation lever that recovered gold
        # provisions vector retrieval missed (+13.3pp pool ceiling).
        ident_result = None
        if identifier_query:
            try:
                ident_result = self.sparse.retrieve(identifier_query, top_k=max(top_k * 2, 20), filters=None)
            except Exception as exc:
                logger.warning("HybridRetriever: identifier arm failed (%s)", exc)

        # RRF fusion — rank-based, so scores from different retrievers are
        # comparable regardless of scale.
        chunk_scores: dict[str, float] = {}
        chunk_map: dict[str, RetrievedChunk] = {}

        for rank, chunk in enumerate(dense_result.chunks):
            chunk_id = chunk.chunk_id
            chunk_scores[chunk_id] = chunk_scores.get(chunk_id, 0.0) + 1.0 / (rank + 1 + self._rrf_k)
            if chunk_id not in chunk_map:
                chunk_map[chunk_id] = chunk

        for rank, chunk in enumerate(sparse_result.chunks):
            chunk_id = chunk.chunk_id
            chunk_scores[chunk_id] = chunk_scores.get(chunk_id, 0.0) + 1.0 / (rank + 1 + self._rrf_k)
            if chunk_id not in chunk_map:
                chunk_map[chunk_id] = chunk
            else:
                # Merge: keep the higher score
                existing = chunk_map[chunk_id]
                if chunk.score > existing.score:
                    chunk_map[chunk_id] = chunk

        if ident_result is not None:
            for rank, chunk in enumerate(ident_result.chunks):
                chunk_id = chunk.chunk_id
                chunk_scores[chunk_id] = chunk_scores.get(chunk_id, 0.0) + 1.0 / (rank + 1 + self._rrf_k)
                if chunk_id not in chunk_map:
                    chunk_map[chunk_id] = chunk
                else:
                    # Merge: keep the higher score
                    existing = chunk_map[chunk_id]
                    if chunk.score > existing.score:
                        chunk_map[chunk_id] = chunk

        # Sort by fused RRF score descending
        fused_ids = sorted(chunk_scores, key=chunk_scores.get, reverse=True)  # type: ignore[arg-type]
        fused_chunks = [chunk_map[cid] for cid in fused_ids[:top_k]]

        # Update scores to the RRF score
        for _i, chunk in enumerate(fused_chunks):
            chunk.score = chunk_scores[chunk.chunk_id]

        error = None
        if dense_result.error and not sparse_result.error:
            # Dense failed but sparse succeeded — that's OK
            error = dense_result.error
        elif dense_result.error and sparse_result.error:
            error = f"{dense_result.error}; {sparse_result.error}"

        # Optional reranking
        if self.reranker is not None and fused_chunks:
            try:
                fused_chunks = self.reranker.rerank(query, fused_chunks, top_k=top_k, query_type=query_type)
            except Exception as exc:
                logger.warning("Reranker failed, returning unfused results: %s", exc)

        return SearchResult(
            query=query,
            query_type="",
            chunks=fused_chunks,
            total=len(fused_chunks),
            latency_ms=int((time.monotonic() - start) * 1000),
            source="hybrid",
            error=error,
        )
