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

#: Property/alias names under which a provision body may arrive.
#:
#: The producers do not agree on one key: ``KGContextExpander`` projects
#: ``p.provision_text`` as ``text``, ``search_provisions`` calls the same
#: property ``snippet``, and ``get_cross_domain_laws`` returns the
#: ``SUPPORTED_BY`` chunk body as ``chunk_text``.  Reading only ``text``
#: therefore left the fusion path — which runs through
#: ``provisions_for_query`` — with **no** body at all: every injected
#: ``[Source n]`` block carried just the header line (provision number,
#: instrument, status, domain, authority), which is the 85-character
#: pointer stub that dropped citation recall from 0.31 to 0.10.
#:
#: Order is by authority, not by convenience: the provision's own text
#: beats a single supporting chunk, which beats the header-derived title.
_PROVISION_TEXT_KEYS: tuple[str, ...] = ("text", "provision_text", "snippet", "chunk_text")

#: A body shorter than this is a pointer stub, not provision text — the
#: header line alone ("PROVISION 16 — Duties of the Authority" plus the
#: Instrument/Status/Domain/Authority lines) is already ~85 characters, so
#: anything at or below that carries no statutory content to ground a
#: citation against.
_MIN_PROVISION_BODY_CHARS = 85


def provision_body_text(provision: dict[str, Any]) -> str:
    """Resolve the actual statutory body of *provision* from any known alias.

    Every KG query path names the body property differently (see
    :data:`_PROVISION_TEXT_KEYS`), so a single-key read silently dropped the
    text for the fusion path and left KG evidence as header-only stubs.
    Resolving the alias chain here fixes every producer at once.

    Args:
        provision: A provision dict from any ``kg`` query path.

    Returns:
        The longest non-stub body found, or ``""`` when the provision only
        has a header/pointer stub (so callers can say so rather than emit a
        misleading empty-looking block).
    """
    best = ""
    for key in _PROVISION_TEXT_KEYS:
        value = provision.get(key)
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        # Longest wins: the full provision text and one arbitrary
        # ``SUPPORTED_BY`` chunk can both be present, and the fuller one is
        # strictly better evidence for grounding a citation.
        if len(candidate) > len(best):
            best = candidate
    return best if len(best) > _MIN_PROVISION_BODY_CHARS else ""


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
            os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_USERNAME") and os.environ.get("NEO4J_PASSWORD"),
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
                    coalesce(p.provision_text, '') AS text,
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
                        "source_chunk_ids": [],
                    },
                )
                for source_cid in (
                    _unwrap(r.get("chunk_node_id")),
                    _unwrap(r.get("qdrant_point_id")),
                ):
                    if source_cid and str(source_cid) not in entry["source_chunk_ids"]:
                        entry["source_chunk_ids"].append(str(source_cid))
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
        except (ConnectionError, RuntimeError) as exc:
            logger.warning("KGContextExpander failed: %s (%s)", exc, type(exc).__name__)
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
        except (ConnectionError, RuntimeError) as exc:
            logger.warning("KGContextExpander.expand_query_context failed: %s (%s)", exc, type(exc).__name__)
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


def is_kg_chunk(chunk: Any) -> bool:
    """True when *chunk* is graph-derived evidence, not a vector hit."""
    if str(getattr(chunk, "document_type", "")) == "KG-Provision":
        return True
    return str(getattr(chunk, "chunk_id", "")).startswith("KG:")


