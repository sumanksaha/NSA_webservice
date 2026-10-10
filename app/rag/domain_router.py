"""Domain-aware multi-collection routing for the FSSAI-primary RAG flow (Gap #2).

Feature-gated by ``RAG_MULTI_DOMAIN_ROUTER``. When enabled, queries carrying
mixed domain signals (e.g. *"A food business operator is improperly discarding
solid waste"* matches FSSAI **and** solid-waste/environment signals) are routed
to multiple collections — FSSAI first, then environment/municipality/other
domains — and the per-collection results are fused with rank-based priority
weighting so that the primary domain's chunks occupy the top slots while
secondary-domain chunks remain available for recall@20.

Design:
    - Rule-based domain classifier seeded with the FBO-violation keyword
      taxonomy (also documented in docs/RAG_FSSAI_PRIMARY_MULTI_DOMAIN_ANALYSIS.md
      §4.3). Conservative multi-match: every taxonomy family that matches votes
      for its domain.
    - Rank-based priority fusion: each chunk is scored by its rank within its
      own collection (RRF), then multiplied by a domain-priority weight; a
      deterministic "primary-dominance" guard then forces an FSSAI chunk into
      top-1 whenever FSSAI was retrieved and is outranked by a secondary chunk.
    - Feature-gated via ``cfg.multi_domain_router`` (Pattern A); when disabled
      every query degrades to the existing single-default-collection path — no
      silent cross-domain contamination.
    - Short-circuits to the single-collection fetch when the classifier
      matches only the primary domain, so non-mixed queries pay no multi-
      collection cost.

No Qdrant, no LLM: this module only orchestrates collection selection, fusion
math, and feature gating. The actual retriever construction lives in
``app/rag/retrieval/factory.py`` (single composition root).
"""

from __future__ import annotations

import logging
from typing import Any

from app.rag.collections import (
    DEFAULT_DOMAIN,
    collection_for_domain,
)
from app.rag.retrieval.factory import build_hybrid_retriever
from app.rag.retrieval.result import RetrievedChunk, SearchResult
from app.shared.config import cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword taxonomy — the deterministic seed for the rule-based domain
# classifier. Each (keywords -> domain) pair votes for a domain when any
# keyword is found in the lowercased query. Matches are de-duplicated in
# taxonomy order and weighted; FSSAI is the platform default so it stays
# first unless a query explicitly votes for another domain.
# ---------------------------------------------------------------------------

_KEYWORD_TAXONOMY: list[tuple[list[str], str]] = [
    # FSSAI identity / FBO + hygiene + penalty / correction families.
    (
        [
            "food business operator",
            "FBO",
            "food establishment",
            "manufacturer",
            "processor",
            "seller",
            "retailer",
            "vendor",
        ],
        "fssai",
    ),
    (
        ["solid waste", "waste", "dumping", "dump", "disposal", "dumped", "discarding"],
        "fssai",
    ),
    (
        ["hygiene", "unsanitary", "sanitation", "dirty", "filth", "vermin", "rodent", "pest"],
        "fssai",
    ),
    (
        ["penalty", "fine", "punishment", "imprisonment", "prosecution", "offence", "contravention"],
        "fssai",
    ),
    (
        ["improvement notice", "correct", "comply", "remedy", "CAPA", "remediation", "closure"],
        "fssai",
    ),
    # Environment / solid-waste-management family.
    (
        ["solid waste management", "segregation", "SWM", "recyclable", "biodegradable", "door-to-door"],
        "env",
    ),
    # Municipality / KMC family.
    (
        ["KMC", "municipal", "Kolkata", "municipality", "municipal commissioner", "ULB", "urban local body"],
        "wb_state",
    ),
]

# Default priority per domain — FSSAI-first is the platform policy
# (``DEFAULT_DOMAIN`` / ``DEFAULT_COLLECTION``). The weight dominates the
# secondary sort key at fusion time, promoting higher-priority-domain chunks
# toward the top while preserving each collection's internal RRF order.
DEFAULT_PRIORITY: dict[str, float] = {
    "fssai": 1.0,
    "env": 0.85,
    "wb_state": 0.80,
    "commercial": 0.60,
    "animal": 0.60,
    "criminal": 0.50,
}

#: Maximum candidate domains the router will fan out to per query.
_MAX_CANDIDATE_DOMAINS = 3


# ---------------------------------------------------------------------------
# Rule-based domain classifier
# ---------------------------------------------------------------------------


def classify_domain(query: str) -> list[tuple[str, float]]:
    """Classify *query* into a prioritized list of ``(domain, priority)`` pairs.

    Conservative multi-match: every taxonomy family whose keywords match the
    query votes for its domain. Votes are de-duplicated (a domain stays at the
    top in the order it first appears) and weighted: each matched family adds
    +0.5 to that domain's priority. If the query carries no vote at all, the
    single primary domain (FSSAI) is returned at the default 1.0 priority,
    which makes the router short-circuit to the existing default-collection
    fetch.

    The returned order is the fusion order: earlier domains' chunks are
    promoted toward the top of the fused pool.

    Args:
        query: The raw user query.

    Returns:
        A list of ``(domain, priority)`` pairs sorted descending by priority,
        always containing at least ``[(DEFAULT_DOMAIN, 1.0)]``.
    """
    lowered = query.lower()
    votes: list[str] = []  # de-duplicated vote order
    scores: dict[str, float] = {}  # vote bonuses per domain
    for family, domain in _KEYWORD_TAXONOMY:
        if any(kw.lower() in lowered for kw in family):
            if domain not in votes:
                votes.append(domain)
            scores[domain] = scores.get(domain, 0.0) + 0.5
    if not votes:
        return [(DEFAULT_DOMAIN, DEFAULT_PRIORITY[DEFAULT_DOMAIN])]
    result: list[tuple[str, float]] = []
    for domain in votes:
        result.append(
            (domain, DEFAULT_PRIORITY.get(domain, 0.5) + scores[domain]),
        )
    return sorted(result, key=lambda pair: pair[1], reverse=True)


