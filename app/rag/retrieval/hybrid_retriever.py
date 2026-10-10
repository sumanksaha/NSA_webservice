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
from app.rag.retrieval.rrf import DEFAULT_RRF_K, reciprocal_rank_fuse
from app.rag.retrieval.section_scope import merge_caller_filters, section_scope_meta
from app.rag.retrieval.sparse_retriever import SparseRetriever

logger = logging.getLogger(__name__)


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
        rrf_k: float = DEFAULT_RRF_K,
    ) -> None:
        self.dense = dense
        self.sparse = sparse
        self.reranker = reranker
        self._rrf_k = rrf_k

    def _section_arm_enabled(self) -> bool:
        """Whether the section/act-scoped structural arm runs.

        Read per call rather than cached in ``__init__`` so tests can flip the
        flag after construction, matching how the other feature flags are
        consumed.
        """
        from app.shared.config import cfg

        return bool(cfg.section_arm)

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
        filters: dict[str, Any] | None = None,
        identifier_query: str | None = None,
        form_query: str | None = None,
        query_type: str | None = None,
        rrf_k: float | None = None,
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
            form_query: Optional form-reference query (e.g. ``"Form VIII"``)
                run through the sparse retriever as a *parallel additive* arm
                and RRF-fused.  Used for workflow/procedure queries that
                reference specific FSSAI forms.

        ``rrf_k`` overrides the retriever's default RRF constant (60) for this
        single query — used by the RL arm to vary the fusion breadth per query.
        ``None`` uses ``self._rrf_k``.

        When ``cfg.section_arm`` is on, a section/act-scoped structural arm
        also runs (see :mod:`app.rag.retrieval.section_scope`). It is derived
        from the query itself, so it needs no new argument, and it is purely
        additive in the RRF fusion.

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
                except (AttributeError, TypeError) as exc:
                    logger.warning("HybridRetriever: sparse capability check failed (%s) (%s)", exc, type(exc).__name__)
                    sparse_capable = False
                if sparse_capable:
                    try:
                        dense_vector = dense_embed(query)
                        if getattr(self.sparse, "server_bm25", False):
                            # Qdrant-side BM25: the sparse arm is the raw query
                            # text — the cluster computes the BM25 vector.
                            hybrid = getattr(sparse_store, "hybrid_search_text", None)
                            if not callable(hybrid):
                                raise RuntimeError(
                                    "store lacks hybrid_search_text (server BM25 requires qdrant-client >= 1.12)",
                                )
                            points = hybrid(dense_vector, query, top_k=top_k, filters=filters)
                        else:
                            sparse_vector = sparse_embed(query)
                            points = sparse_store.hybrid_search(
                                dense_vector,
                                sparse_vector,
                                top_k=top_k,
                                filters=filters,
                            )
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
                    except (ConnectionError, RuntimeError) as exc:
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
            except (ConnectionError, RuntimeError) as exc:
                logger.warning("HybridRetriever: identifier arm failed (%s)", exc)

        # Form reference arm: for procedure queries that explicitly name a
        # form (e.g., "Form VIII" for appeals).
        #
        # Sized to the *same* depth as the dense/sparse lists, not deeper.
        # RRF is rank-based, so a candidate from a short list always enters
        # near the top of its own list and therefore outranks every item
        # below that depth in the other lists — no matter how irrelevant it
        # is. The identifier arm above asks for max(top_k*2, 20); doing the
        # same here would hand the form arm a structural advantage over the
        # vector pool for every procedure query that names a form.
        # This is the same geometry that made KG fusion cost 20.6pp of
        # citation recall (analysis §1), where each KG item entered at rank
        # 1-5 of its own list and outranked gold by construction.
        form_result = None
        if form_query:
            try:
                form_result = self.sparse.retrieve(form_query, top_k=top_k, filters=None)
            except (ConnectionError, RuntimeError) as exc:
                logger.warning("HybridRetriever: form arm failed (%s)", exc)

        # Section/act-scoped structural arm (Phase 0, RAG_SECTION_ARM).
        # Retrieves the structurally-correct chunks by exact section/Act
        # payload identity and RRF-fuses them with the lexical arms. This is
        # the production form of the deterministic section/act signal that
        # outranks both cross-encoders on R@10 (ce_rerank_eval.json). See
        # app/rag/retrieval/section_scope.py for the rationale and the
        # trigger classes.
        scope_result = None
        if self._section_arm_enabled():
            scope_meta = section_scope_meta(query)
            if scope_meta is None:
                logger.debug("HybridRetriever: no section scope detected in query; structural arm skipped")
            else:
                try:
                    # Section-scoped arm is precision-oriented: the filter
                    # already pins the instrument, so top_k is enough. The
                    # act-only arm benefits from a wider fetch (the Act's
                    # sections compete for the governing provision), so it
                    # gets the same 2x width the identifier/form arms use.
                    width = top_k if scope_meta["mode"] == "act_section" else max(top_k * 2, 20)
                    # The caller's own payload filters (QueryParser output and
                    # anything the HTTP body supplied) still apply: the arm
                    # must not be a way to retrieve outside the caller's scope.
                    scope_filter = merge_caller_filters(scope_meta["filter"], filters)
                    armed = self.dense.search(query, top_k=width, filters=scope_filter)
                    # ``dense.search`` swallows its own exceptions and returns
                    # an empty result carrying ``error``, so the ``except``
                    # below rarely fires. An arm that found nothing is not a
                    # reason to widen the pool — gate on the chunks, not on
                    # the object, so a missing payload index (or any other
                    # silent Qdrant failure) leaves the pool exactly as the
                    # base arms built it.
                    if armed.chunks:
                        scope_result = armed
                    else:
                        logger.debug(
                            "HybridRetriever: section/act-scope arm returned no chunks (%s)",
                            armed.error or "no matches",
                        )
                except (ConnectionError, RuntimeError) as exc:
                    logger.warning("HybridRetriever: section/act-scope arm failed (%s)", exc)

        # RRF fusion — rank-based, so scores from different retrievers are
        # comparable regardless of scale.  The core scoring is delegated to
        # ``reciprocal_rank_fuse`` (app.rag.retrieval.rrf) to eliminate the
        # duplicated formula across the dense, sparse, and identifier arms.
        ranked_lists = [dense_result.chunks, sparse_result.chunks]
        if ident_result:
            ranked_lists.append(ident_result.chunks)
        if form_result:
            ranked_lists.append(form_result.chunks)
        if scope_result:
            ranked_lists.append(scope_result.chunks)
        per_call_rrf_k = rrf_k if rrf_k is not None else self._rrf_k
        chunk_scores = reciprocal_rank_fuse(ranked_lists, rrf_k=per_call_rrf_k)

        # Build the chunk_map with keep-higher-score upsert.  For the first
        # list (dense, chunk_map empty) this is equivalent to first-wins.
        chunk_map: dict[str, RetrievedChunk] = {}
        for ranked in ranked_lists:
            for chunk in ranked:
                key = chunk.chunk_id
                if key not in chunk_map or chunk.score > chunk_map[key].score:
                    chunk_map[key] = chunk

        # Sort by fused RRF score descending
        fused_ids = sorted(chunk_scores, key=chunk_scores.get, reverse=True)  # type: ignore[arg-type]
        selected_ids = fused_ids[:top_k]

        # Non-lossy fusion invariant. A *newly added* arm is additive in pool
        # membership but not in rank: giving a third set of RRF points
        # re-orders the pool, and a marginal gold chunk sitting at the
        # ``top_k`` boundary can be pushed below the cut. That is exactly the
        # failure a structural arm must not cause — it is supposed to recover
        # gold, not displace it. So when the structural arm fired, the pool is
        # made a strict superset of what the arms already surfaced: every
        # chunk in any arm's own ``top_k`` is retained, appended after the
        # fusion order so the added arm still promotes what it found.
        #
        # Two details make that safe to hand to the reranker:
        #
        # * **Deduplicated.** Arms overlap by design (dense and sparse share
        #   hits; the identifier and form arms are both sparse retrievals), so
        #   a chunk missed by the cut is usually in several arms. Collecting
        #   into ``selected`` as we go — rather than testing against a
        #   snapshot — keeps one ``RetrievedChunk`` per chunk_id. Without it
        #   the same object lands in the pool twice and ``ContextBuilder``
        #   emits it under two ``[Source n]`` labels.
        # * **Bounded.** Every extra chunk is another cross-encoder forward
        #   pass on the hot path, so the protection is capped at ``top_k``
        #   extras, taken in fused-score order — the survivors that the
        #   structural arm actually promoted. The pool is therefore at most
        #   ``2 * top_k``, not the ~``5 * top_k`` an uncapped per-arm union
        #   would give once the identifier and form arms are also firing.
        #
        # Only applied when the scope arm fired, so the default two-arm path
        # is byte-identical to before.
        if scope_result is not None:
            selected = set(selected_ids)
            protected: list[str] = []
            for ranked in ranked_lists:
                if ranked is scope_result.chunks:
                    continue
                for chunk in ranked[:top_k]:
                    if chunk.chunk_id in selected:
                        continue
                    selected.add(chunk.chunk_id)
                    protected.append(chunk.chunk_id)
            if len(protected) > top_k:
                protected.sort(key=lambda cid: chunk_scores[cid], reverse=True)
                protected = protected[:top_k]
            selected_ids = selected_ids + protected
        fused_chunks = [chunk_map[cid] for cid in selected_ids]

        # Update scores to the RRF score
        for _i, chunk in enumerate(fused_chunks):
            chunk.score = chunk_scores[chunk.chunk_id]

        error = None
        if dense_result.error and not sparse_result.error:
            # Dense failed but sparse succeeded — that's OK
            error = dense_result.error
        elif dense_result.error and sparse_result.error:
            error = f"{dense_result.error}; {sparse_result.error}"

        # ponytail: MMR re-ranking removed — broken (walrus operator rebinds the
        # loop variable, falls back to first chunk in `selected`), never called
        # by any production code, and the Jaccard word-overlap heuristic was a
        # guess that evaluation data never validated.  Re-add when diversity is
        # a measured retrieval problem, not a hypothetical one.

        # Optional reranking
        if self.reranker is not None and fused_chunks:
            try:
                fused_chunks = self.reranker.rerank(query, fused_chunks, top_k=top_k, query_type=query_type)
            except (ConnectionError, RuntimeError) as exc:
                logger.warning("Reranker failed, returning unfused results: %s (%s)", exc, type(exc).__name__)
                fused_chunks = fused_chunks[:top_k]
        else:
            # No reranker to truncate the widened pool: honour ``top_k`` here
            # so ``retrieve`` keeps its documented contract. A no-op on the
            # default path, where the pool is already exactly ``top_k``.
            fused_chunks = fused_chunks[:top_k]

        return SearchResult(
            query=query,
            query_type="",
            chunks=fused_chunks,
            total=len(fused_chunks),
            latency_ms=int((time.monotonic() - start) * 1000),
            source="hybrid",
            error=error,
        )


