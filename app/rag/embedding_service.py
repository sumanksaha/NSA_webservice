"""Embedding service for the corpus pipeline (Agent A, Phase 1 — §3.2).

Loads a ``sentence-transformers`` model and generates the dense vectors that
the Qdrant index stores.  ``sentence-transformers`` is imported **lazily** so
the module (and the Flask app) boots without it; a pre-built encoder can be
injected via the constructor for unit tests (mock-injection pattern from
``app/rag/retrieval/dense_retriever.py``).

Vector-size contract: the embedding model's output dimension MUST match the
``RAG_VECTOR_SIZE`` used to create the Qdrant collection (default 768 with
``sentence-transformers/all-mpnet-base-v2``).  ``all-MiniLM-L6-v2`` emits
384-dim vectors and would silently break retrieval against a 768-dim
collection — :meth:`validate_vector_size` guards against that mismatch.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Default model — 768-dim, must match the default ``RAG_VECTOR_SIZE`` and the
#: collection created by :class:`app.rag.qdrant_client.QdrantStore`.
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"


class EmbeddingService:
    """Generate dense embeddings for text, batches, and chunks.

    Args:
        model_name: sentence-transformers model name; defaults to the
            ``RAG_EMBEDDING_MODEL`` config value.
        encoder: Optional pre-built ``SentenceTransformer`` (for testing).
    """

    def __init__(self, model_name: str | None = None, encoder: Any | None = None) -> None:
        self._model_name = model_name
        self._encoder = encoder

    @property
    def model_name(self) -> str:
        """Resolve the model name, reading from config lazily."""
        if self._model_name:
            return self._model_name
        try:
            from flask import current_app

            return current_app.config.get("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        except Exception:  # noqa: BLE001 - outside an app context
            return DEFAULT_EMBEDDING_MODEL

    @property
    def vector_size(self) -> int:
        """Dimensionality of the produced vectors.

        Prefers the loaded encoder's reported dimension (authoritative); falls
        back to the configured ``RAG_VECTOR_SIZE``.
        """
        encoder = self._get_encoder()
        if encoder is not None and hasattr(encoder, "get_sentence_embedding_dimension"):
            try:
                return int(encoder.get_sentence_embedding_dimension())
            except Exception:  # noqa: BLE001 - some test doubles lack the method
                pass
        try:
            from flask import current_app

            return int(current_app.config.get("RAG_VECTOR_SIZE", 768))
        except Exception:  # noqa: BLE001 - outside an app context
            return 768

    # ------------------------------------------------------------------ #
    # Lazy dependency accessor
    # ------------------------------------------------------------------ #

    def _get_encoder(self) -> Any | None:
        """Return a SentenceTransformer, importing sentence-transformers lazily.

        Returns ``None`` (with a warning) when the package is not installed so
        callers can degrade gracefully; never raises at import time.
        """
        if self._encoder is not None:
            return self._encoder
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
        except ImportError:
            logger.warning(
                "EmbeddingService: sentence-transformers not installed; embeddings unavailable. "
                "Install it (with a matching model) to enable dense retrieval."
            )
            return None
        self._encoder = SentenceTransformer(self.model_name)
        return self._encoder

    def _require_encoder(self) -> Any:
        """Return the encoder or raise a descriptive ``RuntimeError``."""
        encoder = self._get_encoder()
        if encoder is None:
            raise RuntimeError(
                "sentence-transformers is not installed; cannot generate embeddings. "
                "Install it and set RAG_EMBEDDING_MODEL to a model matching RAG_VECTOR_SIZE."
            )
        return encoder

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text into a dense vector (list of floats)."""
        encoder = self._require_encoder()
        vector = encoder.encode(text)
        vector = vector.tolist() if hasattr(vector, "tolist") else list(vector)
        # sentence-transformers may return a (1, dim) array for a single
        # string — normalize the single-row case to a flat dim-length vector.
        # (A multi-row (n, dim) result would indicate a broken encoder and is
        # not a supported input here; rows > 1 are left untouched.)
        if len(vector) == 1 and isinstance(vector[0], list):
            vector = vector[0]
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts in one encoder call."""
        encoder = self._require_encoder()
        vectors = encoder.encode(list(texts))
        if hasattr(vectors, "tolist"):
            return vectors.tolist()
        return [list(v) for v in vectors]

    def embed_chunks(self, chunks: list[Any]) -> list[list[float]]:
        """Embed a list of :class:`app.rag.chunker.Chunk` objects (or strings).

        Accepts objects exposing ``chunk_text`` (e.g. :class:`Chunk`) or plain
        strings — the former is the corpus-pipeline path, the latter is handy
        for query-side embedding reuse.
        """
        if not chunks:
            return []
        if isinstance(chunks[0], str):
            return self.embed_batch(chunks)  # type: ignore[arg-type]
        return self.embed_batch([c.chunk_text for c in chunks])

    def validate_vector_size(self, expected: int | None = None) -> bool:
        """Verify the loaded model matches the configured vector size.

        Args:
            expected: Expected dimension; defaults to ``RAG_VECTOR_SIZE``.

        Returns:
            ``True`` when the encoder is unavailable (nothing to validate) or
            the dimensions match; logs a warning and returns ``False`` on
            mismatch (e.g. a 384-dim MiniLM model against a 768-dim index).
        """
        encoder = self._get_encoder()
        if encoder is None:
            return True
        actual = self.vector_size
        expected = int(expected) if expected is not None else int(
            self._config_vector_size()
        )
        # Note: when an injected encoder lacks get_sentence_embedding_dimension,
        # ``vector_size`` falls back to the configured size, so this comparison
        # passes silently — that is intentional for minimal test doubles.
        if actual != expected:
            logger.warning(
                "EmbeddingService: model %s emits %d-dim vectors but the collection expects %d — "
                "retrieval would break. Use a matching model.",
                self.model_name,
                actual,
                expected,
            )
            return False
        return True

    @staticmethod
    def _config_vector_size() -> int:
        """Read ``RAG_VECTOR_SIZE`` from config (768 outside an app context)."""
        try:
            from flask import current_app

            return int(current_app.config.get("RAG_VECTOR_SIZE", 768))
        except Exception:  # noqa: BLE001
            return 768


# End of embedding_service.py