# ---------------------------------------------------------------------------
# Multi-collection retrieval with FSSAI-first priority fusion
# ---------------------------------------------------------------------------


def _primary_dominance_guard(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Promote the best FSSAI chunk to top-1 when a secondary domain leads.

    Ablation target: with the production fusion weights (FSSAI=1.0,
    env=0.85, wb_state=0.80) and equal per-collection fetch widths, an FSSAI
    rank-1 chunk always outranks any secondary rank-1 chunk, so this guard
    rarely fires.  It exists as a safety net for changed fusion parameters or
    RL-selected RRF overrides and is asserted in isolation by tests.

    Returns a new list — never mutates the caller's pooled chunks.
    """
    if not chunks or chunks[0].domain == "fssai":
        return list(chunks)
    fssai = [c for c in chunks if c.domain == "fssai"]
    if not fssai:
        return list(chunks)
    best = max(fssai, key=lambda c: c.score)
    return [best] + [c for c in chunks if c is not best]


def run_multi_collection_retrieval(
    query: str,
    collection_names: list[str] | None = None,
    top_k: int = 10,
    per_collection_top_k: int = 12,
    rrf_k: float = 60.0,
    filters: dict[str, Any] | None = None,
    query_type: Any | None = None,
) -> SearchResult:
    """Retrieve *query* across multiple collections and fuse with FSSAI-first
    priority.

    Args:
        query: User query.
        collection_names: Explicit list of collection names (when ``None`` the
            top ``_MAX_CANDIDATE_DOMAINS`` of ``classify_domain`` are resolved
            via ``collections.py`` and fetched).
        top_k: Final pool size returned.
        per_collection_top_k: How many chunks to fetch per collection so the
            fused pool keeps enough secondary-domain candidates for recall@20.
        rrf_k: RRF constant.
        filters: Payload filters forwarded to each retriever.
        query_type: Legal query type (e.g. ``QueryType.PENALTY``) forwarded to
            the reranker so hierarchy boosting can apply.

    Returns:
        A :class:`SearchResult` whose chunks each carry ``domain`` and
        ``collection`` attributes tagging their origin, and ``source ==
        "multi-domain"``.
    """
    if collection_names is None:
        domains = classify_domain(query)
        collection_names = [collection_for_domain(domain) for domain, _ in domains[:_MAX_CANDIDATE_DOMAINS]]
    logger.debug(
        "multi-domain route: collections=%s top_k=%s per_collection=%s", collection_names, top_k, per_collection_top_k
    )

    domain_of: dict[str, str] = {}
    results: list[tuple[str, SearchResult]] = []
    for collection in collection_names:
        domain = collection.replace("_legal_768", "")
        domain_of[collection] = domain
        retriever = build_hybrid_retriever(collection)
        res = retriever.retrieve(
            query,
            top_k=per_collection_top_k,
            filters=filters,
            query_type=query_type,
            rrf_k=rrf_k,
        )
        if res.error:
            logger.warning(
                "multi-domain route: collection %s (domain %s) returned error %s",
                collection,
                domain,
                res.error,
            )
            continue
        for chunk in res.chunks:
            chunk.domain = domain
            chunk.collection = collection
        results.append((collection, res))

    if not results:
        return SearchResult(
            query=query,
            query_type=query_type.value if query_type else "",
            chunks=[],
            total=0,
            error="no collection returned results",
            source="multi-domain",
        )

    # Rank-based priority fusion: base = RRF within the originating collection,
    # then multiplied by the domain priority so chunks from higher-priority
    # domains (FSSAI) are promoted toward the top while each collection's
    # internal ranking is preserved.
    scored: list[tuple[float, int, RetrievedChunk]] = []
    for collection, res in results:
        domain = domain_of[collection]
        prio = DEFAULT_PRIORITY.get(domain, 0.5)
        for rank, chunk in enumerate(res.chunks, start=1):
            base = 1.0 / (rank + rrf_k)
            scored.append((base * prio, rank, chunk))
    scored.sort(key=lambda t: (t[0], -t[1]), reverse=True)

    # Dedupe by chunk_id (a chunk can appear in more than one collection),
    # keeping the highest-scoring instance.
    deduped: dict[str, RetrievedChunk] = {}
    for _fused, _rank, chunk in scored:
        deduped.setdefault(chunk.chunk_id, chunk)
    final_chunks = list(deduped.values())[:top_k]

    # Optionally re-score across the cross-domain pool with the configured CE
    # reranker (keeps the multi-domain pool coherent at LLM-semantic level).
    if cfg.ensemble_rerank and final_chunks:
        try:
            final_chunks = list(
                build_hybrid_retriever().reranker.rerank(query, final_chunks, top_k=top_k, query_type=query_type)
            )
        except Exception as exc:
            logger.warning("multi-domain rerank failed, keeping priority-fused pool: %s", exc)

    # Primary-dominance guard (ablation target): FSSAI must hold top-1 whenever
    # FSSAI chunks were retrieved and a non-FSSAI chunk currently leads.
    final_chunks = _primary_dominance_guard(final_chunks)

    return SearchResult(
        query=query,
        query_type=query_type.value if query_type else "",
        chunks=final_chunks,
        total=len(final_chunks),
        source="multi-domain",
        error=None,
    )
