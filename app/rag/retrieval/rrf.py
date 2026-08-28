"""Reciprocal Rank Fusion (RRF) — shared scoring core.

Eliminates the 5× duplication of the RRF formula ``1 / (rank + 1 + rrf_k)``
across:

- ``HybridRetriever.retrieve`` (3 inline loops in ``hybrid_retriever.py``)
- ``kg.hybrid.rrf_fuse_chunks``
- ``evaluation.fusion.rrf_fuse_items``

The shared function computes *scores only* — callers manage which object to
keep per key (first-wins vs keep-higher-score), pre-processing (KG dedup),
and top-k slicing in their own domain-specific wrappers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

#: RRF constant — standard value from Cormack et al., 2009.
DEFAULT_RRF_K = 60.0


def _default_key_fn(item: Any) -> Any:
    """Extract the dedup key from a chunk-like item (default)."""
    return getattr(item, "chunk_id", None) or str(item)


def reciprocal_rank_fuse(
    ranked_lists: Iterable[list[Any]],
    rrf_k: float = DEFAULT_RRF_K,
    key_fn: Callable[[Any], Any] | None = None,
) -> dict[Any, float]:
    """Accumulate RRF scores across multiple ranked lists.

    For each ``(rank, item)`` pair, adds ``1 / (rank + 1 + rrf_k)`` to the
    item's score (keyed by ``key_fn(item)``).  Items appearing in several
    lists accumulate — this is the *agreement boost* that makes RRF
    rank-stable.

    Does **not** manage which object to keep per key — callers do that with
    their own policy (first-wins or keep-higher-score), plus any domain
    pre-processing (e.g. KG dedup) and top-k slicing.

    Args:
        ranked_lists: Iterable of ranked item lists.  Each list is assumed
            already sorted by relevance (rank 0 = most relevant).
        rrf_k: RRF constant.  Larger values make lower-ranked items
            contribute more.
        key_fn: Extracts the dedup key from each item.  When ``None``
            (default), falls back to ``getattr(item, "chunk_id", None)``
            then ``str(item)`` — works with ``RetrievedChunk`` objects and
            plain strings / tuples alike.

    Returns:
        ``dict`` mapping each key to its fused RRF score.
    """
    if key_fn is None:
        key_fn = _default_key_fn
    scores: dict[Any, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            key = key_fn(item)
            if not key:
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (rank + 1 + rrf_k)
    return scores
