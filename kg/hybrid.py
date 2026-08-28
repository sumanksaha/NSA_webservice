"""Qdrant ↔ Neo4j hybrid expansion (Option F — 2026-08-11).

Implements the graph-expansion step of the RAG pipeline: after Qdrant dense/
sparse retrieval returns chunks, this module expands those chunk IDs through
the legal Knowledge Graph into *structured legal context*:

    Qdrant chunk (chunk_id / qdrant_point_id)
        → Chunk node
        → supporting LegalProvision(s)
        → instrument, domain, temporal status, authorities, cross-refs
        → source Document + provenance

This converts a flat vector hit into the graph evidence the audit's D8/D9
dimensions require: "Qdrant result can be expanded through Neo4j" and
"Neo4j result can retrieve source chunks from Qdrant".

Design rules:

1. **Keyed on both ID spaces** — a Qdrant point may arrive as ``chunk_id``
   (payload ``chunk_id``, = point id for multi-domain) or as the DB
   ``qdrant_point_id`` (FSS chunks).  The expander matches either property.
2. **Graceful** — no Neo4j configured, no match, or a query error degrades to
   an empty expansion (never raises); the RAG pipeline keeps working.
3. **Deduplicated** — expansions are keyed by ``provision_id``.
4. **Cheap** — a single batched Cypher call per expansion (UNWIND over the
   retrieved chunk IDs); no per-chunk round trips.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

#: Maximum chunk IDs to expand in one call (protects the Cypher query size).
MAX_CHUNK_IDS = 200


class KGContextExpander:
    """Expand Qdrant chunk IDs into structured Neo4j legal context.

    Args:
        driver: Optional pre-built Neo4j driver (injected for tests).
        database: Neo4j database name (default from ``NEO4J_DATABASE`` env).
        max_chunk_ids: Cap on chunk IDs expanded per call.
    """

    def __init__(
        self,
        driver: Any | None = None,
        database: str | None = None,
        max_chunk_ids: int = MAX_CHUNK_IDS,
    ) -> None:
        self._driver = driver
        self._database = database or os.environ.get("NEO4J_DATABASE", "neo4j")
        self._own_driver = False
        self.max_chunk_ids = max_chunk_ids

    # ------------------------------------------------------------------ #
    # Driver plumbing
    # ------------------------------------------------------------------ #

    def _get_driver(self) -> Any:
        if self._driver is None:
            from app.services.neo4j_graph import _get_driver

            self._driver = _get_driver()
            self._own_driver = True
        return self._driver

    @staticmethod
    def configured() -> bool:
        """Whether Neo4j credentials are present (cheap pre-check)."""
        return bool(
            os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_USERNAME") and os.environ.get("NEO4J_PASSWORD")
        )

    def _execute(self, cypher: str, params: dict | None = None) -> list[dict]:
        result = self._get_driver().execute_query(cypher, parameters_=params or {}, database_=self._database)
        return [dict(r) for r in result.records]

    # ------------------------------------------------------------------ #
    # Expansion
    # ------------------------------------------------------------------ #

    def expand_chunks(self, chunk_ids: Iterable[str]) -> dict[str, Any]:
        """Expand *chunk_ids* (Qdrant point / chunk IDs) into graph context.

        Returns::

            {
              "enabled": true,          # false when Neo4j not configured
              "matched_chunks": 3,      # chunk nodes found
              "provisions": [...],      # deduped, structured
              "domains": [...],
              "latency_ms": 12,
              "error": None,
            }

        Never raises — the RAG pipeline treats expansion as best-effort.
        """
        started = time.monotonic()
        ids = [str(i) for i in chunk_ids if i is not None]
        ids = list(dict.fromkeys(ids))[: self.max_chunk_ids]
        if not ids:
            return self._empty(enabled=True, error=None)

        if not self.configured() and self._driver is None:
            return self._empty(enabled=False, error="Neo4j not configured")

        try:
            # Part 1: chunks -> provisions (one batched call)
            rows = self._execute(
                """
                UNWIND $chunk_ids AS cid
                MATCH (c:Chunk)
                WHERE c.chunk_id = cid OR c.qdrant_point_id = cid
                OPTIONAL MATCH (c)<-[:SUPPORTED_BY]-(p:LegalProvision)
                OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
                OPTIONAL MATCH (i)-[:CONTAINS]->(p)
                OPTIONAL MATCH (p)-[:SOURCE_OF]->(doc:Document)
                OPTIONAL MATCH (p)-[:GRANTS_POWER_TO]->(auth:Authority)
                OPTIONAL MATCH (i)-[:ISSUED_BY]->(issuer:Authority)
                RETURN
                    cid AS chunk_id,
                    c.chunk_id AS chunk_node_id,
                    c.qdrant_point_id AS qdrant_point_id,
                    p.provision_id AS provision_id,
                    p.provision_number AS provision_number,
                    p.title AS title,
                    left(coalesce(p.provision_text, ''), 500) AS text,
                    coalesce(p.status, '') AS status,
                    i.instrument_id AS instrument_id,
                    i.title AS instrument_title,
                    coalesce(d.domain_name, p.legal_domain, '') AS legal_domain,
                    doc.document_id AS document_id,
                    doc.source_uri AS document_uri,
                    auth.name AS authority_name,
                    issuer.name AS issuing_authority_name
                """,
                {"chunk_ids": ids},
            )
            chunk_ids_matched = {r.get("chunk_id") for r in rows if r.get("chunk_node_id")}
            provisions: dict[str, dict[str, Any]] = {}
            domains: set[str] = set()
            for r in rows:
                if not r.get("provision_id"):
                    continue
                pid = r["provision_id"]
                entry = provisions.setdefault(
                    pid,
                    {
                        "provision_id": pid,
                        "provision_number": _unwrap(r.get("provision_number")),
                        "title": _unwrap(r.get("title")) or "",
                        "text": _unwrap(r.get("text")) or "",
                        "status": _unwrap(r.get("status")) or "",
                        "instrument_id": _unwrap(r.get("instrument_id")) or "",
                        "instrument_title": _unwrap(r.get("instrument_title")) or "",
                        "legal_domain": _unwrap(r.get("legal_domain")) or "",
                        "document_id": _unwrap(r.get("document_id")) or "",
                        "document_uri": _unwrap(r.get("document_uri")) or "",
                        "authorities": [],
                        "issuing_authority": _unwrap(r.get("issuing_authority_name")) or "",
                    },
                )
                if r.get("authority_name"):
                    name = _unwrap(r["authority_name"])
                    if name and name not in entry["authorities"]:
                        entry["authorities"].append(name)
                if not entry["issuing_authority"] and r.get("issuing_authority_name"):
                    name = _unwrap(r["issuing_authority_name"])
                    if name:
                        entry["issuing_authority"] = name
                if entry["legal_domain"]:
                    domains.add(entry["legal_domain"])

            # Part 2: related (cross-domain / cross-reference) provisions
            related_rows = self._execute(
                """
                UNWIND $provision_ids AS pid
                MATCH (p:LegalProvision {provision_id: pid})-[r]->(other:LegalProvision)
                WHERE type(r) IN ['CROSS_REFERENCES','COMPLEMENTS','INTERACTS_WITH','DEPENDS_ON']
                OPTIONAL MATCH (other)-[:BELONGS_TO_DOMAIN]->(od:LegalDomain)
                RETURN
                    pid AS source_id,
                    other.provision_id AS related_id,
                    other.provision_number AS related_number,
                    other.title AS related_title,
                    type(r) AS rel_type,
                    r.evidence AS evidence,
                    coalesce(od.domain_name, other.legal_domain, '') AS related_domain
                ORDER BY pid
                """,
                {"provision_ids": list(provisions.keys())},
            )
            related: dict[str, list[dict[str, Any]]] = {}
            for r in related_rows:
                src = r.get("source_id")
                if not src:
                    continue
                related.setdefault(src, []).append({
                    "related_id": _unwrap(r.get("related_id")),
                    "related_number": _unwrap(r.get("related_number")),
                    "related_title": _unwrap(r.get("related_title")) or "",
                    "rel_type": _unwrap(r.get("rel_type")) or "",
                    "evidence": _unwrap(r.get("evidence")) or "",
                    "related_domain": _unwrap(r.get("related_domain")) or "",
                })
            for entry in provisions.values():
                entry["related"] = related.get(entry["provision_id"], [])
        except Exception as exc:
            logger.warning("KGContextExpander failed: %s", exc)
            return self._empty(enabled=True, error=str(exc))

        return {
            "enabled": True,
            "chunk_ids_input": len(ids),
            "matched_chunks": len(chunk_ids_matched),
            "provisions": list(provisions.values()),
            "domains": sorted(domains),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": None,
        }

    def expand_query_context(self, query: str) -> dict[str, Any]:
        """Expand a *query* via the graph-RAG interface (concept + domain).

        Complements vector retrieval with the graph layer: cross-domain laws
        for concepts mentioned in the query, plus current provisions.
        Best-effort; never raises.
        """
        started = time.monotonic()
        if not self.configured() and self._driver is None:
            return self._empty(enabled=False, error="Neo4j not configured")
        try:
            from kg.queries import LegalKGQueries, build_llm_retrieval_contract

            queries = LegalKGQueries(driver=self._get_driver(), database=self._database)
            contract = build_llm_retrieval_contract(query, queries)
            contract["latency_ms"] = int((time.monotonic() - started) * 1000)
            contract["enabled"] = True
            return contract
        except Exception as exc:
            logger.warning("KGContextExpander.expand_query_context failed: %s", exc)
            return self._empty(enabled=True, error=str(exc))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _empty(enabled: bool, error: str | None) -> dict[str, Any]:
        return {
            "enabled": enabled,
            "chunk_ids_input": 0,
            "matched_chunks": 0,
            "provisions": [],
            "domains": [],
            "latency_ms": 0,
            "error": error,
        }


def rrf_fuse_chunks(
    chunk_lists: Iterable[list[Any]],
    rrf_k: float = 60.0,
    top_k: int = 10,
    dedupe_kg: bool = True,
) -> list[Any]:
    """Reciprocal-Rank-Fuse several ranked chunk lists into one ranked list.

    Repair of candidate fusion (2026-08-12): KG evidence must participate in
    the ranking — interleaved by its retrieval rank — rather than being
    tail-appended after the vector top-k (which structurally hides it from
    Recall@K / MRR / nDCG).  Each input list contributes ``1/(rank+1+rrf_k)``
    to its items; items present in several lists accumulate (agreement
    boost).  Chunk scores are overwritten with the fused RRF score so
    downstream sort-by-score consumers (ContextBuilder) rank correctly.

    Args:
        chunk_lists: Iterable of ranked ``RetrievedChunk`` lists.
        rrf_k: RRF constant (default 60 — matches HybridRetriever).
        top_k: Maximum fused results to return.
        dedupe_kg: When true (default), a ``document_type == "KG-Provision"``
            chunk is dropped BEFORE scoring when a non-KG chunk already
            covers the same (act, section) — a redundant KG provision must
            not occupy a fused top-k slot that a novel candidate could fill.
            The act match is a light normalisation (lowercase, leading
            ``the`` stripped, whitespace collapsed); a miss only leaves a
            redundant slot, never drops a novel provision.

    Returns:
        Ranked list of the input chunk objects, deduplicated by chunk_id.

    Tie policy (deterministic): when two chunk ids earn equal RRF scores,
    the tie breaks by first-appearance order across the input lists — i.e.
    the order in which the lists were passed (vector lists first, KG last),
    preserved by Python's stable sort.  This keeps the fusion reproducible
    from the same cached candidate lists and never lets a KG item displace
    an equally-ranked vector item.
    """
    if dedupe_kg:
        chunk_lists = _dedupe_kg_over_chunks(list(chunk_lists))
    from app.rag.retrieval.rrf import reciprocal_rank_fuse

    scores = reciprocal_rank_fuse(chunk_lists, rrf_k=rrf_k)
    best: dict[str, Any] = {}
    for ranked in chunk_lists:
        for chunk in ranked:
            cid = str(getattr(chunk, "chunk_id", ""))
            if not cid:
                continue
            if cid not in best:
                best[cid] = chunk
    # Stable descending sort by RRF score: ties keep first-appearance order
    # (see tie policy in the docstring) — deterministic across runs.
    ordered = sorted(scores, key=scores.get, reverse=True)  # type: ignore[arg-type]
    fused = [best[cid] for cid in ordered[:top_k]]
    for chunk in fused:
        chunk.score = scores[chunk.chunk_id]
    return fused


def _norm_act(title: Any) -> str:
    """Light act-title normalisation for the KG-redundancy check."""
    s = re.sub(r"\s+", " ", str(title or "").strip().lower())
    return re.sub(r"^(the|an|a)\s+", "", s)


def _dedupe_kg_over_chunks(chunk_lists: list[list[Any]]) -> list[list[Any]]:
    """Drop redundant KG-Provision chunks from each list, in place on copies.

    A KG chunk is redundant when a non-KG chunk already covers the same
    ``(act, section)`` — the same provision the graph re-surfaced.  Only
    ``document_type == "KG-Provision"`` chunks are considered (vector chunks
    and other types are untouched).  Returns new lists; the inputs are not
    mutated.
    """
    covered: set[tuple[str, str]] = set()
    for ranked in chunk_lists:
        for chunk in ranked:
            if str(getattr(chunk, "document_type", "")) == "KG-Provision":
                continue
            act = _norm_act(getattr(chunk, "document_title", ""))
            section = str(getattr(chunk, "section_number", "") or "").strip()
            if act and section:
                covered.add((act, section))
    if not covered:
        return chunk_lists
    out: list[list[Any]] = []
    for ranked in chunk_lists:
        kept = [
            chunk
            for chunk in ranked
            if not (
                str(getattr(chunk, "document_type", "")) == "KG-Provision"
                and (
                    _norm_act(getattr(chunk, "document_title", "")),
                    str(getattr(chunk, "section_number", "") or "").strip(),
                )
                in covered
            )
        ]
        out.append(kept)
    return out


def provisions_to_retrieved_chunks(
    provisions: Iterable[dict[str, Any]],
    limit: int = 5,
) -> list[Any]:
    """Convert KG expansion provisions into :class:`RetrievedChunk` objects.

    Lets the grounded-generation pipeline (and the evaluation harness) feed
    structured KG evidence into the standard prompt-context builder: each
    provision becomes a ``[Source n]`` block the LLM can cite, exactly like
    a retrieved chunk.  The chunk id is ``KG:<provision_id>`` so citations
    remain traceable; ``document_type`` is ``KG-Provision`` to distinguish
    graph-derived evidence from vector hits.

    Args:
        provisions: ``KGContextExpander.expand_chunks()`` provision dicts.
        limit: Maximum provisions converted (keeps the context budget sane).

    Returns:
        List of ``RetrievedChunk`` — empty when no provisions are given.
    """
    from app.rag.retrieval.result import RetrievedChunk

    chunks: list[Any] = []
    for i, p in enumerate(provisions):
        if i >= limit:
            break
        number = str(p.get("provision_number") or "").strip()
        title = str(p.get("title") or "").strip()
        text = str(p.get("text") or "").strip()
        instrument = str(p.get("instrument_title") or "").strip()
        status = str(p.get("status") or "").strip()
        domain = str(p.get("legal_domain") or "").strip()
        authorities = [a for a in (p.get("authorities") or []) if a]
        auth = ", ".join(authorities) or str(p.get("issuing_authority") or "").strip()

        parts = [f"PROVISION {number} — {title}".strip()]
        if instrument:
            parts.append(f"Instrument: {instrument}")
        if status:
            parts.append(f"Status: {status}")
        if domain:
            parts.append(f"Domain: {domain}")
        if auth:
            parts.append(f"Authority: {auth}")
        if text:
            parts.append(text)

        chunks.append(
            RetrievedChunk(
                chunk_id=f"KG:{p.get('provision_id') or i}",
                # Provably below every real retrieval score (dense similarity
                # is >= 0) so KG evidence always ranks after retrieved chunks
                # in the context builder's [Source n] ordering.
                score=-1.0 - i * 0.01,
                text="\n".join(parts),
                section_number=number or None,
                document_title=instrument or title,
                document_type="KG-Provision",
                authority=auth,
            )
        )
    return chunks


def _unwrap(value: Any) -> Any:
    """Coerce Neo4j driver values (Node/Date) to plain Python primitives."""
    if value is None:
        return None
    if hasattr(value, "to_native"):
        return value.to_native()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


# End of hybrid.py
