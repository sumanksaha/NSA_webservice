"""Sparse (BM25) embedding service for hybrid retrieval (2026-08-09).

Generates sparse text embeddings with ``fastembed``'s ``TextSparseEmbedding``
using the ``Qdrant/bm25`` model — a local, offline-capable BM25 implementation
that needs no Qdrant Cloud hosted-inference plan (the hosted ``qdrant/bm25``
model is not available on every cluster/plan, observed on the provisioned
cluster 2026-08-09).

The produced vectors are JSON-safe dicts ``{"indices": [int, ...],
"values": [float, ...]}`` — the same shape as Qdrant's ``SparseVector`` model,
so they can be passed straight to ``QdrantStore.upsert_points`` /
``search_sparse``.

``fastembed`` is imported **lazily** so the module (and the Flask app) boots
without it; a pre-built embedder can be injected via the constructor for unit
tests (mock-injection pattern from ``app/rag/embedding_service.py``).  When
``fastembed`` is missing the service reports ``is_available() == False`` and
callers (QdrantIndexer / SparseRetriever) degrade to dense-only / rapidfuzz.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Default sparse model — BM25 via fastembed's TextSparseEmbedding.
DEFAULT_SPARSE_MODEL = "Qdrant/bm25"


class SparseEmbeddingService:
    """Generate BM25 sparse vectors for text, batches, and chunks.

    Args:
        model_name: fastembed sparse model name; defaults to the
            ``RAG_SPARSE_MODEL`` config value (``Qdrant/bm25``).
        embedder: Optional pre-built ``TextSparseEmbedding`` (for testing).
    """

    def __init__(self, model_name: str | None = None, embedder: Any | None = None) -> None:
        self._model_name = model_name
        self._embedder = embedder

    @property
    def model_name(self) -> str:
        """Resolve the model name, reading from config lazily."""
        if self._model_name:
            return self._model_name
        try:
            from flask import current_app

            return current_app.config.get("RAG_SPARSE_MODEL", DEFAULT_SPARSE_MODEL)
        except Exception:
            return DEFAULT_SPARSE_MODEL

    # ------------------------------------------------------------------ #
    # Lazy dependency accessor
    # ------------------------------------------------------------------ #

    def _get_embedder(self) -> Any | None:
        """Return a fastembed ``TextSparseEmbedding``, imported lazily.

        Returns ``None`` (with a warning) when fastembed is not installed so
        callers can degrade gracefully; never raises at import time.
        """
        if self._embedder is not None:
            return self._embedder
        try:
            # fastembed >= 0.8 renamed TextSparseEmbedding -> SparseTextEmbedding
            # (observed 2026-08-09 on fastembed 0.8.0); both names share the
            # ``Qdrant/bm25`` model and the same {indices, values} output.
            from fastembed import SparseTextEmbedding as _SparseCls  # type: ignore[import-untyped]
        except ImportError:
            try:
                from fastembed import TextSparseEmbedding as _SparseCls  # type: ignore[import-untyped]
            except ImportError:
                logger.warning(
                    "SparseEmbeddingService: fastembed not installed; sparse (BM25) "
                    "embeddings unavailable. Install it to enable hybrid retrieval."
                )
                return None
        try:
            self._embedder = _SparseCls(model_name=self.model_name)
        except Exception as exc:
            logger.warning("SparseEmbeddingService: failed to load %s: %s", self.model_name, exc)
            return None
        return self._embedder

    def _require_embedder(self) -> Any:
        """Return the embedder or raise a descriptive ``RuntimeError``."""
        embedder = self._get_embedder()
        if embedder is None:
            raise RuntimeError(
                "fastembed is not installed; cannot generate sparse (BM25) vectors. "
                "Install it and set RAG_SPARSE_MODEL (default Qdrant/bm25)."
            )
        return embedder

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """Whether a sparse embedder can be produced right now."""
        return self._get_embedder() is not None

    @staticmethod
    def _to_json_safe(sparse: Any) -> dict[str, list]:
        """Normalise a fastembed sparse object to ``{indices, values}`` lists."""
        indices = getattr(sparse, "indices", None)
        values = getattr(sparse, "values", None)
        if indices is None or values is None:
            raise ValueError("embedder returned an object without indices/values")
        idx = [int(i) for i in indices]
        vals = [float(v) for v in values]
        return {"indices": idx, "values": vals}

    def embed_sparse(self, text: str) -> dict[str, list]:
        """Embed a single text into a BM25 sparse vector.

        Returns:
            ``{"indices": [int, ...], "values": [float, ...]}`` (JSON-safe,
            Qdrant ``SparseVector``-compatible).
        """
        embedder = self._require_embedder()
        result = embedder.embed([text])
        for sparse in result:  # single-item generator
            return self._to_json_safe(sparse)
        return {"indices": [], "values": []}

    def embed_batch(self, texts: list[str]) -> list[dict[str, list]]:
        """Embed a batch of texts into sparse vectors."""
        if not texts:
            return []
        embedder = self._require_embedder()
        return [self._to_json_safe(sparse) for sparse in embedder.embed(list(texts))]

    def embed_chunks(self, chunks: list[Any]) -> list[dict[str, list]]:
        """Embed a list of :class:`app.rag.chunker.Chunk` objects (or strings)."""
        if not chunks:
            return []
        if isinstance(chunks[0], str):
            return self.embed_batch(chunks)  # type: ignore[arg-type]
        return self.embed_batch([c.chunk_text for c in chunks])


# End of sparse_embedding.py
