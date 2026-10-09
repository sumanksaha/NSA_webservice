"""Food-intent view — one pre-computed value for every food-stage decision.

The food-commodity view used to be re-derived at each pipeline stage: the
query was parsed three times (retrieval understanding, stage 2b rerank,
generation prompt), the fetch width was special-cased inline in
``run_retrieval_pipeline``, and the ~190-line
``_retrieval_food_rerank_validate`` stage threaded 13 parameters just to
carry pass-throughs back into the fetch it already came from.

This module concentrates those decisions behind one seam, mirroring the
established ``QueryUnderstanding`` shape (``retrieval/query_understanding.py``):
the query is parsed once, and every stage asks narrow questions of the
pre-computed view instead of re-deriving answers.

* ``FoodView.for_query(query)`` — single construction site. Returns ``None``
  when the food-intent flag is off, so flag-off callers keep byte-for-byte
  behaviour without branching.
* ``view.fetch_width(top_k)`` — candidate-pool width for this query.
* ``food_stage_2b(view, result, fetch)`` — rerank + anchor/sibling refetch +
  validation + fallback + bundle reconstruction. ``fetch`` is the caller's
  evidence-fetch callable, so this module never imports the orchestrator.

The pure parse value (``FoodQueryUnderstanding``) is untouched — this module
owns stage policy only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.shared.config import cfg

logger = logging.getLogger(__name__)

__all__ = ["FoodFetchContext", "FoodView", "food_stage_2b"]


@dataclass(frozen=True)
class FoodFetchContext:
    """Everything stage 2b needs to call back into evidence fetch.

    One value replaces the nine pass-through parameters the old
    ``_retrieval_food_rerank_validate`` carried (top_k, collection,
    filters, query_type, legal view, identifier pair, form query, cache).
    """

    top_k: int
    collection_name: str | None
    merged_filters: dict[str, Any]
    query_type: Any
    legal_qt: Any | None
    identifier: dict[str, Any] | None
    identifier_query: str | None
    form_query: str | None = None
    cache: Any = None


@dataclass(frozen=True)
class FoodView:
    """Pre-computed food-intent answers for one query.

    Built once per pipeline call via :meth:`for_query`; stages read answers
    off the value instead of re-deriving them.
    """

    query: str
    food: Any
    anchor_budget: list[int] = field(default_factory=lambda: [1])
    sibling_budget: list[int] = field(default_factory=lambda: [1])

    @classmethod
    def for_query(cls, query: str) -> FoodView | None:
        """Parse once; ``None`` when the food-intent flag is off."""
        if not cfg.food_intent_enabled:
            return None
        try:
            from app.rag.retrieval.food_query_understanding import (
                FoodQueryUnderstanding,
            )

            food = FoodQueryUnderstanding.from_query(query)
        except Exception as exc:
            logger.warning("FoodView parse failed: %s", exc)
            return None
        return cls(query=query, food=food)

    def fetch_width(self, top_k: int) -> int:
        """Candidate-pool width: wider when the view wants clause siblings."""
        if cfg.food_legal_rerank or cfg.food_parent_reconstruct:
            return max(top_k, 30)
        return top_k

    def to_result_dict(self) -> dict[str, Any]:
        """The ``food_understanding`` payload for the result dict."""
        return self.food.to_dict()


def food_stage_2b(
    view: FoodView,
    result: Any,
    ctx: FoodFetchContext,
    fetch: Callable[..., Any],
) -> tuple[Any, dict[str, Any] | None, int]:
    """Stage 2b — legal-aware rerank + validation + fallback retrieval.

    Returns ``(result, trace, fallback_rounds_used)``. Every step is
    best-effort: any failure returns the incoming result unchanged.
    """
    from app.rag.retrieval.legal_reranker import LegalAwareReranker
    from app.rag.retrieval.parent_reconstruction import reconstruct_evidence_bundle
    from app.rag.retrieval.validation import validate_retrieval

    query = view.query
    food = view.food
    trace: dict[str, Any] | None = None
    fallback_rounds_used = 0
    try:
        reranker = LegalAwareReranker(enabled=cfg.food_legal_rerank)
        chunks = list(result.chunks)
        ranked = reranker.rerank(query, chunks, food=food)

        _fetch_anchor(view, ranked, reranker, ctx, fetch)

        _fetch_siblings(view, ranked, reranker, ctx, fetch)

        validation = validate_retrieval(query, ranked, food=food)
        if not validation.get("valid") and cfg.food_validate and cfg.food_fallback_rounds > 0:
            fallback_rounds_used = _fallback_rounds(view, ranked, reranker, ctx, fetch, validation)

        result.chunks = ranked[: ctx.top_k]
        result.total = len(result.chunks)

        if cfg.food_parent_reconstruct:
            bundle = reconstruct_evidence_bundle(query, result.chunks, food=food)
            result.evidence_bundle = bundle  # type: ignore[attr-defined]
            if trace is None:
                trace = {}
            trace["evidence_complete"] = bool((bundle.get("completeness") or {}).get("intent_satisfied"))
            trace["completeness"] = bundle.get("completeness")
            trace["legal_source"] = bundle.get("legal_source")

        if trace is None:
            trace = {}
        trace["fallback_triggered"] = fallback_rounds_used > 0
        trace["fallback_rounds"] = fallback_rounds_used
        trace["validation"] = dict(validation)
    except Exception as exc:
        logger.warning("food_stage_2b failed (%s) — hybrid ranking kept", exc)
        trace = {"error": str(exc)}
    return result, trace, fallback_rounds_used


def _fetch_anchor(
    view: FoodView,
    ranked: list[Any],
    reranker: Any,
    ctx: FoodFetchContext,
    fetch: Callable[..., Any],
) -> None:
    """Dynamic identity anchor (ablation iteration 4)."""
    from app.rag.retrieval.provision_metadata import commodity_phrase_match

    food = view.food
    if not (food.entity and food.entity != "unknown"):
        return
    has_anchor = any(
        commodity_phrase_match(str(getattr(c, "text", "") or ""), food.entity) for c in ranked
    )
    if has_anchor or view.anchor_budget[0] <= 0:
        return
    view.anchor_budget[0] -= 1
    anchor_result = fetch(
        f"{food.entity} means",
        top_k=5,
        collection_name=ctx.collection_name,
        merged_filters=ctx.merged_filters,
        query_type=ctx.query_type,
        legal_qt=ctx.legal_qt,
        identifier=ctx.identifier,
        identifier_query=ctx.identifier_query,
        form_query=None,
        cache=ctx.cache,
    )
    seen_ids = {c.chunk_id for c in ranked}
    fresh = [c for c in anchor_result.chunks if c.chunk_id not in seen_ids]
    if fresh:
        ranked[:] = reranker.rerank(view.query, [*fresh, *ranked], food=food)


def _fetch_siblings(
    view: FoodView,
    ranked: list[Any],
    reranker: Any,
    ctx: FoodFetchContext,
    fetch: Callable[..., Any],
) -> None:
    """Exact clause-sibling fetch (ablation iteration 5)."""
    from app.rag.qdrant_client import QdrantStore
    from app.rag.retrieval.provision_metadata import commodity_phrase_match
    from app.rag.retrieval.provision_metadata import (
        derive_provision_metadata_cached as _dpm,
    )
    from app.rag.tasks import _point_to_chunk

    food = view.food
    if not (food.entity and food.entity != "unknown" and view.sibling_budget[0] > 0):
        return
    heading = next(
        (
            c
            for c in ranked
            if _dpm(c).get("commodity") == food.entity
            and str(_dpm(c).get("section", "unknown")) != "unknown"
            and commodity_phrase_match(str(getattr(c, "text", "") or ""), food.entity)
        ),
        None,
    )
    if heading is None:
        return
    doc_id = str(_dpm(heading).get("document_id", "") or "")
    clause_no = str(_dpm(heading).get("section") or "")
    clause_pool_size = sum(
        1
        for c in ranked
        if str(getattr(c, "document_id", "") or "") == doc_id
        and str(getattr(c, "clause_number", "") or getattr(c, "section_number", "") or "") == clause_no
    )
    if clause_pool_size >= 3:
        return
    view.sibling_budget[0] -= 1
    try:
        store = QdrantStore(collection_name=ctx.collection_name)
        points = store.scroll_all(
            batch_size=500,
            filters={"document_id": doc_id, "clause_number": clause_no},
        )
        seen_ids = {c.chunk_id for c in ranked}
        fresh = []
        for p in points:
            payload = p.get("payload") or {}
            pid = str(p.get("id"))
            if pid in seen_ids:
                continue
            fresh.append(_point_to_chunk(p, pid, payload))
        if fresh:
            ranked[:] = reranker.rerank(view.query, [*fresh, *ranked], food=food)
    except Exception as exc:
        logger.warning("clause-sibling fetch failed (non-fatal): %s", exc)


def _fallback_rounds(
    view: FoodView,
    ranked: list[Any],
    reranker: Any,
    ctx: FoodFetchContext,
    fetch: Callable[..., Any],
    validation: dict[str, Any],
) -> int:
    """Fallback retrieval: deterministic arms merged until valid."""
    from app.rag.retrieval.validation import fallback_queries, validate_retrieval

    rounds_used = 0
    for round_no in range(1, cfg.food_fallback_rounds + 1):
        fb_queries = fallback_queries(view.query, food=view.food)
        fb_query = fb_queries[min(round_no - 1, len(fb_queries) - 1)] if fb_queries else None
        if not fb_query:
            break
        fb_result = fetch(
            fb_query,
            top_k=max(ctx.top_k, 20),
            collection_name=ctx.collection_name,
            merged_filters=ctx.merged_filters,
            query_type=ctx.query_type,
            legal_qt=ctx.legal_qt,
            identifier=ctx.identifier,
            identifier_query=ctx.identifier_query,
            form_query=None,
            cache=ctx.cache,
        )
        rounds_used = round_no
        seen_ids = {c.chunk_id for c in ranked}
        merged = list(ranked)
        for c in fb_result.chunks:
            if c.chunk_id not in seen_ids:
                merged.append(c)
                seen_ids.add(c.chunk_id)
        ranked[:] = reranker.rerank(fb_query, merged, food=view.food)
        validation = validate_retrieval(view.query, ranked, food=view.food)
        if validation.get("valid"):
            break
    return rounds_used


