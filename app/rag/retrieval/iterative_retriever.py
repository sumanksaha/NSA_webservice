"""Iterative retrieval with re-ranking.

After the initial fetch + rerank, detects coverage gaps in the evidence set
(missing evidence types, low complementarity, unbacked query terms) and
issues targeted follow-up queries to fill them. Re-ranks the merged pool
after each round. Stops when coverage meets the threshold or max rounds
are reached.

Gated by ``RAG_ITERATIVE_RETRIEVAL`` (default off).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from app.shared.config import cfg

logger = logging.getLogger(__name__)

#: Evidence types considered "required" for a well-rounded answer.
_REQUIRED_TYPES = {"primary_provision", "exception", "definition", "penalty_provision"}

#: Query templates for gap-filling follow-ups. Each maps a missing evidence
#: type to a targeted query suffix.
_GAP_QUERY_TEMPLATES: dict[str, str] = {
    "exception": "{query} exception proviso unless",
    "definition": "{query} definition means includes",
    "penalty_provision": "{query} penalty fine imprisonment punishment",
    "cross_reference": "{query} section cross reference",
    "authority": "{query} authority power",
    "adjacent_section": "{query} adjacent section",
}


def _evidence_coverage(evidence_set: Any) -> float:
    """Compute evidence-type coverage (0-1) of the current evidence set.

    Returns the fraction of ``_REQUIRED_TYPES`` present in the set's items.
    """
    if not evidence_set or not getattr(evidence_set, "items", None):
        return 0.0
    present = {getattr(item, "evidence_type", "") for item in evidence_set.items}
    covered = len(_REQUIRED_TYPES & present)
    return covered / len(_REQUIRED_TYPES) if _REQUIRED_TYPES else 1.0


def _missing_types(evidence_set: Any) -> list[str]:
    """Return the evidence types from _REQUIRED_TYPES not yet covered."""
    if not evidence_set or not getattr(evidence_set, "items", None):
        return list(_REQUIRED_TYPES)
    present = {getattr(item, "evidence_type", "") for item in evidence_set.items}
    return sorted(_REQUIRED_TYPES - present)


def _dedup_chunks(chunks: list[Any]) -> list[Any]:
    """Deduplicate chunks by chunk_id, keeping the highest-scored duplicate."""
    seen: dict[str, Any] = {}
    for c in chunks:
        cid = getattr(c, "chunk_id", None)
        if cid is None and isinstance(c, dict):
            cid = c.get("chunk_id")
        if cid is None:
            seen[str(id(c))] = c
            continue
        cid = str(cid)
        if cid not in seen or _score(c) > _score(seen[cid]):
            seen[cid] = c
    return list(seen.values())


def _score(c: Any) -> float:
    """Extract score from a chunk (object or dict)."""
    if isinstance(c, dict):
        return float(c.get("score", 0.0))
    return float(getattr(c, "score", 0.0))


def _build_gap_query(original_query: str, missing_type: str) -> str:
    """Build a targeted follow-up query for a missing evidence type."""
    template = _GAP_QUERY_TEMPLATES.get(missing_type)
    if template:
        return template.format(query=original_query)
    return f"{original_query} {missing_type}"


class IterativeRetriever:
    """Run iterative retrieval with re-ranking.

    Args:
        fetch_fn: Callable that runs a single retrieval pass.
            Signature: ``fetch_fn(query, top_k) -> list[RetrievedChunk]``
        rerank_fn: Callable that re-ranks a list of chunks.
            Signature: ``rerank_fn(query, chunks, top_k) -> list[RetrievedChunk]``
        select_fn: Callable that selects an evidence set from chunks.
            Signature: ``select_fn(query, chunks, max_size, min_size) -> EvidenceSet``
        expand_fn: Optional callable that expands an evidence set.
            Signature: ``expand_fn(evidence, pool, max_expansion, max_size) -> EvidenceSet``
        max_rounds: Maximum number of retrieval rounds.
        min_coverage: Minimum evidence-type coverage before stopping.
        top_k: Chunks to fetch per round.
        max_size: Maximum evidence items in the final set.
    """

    def __init__(
        self,
        fetch_fn: Callable[[str, int], list[Any]],
        rerank_fn: Callable[[str, list[Any], int | None], list[Any]] | None = None,
        select_fn: Callable[[str, list[Any], int, int], Any] | None = None,
        expand_fn: Callable[[Any, list[Any], int, int], Any] | None = None,
        max_rounds: int = 3,
        min_coverage: float = 0.7,
        top_k: int = 20,
        max_size: int = 5,
    ) -> None:
        self.fetch_fn = fetch_fn
        self.rerank_fn = rerank_fn
        self.select_fn = select_fn
        self.expand_fn = expand_fn
        self.max_rounds = max_rounds
        self.min_coverage = min_coverage
        self.top_k = top_k
        self.max_size = max_size

    def run(
        self,
        query: str,
        initial_chunks: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Run iterative retrieval.

        Returns a dict with:
            - chunks: final merged chunk list
            - evidence_set: final EvidenceSet (if select_fn provided)
            - rounds: number of retrieval rounds executed
            - coverage: final evidence-type coverage
            - gaps_filled: list of evidence types filled across rounds
            - latency_ms: total elapsed time
        """
        start = time.monotonic()
        all_chunks: list[Any] = list(initial_chunks) if initial_chunks else []
        gaps_filled: list[str] = []
        rounds = 0

        # Round 0: initial fetch (if no initial chunks provided)
        if not all_chunks:
            try:
                fetched = self.fetch_fn(query, self.top_k)
                all_chunks.extend(fetched)
                rounds += 1
            except Exception as exc:
                logger.warning("IterativeRetriever: initial fetch failed (%s)", exc)

        # Re-rank the initial pool
        if self.rerank_fn and all_chunks:
            try:
                all_chunks = self.rerank_fn(query, all_chunks, self.top_k)
            except Exception as exc:
                logger.warning("IterativeRetriever: initial rerank failed (%s)", exc)

        # Select initial evidence set
        evidence_set = None
        if self.select_fn and all_chunks:
            try:
                evidence_set = self.select_fn(query, all_chunks, self.max_size, 2)
            except Exception as exc:
                logger.warning("IterativeRetriever: initial selection failed (%s)", exc)

        # Iterative gap-filling rounds
        for round_num in range(1, self.max_rounds):
            if evidence_set is None:
                break
            coverage = _evidence_coverage(evidence_set)
            if coverage >= self.min_coverage:
                logger.debug(
                    "IterativeRetriever: coverage %.2f >= %.2f, stopping",
                    coverage, self.min_coverage,
                )
                break

            missing = _missing_types(evidence_set)
            if not missing:
                break

            # Pick the highest-priority missing type
            target_type = missing[0]
            gap_query = _build_gap_query(query, target_type)
            logger.debug(
                "IterativeRetriever: round %d, fetching for missing type %s",
                round_num, target_type,
            )

            try:
                fetched = self.fetch_fn(gap_query, self.top_k)
                rounds += 1
                all_chunks.extend(fetched)
                all_chunks = _dedup_chunks(all_chunks)

                # Re-rank the merged pool
                if self.rerank_fn:
                    all_chunks = self.rerank_fn(query, all_chunks, self.top_k)

                # Re-select evidence set
                if self.select_fn:
                    evidence_set = self.select_fn(query, all_chunks, self.max_size, 2)

                # Expand if available
                if self.expand_fn and evidence_set is not None:
                    try:
                        evidence_set = self.expand_fn(
                            evidence_set, all_chunks, 3, self.max_size,
                        )
                    except Exception as exc:
                        logger.warning("IterativeRetriever: expansion failed (%s)", exc)

                gaps_filled.append(target_type)
            except Exception as exc:
                logger.warning(
                    "IterativeRetriever: round %d fetch failed (%s)", round_num, exc,
                )
                break

        # Final re-rank
        if self.rerank_fn and all_chunks:
            try:
                all_chunks = self.rerank_fn(query, all_chunks, self.top_k)
            except Exception as exc:
                logger.warning("IterativeRetriever: final rerank failed (%s)", exc)

        # Final evidence selection
        if self.select_fn and all_chunks:
            try:
                evidence_set = self.select_fn(query, all_chunks, self.max_size, 2)
            except Exception as exc:
                logger.warning("IterativeRetriever: final selection failed (%s)", exc)

        latency_ms = int((time.monotonic() - start) * 1000)
        final_coverage = _evidence_coverage(evidence_set) if evidence_set else 0.0

        return {
            "chunks": all_chunks,
            "evidence_set": evidence_set,
            "rounds": rounds,
            "coverage": final_coverage,
            "gaps_filled": gaps_filled,
            "latency_ms": latency_ms,
        }


