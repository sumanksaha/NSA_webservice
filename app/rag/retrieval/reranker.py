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

            # Bound torch threads before the model is built (RAG_TORCH_THREADS)
            # so a rerank call does not peg every core on a laptop.
            from app.rag.torch_runtime import cap_torch_threads

            cap_torch_threads()
            self._encoder = CrossEncoder(self.model_name)
            return self._encoder
        except ImportError:
            return None

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
        query_type: str | None = None,
    ) -> list[RetrievedChunk]:
        """Re-rank ``chunks`` by relevance to ``query``.

        Uses the cross-encoder if available; otherwise falls back to
        deterministic text-overlap scoring.  *query_type* is accepted for
        API symmetry with :class:`EnsembleReranker` but is not used by the
        plain reranker.
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
            scored = list(zip(scores, chunks, strict=False))
            scored.sort(key=lambda x: float(x[0]), reverse=True)
            for score, chunk in scored:
                chunk.score = float(score)
            result = [chunk for _, chunk in scored]
        except Exception as exc:
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


class EnsembleReranker:
    """sec_act + cross-encoder ensemble reranker (CE_RERANK_REVIEW, 2026-08-14).

    Production form of the V5.5/CE evaluation finding: the deterministic
    ``sec_act`` legal features are the strongest single reranker measured
    (R@10 0.474 vs 0.362 for the fine-tuned cross-encoder on the same P1
    head), but the two are complementary — the cross-encoder alone recovers
    8 questions the features miss, and the union (``sec_act ∨ CE``) reaches
    any-hit R@10 = 62.0% vs 56.7% for sec_act alone.

    Algorithm (bounded cost):

    1. **sec_act primary** — rank all chunks by
       ``base_score + 2.0 * sec_match + 1.5 * act_match + 1.0 * exact`` (the
       measured weight grid, with exact = sec AND act match).  ``sec_match``
       = query-detected section number == chunk section; ``act_match`` =
       query-detected Act is contained in the chunk's ``act_name``/
       ``document_title``.  Both are pure lexical detections
       (``app/rag/retrieval/identifier``) — no gold labels.
    2. **CE second opinion on the head only** — score only the top
       ``ce_head`` (default 20) of the sec_act ranking with the cross-encoder,
       min-max normalize the scores, and add ``ce_weight * norm`` to the
       primary score.  Chunks outside the head keep their feature score.
       **Dynamic skipping**: when *both* section and Act are detected in the
       query AND the entire head has exact matches, the CE is skipped
       (features already decided the ranking; ~5-9s CE cost saved).
    3. **Graceful degradation** — no encoder / predict failure → pure sec_act
       ranking; no identifiers in the query → features are all zero, so the
       order is base-score + CE bonus (the CE's strength: text-level
       relevance when legal features are absent).

    Latency: the CE is invoked on at most ``ce_head`` pairs per query,
    independent of the candidate pool size.  Dynamic skipping can reduce
    this to zero on high-confidence (exact-match) queries.
    """

    #: Measured weight grid from rerank_legal.py (sec_act).
    _W_SEC = 2.0
    _W_ACT = 1.5
    _W_EXACT = 1.0  # sec AND act — both match, stronger signal
    #: Small boost for chunks at section/subsection/clause granularity
    #: (hierarchy_level 3-5).  Level 1 = document root, level 2 = chapter;
    #: these are header chunks that rarely carry the specific provision text.
    #: Measured: gold chunks concentrate at levels 3-5 (section/sub/clause).
    _W_HIERARCHY = 0.2

    def __init__(
        self,
        model_name: str | None = None,
        encoder: Any | None = None,
        ce_head: int = 20,
        ce_weight: float = 0.5,
    ) -> None:
        self.model_name = model_name or "cross-encoder/ms-marco-MiniLM-L-6-v2"
        self._encoder = encoder
        self.ce_head = max(ce_head, 1)
        self.ce_weight = ce_weight
        # Dynamic CE skipping: when the sec_act head is already decisive
        # (all top-K chunks have exact match), skip the CE to save latency.
        # ponytail: skip CE when confidence is already maximal — ceiling is
        # exact-match queries where CE adds nothing; the ~5-9s CE cost is
        # wasted.
        self.skip_ce_when_confident = True

    def _get_encoder(self) -> Any | None:
        """Return a ``CrossEncoder``, importing lazily (None when unavailable)."""
        if self._encoder is not None:
            return self._encoder
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]

            # Bound torch threads before the model is built (RAG_TORCH_THREADS)
            # so the CE head does not peg every core on a laptop.
            from app.rag.torch_runtime import cap_torch_threads

            cap_torch_threads()
            self._encoder = CrossEncoder(self.model_name)
            return self._encoder
        except Exception as exc:
            logger.warning("EnsembleReranker: cross-encoder unavailable (%s)", exc)
            return None

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
        query_type: str | None = None,
    ) -> list[RetrievedChunk]:
        """Re-rank ``chunks`` with sec_act features primary + CE head bonus.

        When *query_type* is provided (from the legal query-type classifier),
        applies the matching :class:`~app.rag.retrieval.legal_query_classifier.QueryTypeConfig`
        weight overrides instead of the default measured grid.  This is the
        CE_RERANK_REVIEW query-type-aware reranking layer (STEP 7): e.g.
        prohibition queries regress with hierarchy boosting (hierarchy=0),
        authority queries need a larger CE head, cross-reference queries
        rely on identifier recovery.
        """
        if not chunks:
            return []

        from app.rag.retrieval.legal_query_classifier import get_config

        w_sec = self._W_SEC
        w_act = self._W_ACT
        w_exact = self._W_EXACT
        w_hierarchy = self._W_HIERARCHY
        ce_head = self.ce_head
        ce_weight = self.ce_weight
        skip_ce = False

        if query_type is not None:
            try:
                cfg = get_config(query_type)
                fw = cfg.feature_weight
                w_sec = self._W_SEC * fw
                w_act = self._W_ACT * fw
                w_exact = self._W_EXACT * fw
                w_hierarchy = cfg.hierarchy_weight
                ce_head = cfg.ce_head
                ce_weight = cfg.ce_weight
                skip_ce = cfg.skip_ce
            except Exception:
                pass

        from app.rag.retrieval.identifier import detect_act, detect_section

        q_sec, _sub = detect_section(query)
        q_act = detect_act(query)

        # 1. sec_act + hierarchy features per chunk (exact = sec AND act match)
        primary = []
        exact_flags = []
        for chunk in chunks:
            sec = self._section_match(q_sec, chunk.section_number)
            act = self._act_match(q_act, chunk)
            exact = 1.0 if (sec and act) else 0.0
            exact_flags.append(bool(exact))
            hier = self._hierarchy_boost(chunk.hierarchy_level)
            primary.append(chunk.score + w_sec * sec + w_act * act + w_exact * exact + w_hierarchy * hier)

        # 2. CE scores for the head only
        head_idx = sorted(range(len(chunks)), key=lambda i: primary[i], reverse=True)[:ce_head]
        ce_bonus: dict[int, float] = {}
        encoder = self._get_encoder()
        # ponytail: dynamic CE skipping — when the entire sec_act head has
        # exact matches, the features already decided the ranking; skip the
        # ~5-9s CE cost.  Only triggers when BOTH section + Act detected.
        if not skip_ce:
            skip_ce = (
                self.skip_ce_when_confident
                and q_sec is not None
                and q_act is not None
                and all(exact_flags[i] for i in head_idx)
            )
        if encoder is not None and not skip_ce:
            try:
                pairs = [(query, chunks[i].text) for i in head_idx]
                scores = encoder.predict(pairs)
                norm = self._minmax([float(s) for s in scores])
                ce_bonus = {i: ce_weight * n for i, n in zip(head_idx, norm, strict=False)}
            except Exception as exc:
                logger.warning("EnsembleReranker: CE scoring failed (%s) — sec_act features only", exc)

        # 3. final = primary + CE bonus, stable re-sort
        final = [primary[i] + ce_bonus.get(i, 0.0) for i in range(len(chunks))]
        order = sorted(range(len(chunks)), key=lambda i: final[i], reverse=True)
        result = [chunks[i] for i in order]
        for i, chunk in enumerate(result):
            chunk.score = final[order[i]]
        if top_k is not None:
            result = result[:top_k]
        return result

    @staticmethod
    def _section_match(q_sec: str | None, chunk_sec: str | None) -> float:
        """1.0 when the query section matches the chunk's leading section number."""
        if not q_sec:
            return 0.0
        m = re.match(r"\s*(\d{1,4})", str(chunk_sec or ""))
        return 1.0 if m and m.group(1) == q_sec else 0.0

    @staticmethod
    def _act_match(q_act: str | None, chunk: RetrievedChunk) -> float:
        """1.0 when the query-detected Act is contained in the chunk's identity.

        Mirrors the V5 resolution semantics (``act_name OR document_title``
        unioned): sub-instruments stamp the parent Act in ``act_name`` but the
        instrument title only in ``document_title``, so both are consulted.
        """
        if not q_act:
            return 0.0
        hay = f"{chunk.act_name or ''} {chunk.document_title or ''}".lower()
        return 1.0 if q_act.lower() in hay else 0.0

    @staticmethod
    def _hierarchy_boost(level: int) -> float:
        """1.0 for section/subsection/clause chunks (level 3-5), 0 otherwise.

        Level 1 = document root, level 2 = chapter — these are structural
        headers that rarely carry specific provision text.  Gold units
        concentrate at levels 3-5 where the actual section/subsection text
        lives.
        """
        return 1.0 if 3 <= level <= 5 else 0.0

    @staticmethod
    def _minmax(scores: list[float]) -> list[float]:
        """Min-max normalize to [0, 1]; all-equal input maps to zeros."""
        if not scores:
            return []
        lo, hi = min(scores), max(scores)
        span = hi - lo
        if span <= 0:
            return [0.0] * len(scores)
        return [(s - lo) / span for s in scores]
