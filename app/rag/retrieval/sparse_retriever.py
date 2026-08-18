"""Sparse (lexical) retriever — BM25 via Qdrant sparse vectors, rapidfuzz fallback.

Primary path (2026-08-09): when a :class:`QdrantStore` and a
:class:`SparseEmbeddingService` are provided AND the collection declares
named sparse vectors (``text_sparse``), the query is embedded into a BM25
sparse vector with fastembed (``Qdrant/bm25``) and searched server-side via
``QdrantStore.search_sparse`` — a real, scalable BM25 that needs no Qdrant
hosted-inference plan.

Fallback path (unchanged): fuzzy keyword matching with rapidfuzz against an
in-memory corpus dict (``chunk_id -> chunk_dict`` with ``text`` and payload
metadata fields), adapting the ``_field_score`` pattern from
``app/search/indexer.py`` (``FTS5Indexer.fuzzy_search``).  Used when no
store/embedder is injected, the collection is dense-only, or the sparse
path fails.

Per ``RAG_AGENT_B_SCOPE.md`` warning #8: the storage backend must be Qdrant
— no SQLite FTS5 as the primary retriever.
"""

from __future__ import annotations

import logging
from typing import Any

from rapidfuzz import fuzz

from app.rag.retrieval.result import RetrievedChunk, SearchResult

logger = logging.getLogger(__name__)


