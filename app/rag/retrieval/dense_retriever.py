"""Dense vector retriever — queries a Qdrant collection using embeddings.

Uses ``sentence-transformers`` for query embedding and ``qdrant-client`` for
search.  Both are imported **lazily** so the module boots even when the
packages are absent (consistent with the graceful-degradation philosophy in
``app/food_cell/services.py``).  A test double or pre-built client can be
injected via the constructor for unit testing without the optional deps.

Configuration reads Qdrant URL / collection / vector-size / embedding-model
from ``current_app.config`` at call time (lazy, per-request) — the same
pattern used by ``app/ai_assistant/service.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.rag.retrieval.result import RetrievedChunk, SearchResult

logger = logging.getLogger(__name__)


class DenseRetriever:
    """Retrieve top-k chunks from Qdrant via dense vector similarity.

    Args:
        collection_name: Qdrant collection (must match Agent A's index).
        vector_size: Dimensionality of the embedding vectors (768 per scope §5.2).
        embedding_model: Name of the sentence-transformers model to use.
        client: Optional pre-built ``QdrantClient`` (for testing).
        encoder: Optional pre-built ``SentenceTransformer`` (for testing).
    """

    def __init__(
        self,
        collection_name: str,
        vector_size: int = 768,
        embedding_model: str | None = None,
        client: Any | None = None,
        encoder: Any | None = None,
    ) -> None:
        # Store raw params; resolve from current_app lazily in _get_encoder
        # so the constructor works outside an app context (e.g. in unit tests).
        self.collection_name = collection_name or "fssai_legal_768"
        self.vector_size = vector_size
        self._embedding_model = embedding_model
        # Injection points for testing (bypass lazy imports)
        self._client = client
        self._encoder = encoder
        #: Cache for :meth:`_collection_has_sparse` (one get_collection call).
        self._has_sparse: bool | None = None

    @property
    def embedding_model(self) -> str:
        """Resolve the embedding model, reading from config lazily."""
        if self._embedding_model is not None:
            return self._embedding_model
        from flask import current_app
        return current_app.config.get(
            "RAG_EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"
        )

    # ------------------------------------------------------------------ #
    # Lazy dependency accessors
    # ------------------------------------------------------------------ #

    def _get_client(self) -> Any | None:
        """Return a QdrantClient, importing qdrant-client lazily."""
        if self._client is not None:
            return self._client
        from qdrant_client import QdrantClient  # type: ignore[import-untyped]

        from flask import current_app
        url = current_app.config.get("RAG_QDRANT_URL", "")
        if not url:
            logger.warning("DenseRetriever: RAG_QDRANT_URL not configured; dense retrieval unavailable.")
            return None
        api_key = current_app.config.get("RAG_QDRANT_API_KEY") or None
        self._client = QdrantClient(url=url, api_key=api_key)
        return self._client

    def _get_encoder(self) -> Any | None:
        """Return a SentenceTransformer, importing sentence-transformers lazily."""
        if self._encoder is not None:
            return self._encoder
        from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

        self._encoder = SentenceTransformer(self.embedding_model)
        return self._encoder

    def _collection_has_sparse(self) -> bool:
        """Whether the collection declares named sparse vectors (BM25 hybrid).

        Hybrid collections require ``using="dense"`` on every dense query;
        detected once per retriever instance and cached.  Returns ``False``
        for dense-only / unavailable collections so existing behaviour is
        unchanged.
        """
        if self._has_sparse is not None:
            return self._has_sparse
        client = self._get_client()
        if client is None:
            return False
        try:
            info = client.get_collection(self.collection_name)
            params = getattr(getattr(info, "config", None), "params", None)
            sparse = getattr(params, "sparse_vectors", None) if params is not None else None
            self._has_sparse = bool(sparse)
            return self._has_sparse
        except Exception as exc:  # noqa: BLE001 - mock/unconfigured clients
            # Not cached: a transient failure must not permanently mask a
            # sparse-capable collection for the lifetime of the retriever.
            logger.warning("DenseRetriever._collection_has_sparse failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def embed_query(self, text: str) -> list[float]:
        """Embed a query string into a dense vector.

        Raises ``RuntimeError`` when sentence-transformers is unavailable.
        """
        encoder = self._get_encoder()
        if encoder is None:
            raise RuntimeError("sentence-transformers is not installed; cannot embed query.")
        vec = encoder.encode(text)
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)

    @staticmethod
    def _payload_to_chunk(point: Any) -> RetrievedChunk:
        """Convert a Qdrant ``ScoredPoint`` (or ``{"id", "score", "payload"}``
        dict) to a :class:`RetrievedChunk`."""
        point_id = getattr(point, "id", None)
        if point_id is None and isinstance(point, dict):
            point_id = point.get("id")
        score = getattr(point, "score", None)
        if score is None and isinstance(point, dict):
            score = point.get("score")
        payload = getattr(point, "payload", None)
        if payload is None and isinstance(point, dict):
            payload = point.get("payload")
        payload = payload or {}
        return RetrievedChunk(
            chunk_id=str(point_id),
            score=float(score or 0.0),
            text=payload.get("chunk_text", payload.get("text", "")),
            section_number=payload.get("section_number"),
            document_title=payload.get("document_title", ""),
            document_type=payload.get("document_type", ""),
            authority=payload.get("authority", ""),
            chunk_index=payload.get("chunk_index", 0),
            hierarchy_level=payload.get("hierarchy_level", 0),
            parent_chunk_id=payload.get("parent_chunk_id"),
        )

    def search(
        self,
        query: str,
        top_k: int = 10,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> SearchResult:
        """Retrieve top-k chunks from Qdrant for ``query``.

        Args:
            query: User query text.
            top_k: Maximum number of results.
            score_threshold: Minimum similarity score (Qdrant ``score_threshold``).
            filters: Optional payload filter dict, e.g. ``{"section_number": "55"}``.

        Returns:
            A :class:`SearchResult` with dense-retrieved chunks.
        """
        import time

        start = time.monotonic()
        try:
            encoder = self._get_encoder()
            if encoder is None:
                return SearchResult(
                    query=query, query_type="", chunks=[], total=0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    source="dense", error="sentence-transformers not installed",
                )

            vector = self.embed_query(query)
            client = self._get_client()
            if client is None:
                return SearchResult(
                    query=query, query_type="", chunks=[], total=0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    source="dense", error="Qdrant not configured (RAG_QDRANT_URL missing)",
                )

            filter_dict = self._build_filter(filters) if filters else None
            from app.rag.qdrant_client import DENSE_VECTOR_NAME, dense_search

            points = dense_search(
                client,
                collection_name=self.collection_name,
                vector=vector,
                limit=top_k,
                score_threshold=score_threshold,
                filter_dict=filter_dict,
                using=DENSE_VECTOR_NAME if self._collection_has_sparse() else None,
            )

            chunks = [self._payload_to_chunk(p) for p in (points or [])]
            return SearchResult(
                query=query, query_type="", chunks=chunks, total=len(chunks),
                latency_ms=int((time.monotonic() - start) * 1000),
                source="dense",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("DenseRetriever.search failed: %s", exc)
            return SearchResult(
                query=query, query_type="", chunks=[], total=0,
                latency_ms=int((time.monotonic() - start) * 1000),
                source="dense", error=str(exc),
            )

    @staticmethod
    def _build_filter(filters: dict[str, Any]) -> dict[str, Any]:
        """Convert a flat ``{field: value}`` dict into a Qdrant filter dict."""
        must = []
        for key, value in filters.items():
            if isinstance(value, list):
                for v in value:
                    must.append({"key": key, "match": {"value": v}})
            else:
                must.append({"key": key, "match": {"value": value}})
        return {"must": must} if must else {}