def rrf_fuse_chunks(
    chunk_lists: Iterable[list[Any]],
    rrf_k: float = 60.0,
    top_k: int = 10,
    dedupe_kg: bool = True,
    reserve_kg_slots: int = 0,
    preserve_vector_top_k: bool = True,
) -> list[Any]:
    """Reciprocal-Rank-Fuse several ranked chunk lists into one ranked list.

    Candidate fusion has two jobs that pull in opposite directions:

    * KG evidence must be able to reach the prompt at all.  Tail-appending it
      after every vector chunk structurally hides it from Recall@K / MRR /
      nDCG and from the prompt window.
    * A short KG list must not evict vector evidence merely because RRF scores
      rank position.  A KG item at rank 1 contributes ``1/(1+1+rrf_k)`` while
      a vector item at rank 500 contributes ``1/(500+1+rrf_k)``; with a ~500
      chunk vector pool and a ~20 chunk prompt budget, unbounded competition
      is not a fair merge.

    ``preserve_vector_top_k`` resolves that tension with a non-lossy invariant
    (the same rule :class:`HybridRetriever` uses for newly added arms): the
    first input list is the base vector ranking, and every item in its own
    top-k is retained.  KG evidence fills only the slots the base ranking does
    not need.  When the vector pool is wider than the prompt budget, no KG
    item can displace a vector candidate that would otherwise have survived
    the truncation.  When the vector pool is narrower (the production default:
    ``top_k=10`` retrieval into a 20-slot prompt), the spare slots are filled
    with KG evidence in RRF order.

    Scores in this mode are reassigned by final position, not raw RRF: a
    downstream ``ContextBuilder`` sorts by ``chunk.score``, and leaving a KG
    item's rank-1 RRF score intact would let it jump back above the protected
    vector head after the merge.

    Args:
        chunk_lists: Iterable of ranked ``RetrievedChunk`` lists.  The first
            list is the base vector ranking when ``preserve_vector_top_k`` is
            on and the last list is graph-derived.
        rrf_k: RRF constant (default 60 — matches HybridRetriever).
        top_k: Maximum fused results to return.
        dedupe_kg: When true (default), a ``document_type == "KG-Provision"``
            chunk is dropped BEFORE scoring when a non-KG chunk already
            covers the same (act, section) — a redundant KG provision must
            not occupy a fused top-k slot that a novel candidate could fill.
            The act match is a light normalisation (lowercase, leading
            ``the`` stripped, whitespace collapsed); a miss only leaves a
            redundant slot, never drops a novel provision.
        reserve_kg_slots: Legacy bounded-footprint mode.  When > 0, this many
            tail slots are filled from the last (KG) list and the remaining
            ``top_k - reserve_kg_slots`` from the other lists.  It is kept for
            reproducing the 2026-08 reserve sweep; production uses
            ``preserve_vector_top_k`` instead.  Defaults to 0.
        preserve_vector_top_k: When true (default) and the last list is
            graph-derived, retain the first list's own top-k and fill only
            unused slots from the fused ranking.  Set false to reproduce the
            original unbounded interleave for offline experiments.

    Returns:
        Ranked list of the input chunk objects, deduplicated by chunk_id.

    Tie policy (deterministic): when two chunk ids earn equal RRF scores,
    the tie breaks by first-appearance order across the input lists — i.e.
    the order in which the lists were passed (vector lists first, KG last),
    preserved by Python's stable sort.  This keeps the fusion reproducible
    from the same cached candidate lists and never lets a KG item displace
    an equally-ranked vector item.

    """
    lists = list(chunk_lists)
    if dedupe_kg:
        lists = _dedupe_kg_over_chunks(lists)
    from app.rag.retrieval.rrf import reciprocal_rank_fuse

    scores = reciprocal_rank_fuse(lists, rrf_k=rrf_k)
    best: dict[str, Any] = {}
    for ranked in lists:
        for chunk in ranked:
            cid = str(getattr(chunk, "chunk_id", ""))
            if not cid:
                continue
            if cid not in best:
                best[cid] = chunk
    # Stable descending sort by RRF score: ties keep first-appearance order
    # (see tie policy in the docstring) — deterministic across runs.
    ordered = sorted(scores, key=scores.get, reverse=True)  # type: ignore[arg-type]

    fair = (
        preserve_vector_top_k
        and reserve_kg_slots <= 0
        and len(lists) > 1
        and bool(lists[0])
        and any(is_kg_chunk(chunk) for chunk in lists[-1])
    )
    if fair:
        # The base vector list's own top-k is the protected set.  Preserve its
        # order (it is already relevance-ranked) rather than letting KG
        # agreement boosts reorder it.
        protected: list[str] = []
        seen: set[str] = set()
        for chunk in lists[0]:
            cid = str(getattr(chunk, "chunk_id", ""))
            if cid and cid not in seen and cid in scores:
                seen.add(cid)
                protected.append(cid)
            if len(protected) >= top_k:
                break
        protected_set = set(protected)
        remaining = [cid for cid in ordered if cid not in protected_set]
        ordered = protected + remaining[: max(top_k - len(protected), 0)]
        # ContextBuilder sorts by score, so make the final order authoritative.
        scores = {
            cid: 1.0 / (rank + 1 + rrf_k)
            for rank, cid in enumerate(ordered)
        }
    elif reserve_kg_slots > 0 and len(lists) > 1:
        # Legacy reserve sweep: bound KG's footprint at ``reserve_kg_slots``
        # instead of letting it scale with the vector pool width.  KG items
        # keep their fused order among themselves; vector items fill the head.
        kg_ids = [str(getattr(c, "chunk_id", "")) for c in lists[-1]]
        kg_ids = [cid for cid in kg_ids if cid]
        kg_ordered = [cid for cid in ordered if cid in set(kg_ids)]
        head_ordered = [cid for cid in ordered if cid not in set(kg_ordered)]
        n_head = max(top_k - reserve_kg_slots, 0)
        head = head_ordered[:n_head]
        kg = kg_ordered[: max(top_k - len(head), 0)]
        ordered = head + kg

    fused = [best[cid] for cid in ordered[:top_k]]
    for chunk in fused:
        chunk.score = scores[chunk.chunk_id]
    return fused


