"""Parent-standard reconstruction — build the evidence bundle (task spec §5, §12).

The corpus stores commodity-standard tables as row fragments:
``"(iii) Moisture Not more than 10.0 percent by weight …"`` — the commodity
name lives in a *sibling* chunk (the clause heading
``"2.9.8: Cumin (Zeera, Kalonji) 1. Cumin … whole means …"``).  When a row is
retrieved, the answer generator must know **which standard it belongs to**.

This module reconstructs that context from information already on the
retrieved chunks (``document_id`` + ``clause_number`` + ``chunk_index``):

1. **Sibling grouping** — chunks sharing ``(document_id, clause_number)``
   belong to the same standard clause; the row with the clause *heading*
   (leading dotted clause number + commodity name) is the parent.
2. **Parent assembly** — the parent context text is the heading chunk's text
   plus, when the heading chunk is not in the pool, a synthetic context line
   built ONLY from verified payload fields (clause number, document title,
   detected commodity) — never invented values.
3. **Evidence bundle** (task spec §12)::

       {
         "entity": "cumin",
         "intent": "food_standard",
         "primary_evidence": [...],     # chunks satisfying the intent
         "parent_context": [...],       # reconstructed standard context
         "related_evidence": [...],     # other entity-matching chunks
         "legal_source": {...},         # act/regulation/section/source
         "completeness": {...}          # §16 completeness flags
       }

No Qdrant round-trip is required: sibling metadata arrives on the retrieved
pool itself.  A (future) store-side fetcher can be injected via
``sibling_fetch`` for pools that truncate the clause — kept optional so the
offline benchmark is reproducible.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Callable

from app.rag.retrieval.food_query_understanding import FoodQueryUnderstanding
from app.rag.retrieval.provision_metadata import (
    derive_provision_metadata_cached,
    is_definition_chunk,
    is_standard_chunk,
)

logger = logging.getLogger(__name__)

__all__ = ["EvidenceBundle", "reconstruct_evidence_bundle", "group_by_clause"]


_CLAUSE_LEAD_RE = re.compile(r"^\s*(\d{1,2}\.\d{1,3}(?:\.\d{1,3})?)\s*[:.]?\s*")

#: Fields copied into ``legal_source`` (first non-unknown wins, priority order).
_SOURCE_FIELDS = ("act", "regulation", "section", "source")

#: Clause-sibling commodity registry — filled by :func:`group_by_clause` from
#: heading chunks, read by the reranker's entity feature so a table-row
#: fragment under clause "2.9.8" counts as a cumin match.  Key:
#: ``(document_id, clause_number)`` → commodity.  Populated per pipeline run
#: (the pool is the evidence; nothing persists across requests).
_CLAUSE_COMMODITY: dict[tuple[str, str], str] = {}


def _f(chunk: Any, name: str, default: Any = None) -> Any:
    if isinstance(chunk, dict):
        return chunk.get(name, default)
    return getattr(chunk, name, default)


def _cid(chunk: Any) -> str:
    return str(_f(chunk, "chunk_id", "") or "")


def _ctext(chunk: Any) -> str:
    return str(_f(chunk, "text", "") or "")


def group_by_clause(chunks: list[Any]) -> dict[tuple[str, str], list[Any]]:
    """Group chunks by ``(document_id, clause_number)``.

    Chunks missing both keys get a singleton group keyed by their own id, so
    nothing is silently dropped.  Also publishes the clause→commodity map
    (heading chunks are authoritative) consumed by the reranker's entity
    feature — the pool is the only source, so nothing is fabricated.
    """
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for c in chunks:
        doc = str(_f(c, "document_id", "") or "")
        clause = str(_f(c, "clause_number", "") or _f(c, "section_number", "") or "")
        if doc and clause:
            groups[(doc, clause)].append(c)
        else:
            groups[(_cid(c), "")].append(c)

    # Publish clause→commodity from each group's best heading/heading-ish
    # chunk (first chunk that yields a non-unknown commodity).
    _CLAUSE_COMMODITY.clear()
    for key, group in groups.items():
        if not key[1]:
            continue
        for c in sorted(group, key=_heading_sort_key):
            meta = derive_provision_metadata_cached(c)
            commodity = str(meta.get("commodity", "unknown") or "unknown")
            if commodity != "unknown":
                _CLAUSE_COMMODITY[key] = commodity.lower()
                break
    return dict(groups)


def clause_commodity_for(chunk: Any) -> str | None:
    """Commodity registered for this chunk's clause by the last grouping pass.

    Returns ``None`` when the chunk was never grouped (no fabrication): the
    caller then falls back to text-only entity matching.
    """
    doc = str(_f(chunk, "document_id", "") or "")
    clause = str(_f(chunk, "clause_number", "") or _f(chunk, "section_number", "") or "")
    if not doc or not clause:
        return None
    return _CLAUSE_COMMODITY.get((doc, clause))


def _is_heading_chunk(chunk: Any) -> bool:
    """Whether this chunk *leads* its clause (carries the clause heading)."""
    return bool(_CLAUSE_LEAD_RE.match(_ctext(chunk)))


def _heading_sort_key(chunk: Any) -> int:
    """Order chunks so heading chunks come first, then by chunk_index."""
    idx = _f(chunk, "chunk_index", 0)
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = 10**9
    return (0 if _is_heading_chunk(chunk) else 1, idx)


def _heading_chunk(group: list[Any]) -> Any | None:
    """The clause-heading chunk of a group, if present in the pool."""
    for c in sorted(group, key=_heading_sort_key):
        if _is_heading_chunk(c):
            return c
    return None


def _synthetic_parent_context(group: list[Any], fq: FoodQueryUnderstanding) -> str | None:
    """Context line built ONLY from verified payload fields.

    Used when the heading chunk is not in the retrieved pool: the generator
    still learns "this row belongs to clause 2.9.8 of the Food Additives
    Regulations" without inventing the standard's wording.
    """
    meta = derive_provision_metadata_cached(group[0]) if group else {}
    clause = meta.get("section", "unknown")
    regulation = meta.get("regulation", "unknown")
    commodity = meta.get("commodity", "unknown")
    if clause == "unknown" and regulation == "unknown":
        return None
    parts = [f"Clause {clause}" if clause != "unknown" else "Clause unknown"]
    parts.append(f"of {regulation}" if regulation != "unknown" else "(regulation unknown)")
    if commodity != "unknown":
        parts.append(f"— {commodity}")
    return "Reconstructed context: " + " ".join(parts)


def _detect_commodity_in_group(group: list[Any]) -> str | None:
    for c in sorted(group, key=_heading_sort_key):
        meta = derive_provision_metadata_cached(c)
        commodity = meta.get("commodity")
        if commodity and commodity != "unknown":
            return str(commodity)
    return None


def reconstruct_evidence_bundle(
    query: str,
    ranked_chunks: list[Any],
    food: FoodQueryUnderstanding | None = None,
    max_primary: int = 5,
    max_related: int = 5,
) -> dict[str, Any]:
    """Build the §12 evidence bundle from a ranked candidate pool.

    The pool is grouped by legal clause; the group containing the
    best-scoring intent-satisfying chunk contributes the parent context
    (heading chunk text or the synthetic verified-fields context).  The
    bundle drives generation — the generator sees clause context, not
    orphan rows.
    """
    fq = food or FoodQueryUnderstanding.from_query(query)
    if not ranked_chunks:
        return _empty_bundle(fq)

    groups = group_by_clause(ranked_chunks)
    # Rank groups by their best chunk score (pool order is ranked already).
    def _group_rank(item: tuple[tuple[str, str], list[Any]]) -> float:
        return max((float(_f(c, "score", 0.0) or 0.0) for c in item[1]), default=0.0)

    ordered_groups = sorted(groups.items(), key=lambda kv: -_group_rank(kv))

    primary: list[Any] = []
    parent_context: list[Any] = []
    related: list[Any] = []
    chosen_group: list[Any] | None = None

    for _key, group in ordered_groups:
        # Does this group carry intent-satisfying evidence?
        if fq.wants_standard:
            satisfiers = [c for c in group if is_standard_chunk(c)]
        elif fq.intent == "definition":
            satisfiers = [c for c in group if is_definition_chunk(c)]
        else:
            satisfiers = list(group)
        if not satisfiers:
            # definition-ask fallback: the heading chunk still informs
            if fq.intent == "definition":
                satisfiers = [_heading_chunk(group)] if _heading_chunk(group) else []
            if not satisfiers:
                related.extend(group[:max_related])
                continue
        chosen_group = group
        head = _heading_chunk(group)
        if head is not None:
            parent_context.append(head)
        else:
            synth = _synthetic_parent_context(group, fq)
            if synth:
                parent_context.append(synth)  # type: ignore[arg-type]
        primary.extend(satisfiers[:max_primary])
        break

    # Related: everything else matching the entity, best-scored first.
    entity = fq.entity
    for _key, group in ordered_groups:
        if group is chosen_group:
            continue
        if entity:
            entity_hit = any(
                entity.lower() in _ctext(c).lower()
                or entity.lower() in str(_f(c, "document_title", "") or "").lower()
                for c in group
            )
            if not entity_hit:
                continue
        related.extend(group[:2])
        if len(related) >= max_related:
            break

    bundle: dict[str, Any] = {
        "entity": fq.entity or "unknown",
        "intent": fq.intent,
        "primary_evidence": primary,
        "parent_context": parent_context,
        "related_evidence": related[:max_related],
        "legal_source": _legal_source(primary, parent_context),
        "completeness": _completeness(fq, primary, parent_context),
    }
    return bundle


def _legal_source(primary: list[Any], parent_context: list[Any]) -> dict[str, Any]:
    """Legal location of the evidence — first non-unknown value per field,
    preferring parent-context (heading) chunks which carry the clause."""
    source: dict[str, Any] = {}
    for field in _SOURCE_FIELDS:
        for chunk in [*parent_context, *primary]:
            if isinstance(chunk, str):
                continue
            meta = derive_provision_metadata_cached(chunk)
            val = meta.get(field)
            if val and val != "unknown":
                source[field] = val
                break
            source.setdefault(field, "unknown")
    return source


def _completeness(fq: FoodQueryUnderstanding, primary: list[Any], parent_context: list[Any]) -> dict[str, Any]:
    """§16 evidence-completeness flags (pure, no fabrication)."""
    # Entity presence: the entity may appear verbatim in the evidence text OR
    # be resolvable through the clause→commodity map (a heading chunk in the
    # same clause group named the commodity) — a row fragment then belongs to
    # the requested commodity without repeating its name.
    from app.rag.retrieval.parent_reconstruction import clause_commodity_for  # local import avoids cycle at load

    def _entity_in(c: Any) -> bool:
        if not fq.entity:
            return False
        if fq.entity.lower() in _ctext(c).lower():
            return True
        commodity = clause_commodity_for(c)
        return bool(commodity) and (
            commodity == fq.entity.lower() or fq.entity.lower() in commodity
        )

    entity_found = bool(primary) and any(_entity_in(c) for c in primary)
    if fq.wants_standard:
        standard_found = any(is_standard_chunk(c) for c in primary)
    elif fq.intent == "definition":
        standard_found = any(is_definition_chunk(c) for c in primary)
    else:
        standard_found = bool(primary)
    parameter_complete = True
    if fq.parameters:
        joined = " ".join(_ctext(c).lower() for c in primary)
        parameter_complete = all(p.lower() in joined for p in fq.parameters)
    return {
        "entity_found": bool(entity_found),
        "intent_satisfied": bool(standard_found),
        "standard_found": bool(standard_found) if fq.wants_standard else bool(primary),
        "source_found": any(
            str(v) != "unknown" for k, v in _legal_source(primary, parent_context).items()
        ),
        "parameter_complete": bool(parameter_complete),
    }


def _empty_bundle(fq: FoodQueryUnderstanding) -> dict[str, Any]:
    return {
        "entity": fq.entity or "unknown",
        "intent": fq.intent,
        "primary_evidence": [],
        "parent_context": [],
        "related_evidence": [],
        "legal_source": {f: "unknown" for f in _SOURCE_FIELDS},
        "completeness": {
            "entity_found": False,
            "intent_satisfied": False,
            "standard_found": False,
            "source_found": False,
            "parameter_complete": not fq.parameters,
        },
    }