class SparseRetriever:
    """Sparse lexical retriever — BM25 Qdrant vectors with rapidfuzz fallback.

    Args:
        corpus: ``{chunk_id: chunk_dict}`` (rapidfuzz fallback corpus), each
            dict with at least ``text`` (str) and optionally
            ``section_number``, ``document_title``, ``document_type``,
            ``authority``, etc.
        store: Optional :class:`QdrantStore` for the BM25 sparse-vector path.
        embedder: Optional :class:`SparseEmbeddingService` for query-side BM25
            embedding (built lazily when ``None``).
        server_bm25: When True (and the store is sparse-capable), the query
            is sent as text and **Qdrant computes the BM25 vector in-cluster**
            (``Qdrant/bm25`` — no local fastembed at query time).  Gated by
            ``RAG_QDRANT_BM25``; requires qdrant-client >= 1.12 and a cluster
            with BM25-in-cluster support (verified on the provisioned one).
    """

    def __init__(
        self,
        corpus: dict[str, dict[str, Any]] | None = None,
        store: Any | None = None,
        embedder: Any | None = None,
        server_bm25: bool = False,
    ) -> None:
        self._corpus = corpus or {}
        self._store = store
        self._embedder = embedder
        self.server_bm25 = server_bm25

    @property
    def store(self) -> Any | None:
        """The Qdrant store backing this retriever (None when not configured).

        Note the store is also non-None when the collection is dense-only —
        the actual fallback condition is ``store.has_sparse_vectors()``.
        """
        return self._store

    def embed_query(self, text: str) -> dict[str, list]:
        """Embed a query into a BM25 sparse vector ``{indices, values}``.

        Uses the injected embedder or builds the default
        :class:`SparseEmbeddingService`.  Raises ``RuntimeError`` when
        fastembed is unavailable.
        """
        if self._embedder is None:
            from app.rag.sparse_embedding import SparseEmbeddingService

            self._embedder = SparseEmbeddingService()
        return self._embedder.embed_sparse(text)

    @staticmethod
    def _field_score(query: str, text: str) -> float:
        """Best fuzzy similarity of ``query`` against ``text`` (0–100).

        Mirrors ``app/search/indexer.py::``_field_score``: combines
        ``token_set_ratio`` (multi-word queries) with ``partial_ratio``
        (substring tolerance) and returns the maximum.
        """
        if not text:
            return 0.0
        return max(
            fuzz.token_set_ratio(query, text),
            fuzz.partial_ratio(query, text),
        )

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        threshold: float = 65.0,
        filters: dict[str, Any] | None = None,
    ) -> SearchResult:
        """Retrieve top-k chunks via fuzzy lexical matching.

        Args:
            query: User query text.
            top_k: Maximum number of results.
            threshold: Minimum fuzzy score (0–100) to include.
            filters: Optional ``{field: value}`` dict to pre-filter the corpus
                before scoring (e.g. ``{"document_type": "Act"}``).

        Returns:
            A :class:`SearchResult` with sparse-retrieved chunks.
        """
        import time

        start = time.monotonic()
        if not query or not query.strip():
            return SearchResult(
                query=query,
                query_type="",
                chunks=[],
                total=0,
                latency_ms=int((time.monotonic() - start) * 1000),
                source="sparse",
            )

        # Primary path: BM25 sparse vectors in Qdrant (real lexical search).
        # Capability check + search are both guarded so any failure (missing
        # method on a double, transient error) degrades to rapidfuzz instead
        # of escaping retrieve().
        if self._store is not None:
            try:
                has_sparse = getattr(self._store, "has_sparse_vectors", None)
                sparse_capable = bool(callable(has_sparse) and has_sparse())
            except Exception as exc:
                logger.warning("SparseRetriever: sparse capability check failed (%s)", exc)
                sparse_capable = False
            if sparse_capable:
                try:
                    if self.server_bm25:
                        # Qdrant-side BM25: the cluster tokenizes + weights the
                        # query text (no local fastembed).  Verified live
                        # 2026-08-16 — free on the free tier.
                        search = getattr(self._store, "search_sparse_text", None)
                        if not callable(search):
                            raise RuntimeError(
                                "store lacks search_sparse_text (RAG_QDRANT_BM25 requires qdrant-client >= 1.12)"
                            )
                        points = search(query, top_k=top_k, filters=filters)
                    else:
                        sparse_vector = self.embed_query(query)
                        # BM25 scores are unbounded similarity (not 0-1 like
                        # dense cosine); hybrid fusion consumes them rank-based
                        # (RRF).
                        points = self._store.search_sparse(sparse_vector, top_k=top_k, filters=filters)
                    from app.rag.retrieval.dense_retriever import DenseRetriever

                    chunks = [DenseRetriever._payload_to_chunk(p) for p in points]
                    return SearchResult(
                        query=query,
                        query_type="",
                        chunks=chunks,
                        total=len(chunks),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        source="sparse",
                    )
                except Exception as exc:
                    logger.warning(
                        "SparseRetriever: BM25 Qdrant path failed (%s) — using rapidfuzz fallback",
                        exc,
                    )

        candidates = list(self._corpus.values())

        # Apply pre-filters
        if filters:
            candidates = [c for c in candidates if all(c.get(k) == v for k, v in filters.items())]

        # Score each candidate — combine title + text scoring
        scored: list[tuple[float, dict[str, Any]]] = []
        for chunk in candidates:
            text = chunk.get("text", "")
            title = chunk.get("document_title", "")
            text_score = self._field_score(query, text)
            title_score = self._field_score(query, title) if title else 0.0
            # Weight text higher than title (text carries the substantive content)
            combined = max(text_score, title_score * 0.7)
            if combined >= threshold:
                scored.append((combined, chunk))

        # Sort by score descending, take top_k
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        chunks = [
            RetrievedChunk(
                chunk_id=str(chunk.get("chunk_id", chunk.get("id", ""))),
                score=score / 100.0,  # normalise to 0–1
                text=chunk.get("text", ""),
                section_number=chunk.get("section_number"),
                clause_number=chunk.get("clause_number"),
                document_title=chunk.get("document_title", ""),
                act_name=chunk.get("act_name", ""),
                document_type=chunk.get("document_type", ""),
                authority=chunk.get("authority", ""),
                chunk_index=chunk.get("chunk_index", 0),
                hierarchy_level=chunk.get("hierarchy_level", 0),
                parent_chunk_id=chunk.get("parent_chunk_id"),
            )
            for score, chunk in top
        ]

        return SearchResult(
            query=query,
            query_type="",
            chunks=chunks,
            total=len(chunks),
            latency_ms=int((time.monotonic() - start) * 1000),
            source="sparse",
        )