def _norm_act(title: Any) -> str:
    """Light act-title normalisation shared by backfill and KG dedupe."""
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
    covered_keys: set[tuple[str, str]] = set()
    covered_ids: set[str] = set()
    for ranked in chunk_lists:
        for chunk in ranked:
            if str(getattr(chunk, "document_type", "")) == "KG-Provision":
                continue
            cid = str(getattr(chunk, "chunk_id", "") or "")
            if cid:
                covered_ids.add(cid)
            act = str(
                getattr(chunk, "act_name", "")
                or getattr(chunk, "document_title", "")
                or ""
            )
            section = str(
                getattr(chunk, "section_number", "")
                or getattr(chunk, "clause_number", "")
                or ""
            ).strip()
            if act and section:
                covered_keys.update(backfill_key_variants(act, section))
    if not covered_keys and not covered_ids:
        return chunk_lists

    def _is_covered(chunk: Any) -> bool:
        if str(getattr(chunk, "document_type", "")) != "KG-Provision":
            return False
        source_ids = {
            str(cid)
            for cid in (getattr(chunk, "source_chunk_ids", None) or [])
            if cid
        }
        if source_ids & covered_ids:
            return True
        act = str(
            getattr(chunk, "act_name", "")
            or getattr(chunk, "document_title", "")
            or ""
        )
        section = str(
            getattr(chunk, "section_number", "")
            or getattr(chunk, "clause_number", "")
            or ""
        ).strip()
        if not act or not section:
            return False
        # ``backfill_key_variants`` normalises both sides: the graph names
        # sections ``fssai:s57`` while the corpus stamps ``57``, and the old
        # raw-string comparison silently missed that pair.
        return bool(set(backfill_key_variants(act, section)) & covered_keys)

    out: list[list[Any]] = []
    for ranked in chunk_lists:
        out.append([chunk for chunk in ranked if not _is_covered(chunk)])
    return out


