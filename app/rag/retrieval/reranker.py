"""Reranker — cross-encoder reranking of retrieval results.

Uses a ``sentence-transformers`` cross-encoder (``cross-encoder/ms-marco-``
``MiniLM-L-6-v2``) when available.  Falls back to a deterministic text-overlap
scoring strategy (BM25-style + rapidfuzz) so the reranker always functions even
without the optional ``sentence-transformers`` / ``torch`` dependencies —
consistent with the graceful-degradation pattern in ``app/food_cell/services.py``.

The fallback scoring mirrors the method-based confidence approach from
``app/metadata_extractor/confidence.py``: keyword overlap carries higher weight
than fuzzy similarity, combining both into a composite score.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from rapidfuzz import fuzz

from app.rag.retrieval.result import RetrievedChunk

logger = logging.getLogger(__name__)

# Method-based base weighting — mirrors _METHOD_BASE from confidence.py
_REGEX_BOOST = 0.85  # exact keyword overlap
_FUZZY_BOOST = 0.70  # fuzzy partial ratio


class Reranker:
    """Re-rank a list of retrieved chunks by relevance to the query.

    Args:
        model_name: Cross-encoder model name.
        encoder: Optional pre-built cross-encoder (for testing).
    """

    def __init__(self, model_name: str | None = None, encoder: Any | None = None) -> None:
        self.model_name = model_name or "cross-encoder/ms-marco-MiniLM-L-6-v2"
        self._encoder = encoder

    def _get_encoder(self) -> Any | None:
        """Return a ``CrossEncoder``, importing lazily."""
        if self._encoder is not None:
            return self._encoder
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]

            self._encoder = CrossEncoder(self.model_name)
            return self._encoder
        except ImportError:
            return None

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Re-rank ``chunks`` by relevance to ``query``.

        Uses the cross-encoder if available; otherwise falls back to
        deterministic text-overlap scoring.
        """
        if not chunks:
            return []

        encoder = self._get_encoder()
        if encoder is not None:
            return self._rerank_cross_encoder(query, chunks, encoder, top_k)

        return self._rerank_fallback(query, chunks, top_k)

    # ------------------------------------------------------------------ #
    # Re-ranking strategies
    # ------------------------------------------------------------------ #

    def _rerank_cross_encoder(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        encoder: Any,
        top_k: int | None,
    ) -> list[RetrievedChunk]:
        """Re-rank using cross-encoder pairwise scores."""
        pairs = [(query, chunk.text) for chunk in chunks]
        try:
            scores = encoder.predict(pairs)
            scored = list(zip(scores, chunks))
            scored.sort(key=lambda x: float(x[0]), reverse=True)
            for score, chunk in scored:
                chunk.score = float(score)
            result = [chunk for _, chunk in scored]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cross-encoder reranking failed, falling back: %s", exc)
            return self._rerank_fallback(query, chunks, top_k)

        if top_k is not None:
            result = result[:top_k]
        return result

    def _rerank_fallback(
        self,
        query: str,
                chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Deterministic re-ranking using keyword overlap + fuzzy matching.

        Uses a simplified BM25-style term-frequency score combined with
        rapidfuzz ``partial_ratio``, blended with the original retrieval
        score.  Mirrors the method-based weighting from
        ``app/metadata_extractor/confidence.py``.
        """
        query_terms = self._tokenize(query)
        doc_freqs: dict[str, int] = {}
        total_docs = len(chunks)

        for chunk in chunks:
            terms = set(self._tokenize(chunk.text))
            for t in terms:
                doc_freqs[t] = doc_freqs.get(t, 0) + 1

        scored: list[tuple[float, RetrievedChunk]] = []
        for chunk in chunks:
            chunk_terms = self._tokenize(chunk.text)
            term_freqs: dict[str, int] = {}
            for t in chunk_terms:
                term_freqs[t] = term_freqs.get(t, 0) + 1

            bm25 = 0.0
            for term in query_terms:
                tf = term_freqs.get(term, 0)
                df = doc_freqs.get(term, 0)
                if tf == 0 or df == 0:
                    continue
                idf = math.log((total_docs + 1) / df)
                bm25 += tf * idf

            fuzzy = max(
                fuzz.token_set_ratio(query, chunk.text) / 100.0,
                fuzz.partial_ratio(query, chunk.text) / 100.0,
            )

            combined = (_REGEX_BOOST if bm25 > 0 else 0.0) * min(bm25 / 10.0, 1.0) + _FUZZY_BOOST * fuzzy
            combined = 0.5 * chunk.score + 0.5 * combined
            chunk.score = combined
            scored.append((combined, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        result = [chunk for _, chunk in scored]
        if top_k is not None:
            result = result[:top_k]
        return result

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize text into lowercase alphanumeric tokens."""
        return re.findall(r"\b[a-z0-9]+\b", text.lower())