# 1.3: Maximal Marginal Relevance re-ranking for diversity.
# Selects top_k chunks that maximize: λ * relevance - (1-λ) * max_similarity_to_selected
# Uses Jaccard word-overlap as a cheap, embedding-free similarity proxy
# (the chunk text is already available; no second embedding pass needed).

import re as _re


def _chunk_word_set(chunk: RetrievedChunk) -> set[str]:
    """Return the set of normalized words in a chunk's text."""
    return set(_re.findall(r"\b\w+\b", chunk.text.lower()))


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two word sets."""
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def mmr_rerank(
    fused_chunks: list[RetrievedChunk],
    top_k: int = 10,
    lambda_: float = 0.5,
) -> list[RetrievedChunk]:
    """Maximal Marginal Relevance re-ranking for diversity (1.3).

    Greedy selection: at each step, pick the chunk that maximizes
    ``lambda_ * relevance - (1 - lambda_) * max_jaccard_to_already_selected``.

    Args:
        fused_chunks: Chunks from RRF fusion (already scored).
        top_k: Maximum number of chunks to return.
        lambda_: Trade-off λ ∈ [0,1].  λ=1 = pure relevance, λ=0 = pure diversity.

    Returns:
        A list of at most ``top_k`` chunks re-ranked for diversity.

    """
    if not fused_chunks:
        return []

    selected: list = []
    remaining = sorted(fused_chunks, key=lambda c: c.score, reverse=True)

    # First pick: highest relevance
    if remaining:
        selected.append(remaining.pop(0))

    while len(selected) < top_k and remaining:
        best_chunk = None
        best_mmr = -float("inf")

        for chunk in remaining:
            rel = chunk.score
            # Max similarity to any already-selected chunk
            max_sim = 0.0
            for sel in selected:
                sim = _jaccard_similarity(_chunk_word_set(chunk), _chunk_word_set(sel))
                if sim > max_sim:
                    max_sim = sim

            mmr = lambda_ * rel - (1.0 - lambda_) * max_sim
            if mmr > best_mmr:
                best_mmr = mmr
                best_chunk = chunk

        if best_chunk is not None:
            selected.append(best_chunk)
            remaining.remove(best_chunk)
        else:
            break

    return selected