def provisions_to_retrieved_chunks(
    provisions: Iterable[dict[str, Any]],
    limit: int = 5,
    backfill: dict[tuple[str, str], str] | None = None,
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
        backfill: Optional ``{(norm_act, norm_section): body_text}`` corpus
            index (see :func:`backfill_key`). A provision whose graph body
            resolves to a stub (or empty) takes its body from here instead of
            emitting a header-only block. Default ``None`` preserves existing
            behaviour exactly — callers that do not pass a corpus see
            byte-identical output.

    Returns:
        List of ``RetrievedChunk`` — empty when no provisions are given.

    The body is resolved through :func:`provision_body_text`, which reads
    the alias chain in :data:`_PROVISION_TEXT_KEYS`.  A single-key read of
    ``text`` is what produced the header-only 85-character stubs that
    dropped citation recall from 0.31 to 0.10: the fusion path runs via
    :func:`kg.queries.provisions_for_query`, whose producers name the body
    ``snippet`` (``search_provisions``) or ``chunk_text``
    (``get_cross_domain_laws``) — never ``text``.

    A provision whose resolved body is a stub is still emitted (it carries
    real identity — number, instrument, status, domain, authority), but the
    block records that no statutory text was resolved so the missing
    ``provision_text`` is visible in the prompt/audit rather than being
    mistaken for an empty-evidence verdict — unless *backfill* supplies the
    text, in which case the block carries real substance (2026-10-12: live
    measurement showed fused stubs displacing vector evidence, e.g. Q014).
    """
    from app.rag.retrieval.result import RetrievedChunk

    chunks: list[Any] = []
    for i, p in enumerate(provisions):
        if i >= limit:
            break
        number = str(p.get("provision_number") or "").strip()
        title = str(p.get("title") or "").strip()
        body = provision_body_text(p)
        instrument = str(p.get("instrument_title") or "").strip()
        status = str(p.get("status") or "").strip()
        domain = str(p.get("legal_domain") or p.get("domain") or "").strip()
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
        backfilled_body = None
        if backfill is not None:
            for _key in backfill_key_variants(
                instrument, number,
                str(p.get("provision_id") or ""),
            ):
                if _key in backfill:
                    backfilled_body = backfill[_key]
                    break
        # Prefer the fuller body: a header-only graph text must not beat a
        # backfilled statutory body just because it arrived in the first alias.
        resolved_body = max((body or "", backfilled_body or ""), key=len).strip()
        if resolved_body:
            parts.append(resolved_body)
        else:
            parts.append("[No statutory text available for this provision.]")

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
                source_chunk_ids=[
                    str(cid)
                    for cid in (p.get("source_chunk_ids") or [])
                    if cid
                ],
                body_char_count=len(resolved_body),
            ),
        )
    return chunks


def filter_injectable_kg_chunks(
    chunks: Iterable[Any],
    min_body_chars: int = 0,
) -> list[Any]:
    """Keep only KG chunks with enough statutory body to be useful evidence.

    Pointer-only KG provisions still carry identity (number, instrument,
    status) but no quotable text.  Fusing them spends prompt slots that vector
    evidence could use, which is the measured failure mode.  ``0`` preserves
    the historical behaviour (emit everything); the production fusion path
    passes ``cfg.kg_min_body_chars``.
    """
    if min_body_chars <= 0:
        return list(chunks)
    return [
        chunk
        for chunk in chunks
        if int(getattr(chunk, "body_char_count", 0) or 0) >= min_body_chars
    ]


def _norm_text(value: Any) -> str:
    """Lowercase alphanumeric collapse for cross-source key matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def backfill_key(instrument: str, number: str, provision_id: str = "") -> tuple[str, str]:
    """Normalised ``(act, section)`` key joining a KG provision to corpus text.

    ``provision_number`` arrives as ``fssai:s57`` (family:section) while the
    corpus stamps ``section_number`` as ``57`` and ``act_name`` with different
    punctuation (``... Act 2006`` vs ``... Act, 2006``). Both sides collapse to
    alphanumeric-lowercase (``_norm_text``); use :func:`backfill_key_variants`
    for lookup so the ``s``-prefixed graph form meets the bare corpus form.
    The ``provision_id`` argument is accepted for future exact-first matching
    and currently unused.
    """
    sec = str(number or "").split(":")[-1].strip().lower()
    # ``_norm_act`` strips a leading article ("The Food Safety..." ->
    # "Food Safety...") before punctuation collapse, so corpus and graph
    # titles match whether or not the drafter included the article.
    return (_norm_text(_norm_act(instrument)), _norm_text(sec))


def backfill_key_variants(instrument: str, number: str,
                          provision_id: str = "") -> list[tuple[str, str]]:
    """Lookup order for :func:`backfill_key`: exact normalised form first,
    then the leading-alpha-stripped form (``s57`` -> ``57``).

    The graph names sections ``fssai:s57`` where the corpus stamps ``57``;
    without the fallback no stub ever matches and the backfill silently covers
    nothing (caught 2026-10-12: 0/5 stubs filled on Q014/Q016 despite a 2176-
    entry index). Exact-first so ``57A`` never collides with ``57``.
    """
    act, sec = backfill_key(instrument, number, provision_id)
    bare = re.sub(r"^[^0-9]+", "", sec)
    variants = [(act, sec)]
    if bare and bare != sec:
        variants.append((act, bare))
    return variants


def _unwrap(value: Any) -> Any:
    """Coerce Neo4j driver values (Node/Date) to plain Python primitives."""
    if value is None:
        return None
    if hasattr(value, "to_native"):
        return value.to_native()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (ConnectionError, RuntimeError):
            pass
    return value


# End of hybrid.py