def run_iterative_retrieval(
    query: str,
    top_k: int = 20,
    max_rounds: int | None = None,
    min_coverage: float | None = None,
    fetch_fn: Callable[[str, int], list[Any]] | None = None,
    rerank_fn: Callable[[str, list[Any], int | None], list[Any]] | None = None,
    select_fn: Callable[[str, list[Any], int, int], Any] | None = None,
    expand_fn: Callable[[Any, list[Any], int, int], Any] | None = None,
    initial_chunks: list[Any] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for iterative retrieval.

    Wires up the standard components from the retrieval stack when not
    explicitly provided. Returns the same dict as ``IterativeRetriever.run``.
    """
    max_rounds = max_rounds or cfg.iterative_max_rounds
    min_coverage = min_coverage if min_coverage is not None else cfg.iterative_min_coverage

    # Default fetch: use the hybrid retriever
    if fetch_fn is None:
        from app.rag.retrieval.factory import build_hybrid_retriever

        def _fetch(q: str, k: int) -> list[Any]:
            retriever = build_hybrid_retriever()
            result = retriever.retrieve(q, top_k=k)
            return list(result.chunks)

        fetch_fn = _fetch

    # Default rerank: use the ensemble reranker
    if rerank_fn is None:
        try:
            from app.rag.retrieval.factory import build_reranker

            _reranker = build_reranker()

            def _rerank(q: str, chunks: list[Any], k: int | None) -> list[Any]:
                return _reranker.rerank(q, chunks, top_k=k)

            rerank_fn = _rerank
        except Exception as exc:
            logger.warning("IterativeRetriever: reranker unavailable (%s)", exc)
            rerank_fn = None

    # Default select: use evidence_selector
    if select_fn is None:
        try:
            from app.rag.retrieval.evidence_selector import select_evidence_set

            def _select(q: str, chunks: list[Any], max_sz: int, min_sz: int) -> Any:
                return select_evidence_set(q, chunks, max_size=max_sz, min_size=min_sz)

            select_fn = _select
        except Exception as exc:
            logger.warning("IterativeRetriever: evidence selector unavailable (%s)", exc)
            select_fn = None

    # Default expand: use expand_evidence_units
    if expand_fn is None:
        try:
            from app.rag.retrieval.evidence_selector import expand_evidence_units

            def _expand(es: Any, pool: list[Any], max_exp: int, max_sz: int) -> Any:
                return expand_evidence_units(es, pool, max_expansion=max_exp, max_size=max_sz)

            expand_fn = _expand
        except Exception as exc:
            logger.warning("IterativeRetriever: evidence expander unavailable (%s)", exc)
            expand_fn = None

    retriever = IterativeRetriever(
        fetch_fn=fetch_fn,
        rerank_fn=rerank_fn,
        select_fn=select_fn,
        expand_fn=expand_fn,
        max_rounds=max_rounds,
        min_coverage=min_coverage,
        top_k=top_k,
        max_size=5,
    )
    return retriever.run(query, initial_chunks=initial_chunks)