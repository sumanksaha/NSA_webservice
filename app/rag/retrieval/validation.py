"""Retrieval validation + fallback retrieval (task spec §10, §16, §11).

After retrieval/reranking, validate whether the retrieved evidence is
*capable of answering the query*:

* ``food_standard`` → the evidence must contain an actual standard/provision
  (requirement language or measurement rows) or a clearly linked
  parent/child standard context.
* ``parameter_specific_standard`` → the named parameter must appear together
  with a measurement/limit.
* ``definition`` → a definition-shaped chunk must be present.

When validation fails: **do not generate** — build fallback retrieval
queries instead (entity + standard / entity + requirement / entity + "shall
conform" / entity + limits / entity + provision) and, when Neo4j is
configured, a KG relationship arm (entity → HAS_STANDARD → provision).

No fabrication: fallback queries are composed only from parsed query values
(entity/parameters) and fixed regulatory vocabulary.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.rag.retrieval.food_query_understanding import FoodQueryUnderstanding
from app.rag.retrieval.parent_reconstruction import _completeness, clause_commodity_for  # reuse flags
from app.rag.retrieval.provision_metadata import commodity_agrees, commodity_phrase_match
from app.rag.retrieval.provision_metadata import is_definition_chunk, is_standard_chunk

logger = logging.getLogger(__name__)

__all__ = ["ValidationReport", "validate_retrieval", "fallback_queries", "kg_fallback_queries"]


class ValidationReport(dict):
    """Validation verdict + diagnostics (JSON-safe dict)."""


_MEASUREMENT_RE = re.compile(
    r"\b(?:not\s+more\s+than|not\s+less\s+than|maximum|minimum|per\s+cent|percent|mg/kg|ppm)\b",
    re.IGNORECASE,
)


def _entity_in_evidence(ranked_chunks: list[Any], fq: FoodQueryUnderstanding) -> bool:
    """Whether the evidence actually concerns the requested entity.

    Two legitimate routes: the entity named verbatim in a chunk's text, or
    the clause→commodity map (a heading chunk in the pool named the clause's
    commodity, so its row fragments inherit it).  Pool-wide standard language
    about *other* commodities does not count.
    """
    if not fq.entity:
        return True
    return _entity_evidence_state(ranked_chunks, fq) == "match"


def _entity_evidence_state(ranked_chunks: list[Any], fq: FoodQueryUnderstanding) -> str:
    """Entity-evidence state: ``match`` / ``conflict`` / ``absent``.

    ``conflict``  — clauses in the pool are identified (via the clause map)
                    and *none* of them is the requested entity: positive
                    evidence the pool concerns other commodities.
    ``absent``    — no entity match AND no identified clause: unknown, not
                    a conflict (a rows-only pool cannot name its commodity).
    """
    entity = (fq.entity or "").lower()
    if not entity:
        return "match"
    has_known_clause = False
    for c in ranked_chunks:
        if commodity_phrase_match(_ctext(c), entity):
            return "match"
        commodity = (clause_commodity_for(c) or "").lower()
        if commodity:
            has_known_clause = True
            if commodity_phrase_match(commodity, entity) or commodity_agrees(commodity, entity):
                return "match"
    return "conflict" if has_known_clause else "absent"


def _parameter_satisfied(ranked_chunks: list[Any], fq: FoodQueryUnderstanding) -> bool:
    """Whether the evidence contains a *limit row for the named parameter on
    the requested entity*.

    Pool-wide "moisture" mentions are not enough — sibling commodities' rows
    mention moisture too (the measured S4 failure: validation passed with
    generic moisture rows while the entity's own row was absent).  The
    parameter, a measurement, and the entity identity must co-occur on one
    chunk (identity via the chunk's own text or its clause's commodity).
    """
    params = [p.lower() for p in (fq.parameters or [])]
    if not params:
        return True
    if _entity_evidence_state(ranked_chunks, fq) == "conflict":
        return False
    entity = (fq.entity or "").lower()
    for c in ranked_chunks:
        text = _ctext(c).lower()
        commodity = (clause_commodity_for(c) or "").lower()
        if entity:
            entity_here = (
                commodity_phrase_match(text, entity)
                or (commodity and (commodity_phrase_match(commodity, entity) or commodity_agrees(commodity, entity)))
            )
            # Unknown identity (rows-only, no clause map): lenient — the row
            # may belong to the entity; a conflict would have returned above.
            if not entity_here and not commodity and not commodity_phrase_match(text, entity):
                pass  # unknown identity — do not exclude
            elif not entity_here:
                continue
        if _MEASUREMENT_RE.search(text) and any(p in text for p in params):
            return True
    return False


def _ctext(chunk: Any) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("text", "") or "")
    return str(getattr(chunk, "text", "") or "")


def validate_retrieval(
    query: str,
    ranked_chunks: list[Any],
    food: FoodQueryUnderstanding | None = None,
    bundle: dict[str, Any] | None = None,
) -> ValidationReport:
    """Can this evidence answer the query?  Deterministic verdict.

    Returns::

        {
          "valid": bool,
          "intent": ..., "entity": ...,
          "completeness": {...},      # §16 flags
          "reasons": [...],           # human-readable failures
        }
    """
    fq = food or FoodQueryUnderstanding.from_query(query)
    reasons: list[str] = []

    if bundle is not None:
        completeness = bundle.get("completeness") or {}
    else:
        completeness = _completeness(fq, list(ranked_chunks), [])

    valid = True
    if fq.wants_standard:
        if not any(is_standard_chunk(c) for c in ranked_chunks):
            valid = False
            reasons.append("no standard/requirement language in retrieved evidence")
            # A linked parent context can still satisfy (child rows + heading
            # elsewhere).  Reconstructed context counts only when the pool
            # itself carries measurement rows.
            if completeness.get("standard_found"):
                reasons.clear()
                valid = True
        if fq.parameters and not _parameter_satisfied(ranked_chunks, fq):
            valid = False
            reasons.append(f"named parameter limit not found in evidence: {fq.parameters}")
        # Entity gate: for a standard ask about entity E, a pool whose clauses
        # are identified as OTHER commodities does not answer the question
        # (positive conflict).  An unidentified pool (rows-only, clause map
        # never populated) is 'absent', not a conflict — no false invalidation.
        if fq.entity and _entity_evidence_state(ranked_chunks, fq) == "conflict":
            valid = False
            reasons.append(f"evidence concerns other commodities than the requested entity: {fq.entity}")
    elif fq.intent == "definition":
        if not any(is_definition_chunk(c) for c in ranked_chunks):
            valid = False
            reasons.append("no definition-shaped chunk in retrieved evidence")
    else:
        if not ranked_chunks:
            valid = False
            reasons.append("no evidence retrieved")

    return ValidationReport(
        valid=valid,
        intent=fq.intent,
        entity=fq.entity or "unknown",
        completeness=completeness,
        reasons=reasons,
    )


def fallback_queries(query: str, food: FoodQueryUnderstanding | None = None) -> list[str]:
    """Deterministic fallback retrieval queries (task spec §10).

    Ordered by expected precision; the caller runs them in order until
    validation passes (or budget is spent).
    """
    fq = food or FoodQueryUnderstanding.from_query(query)
    entity = fq.entity or ""
    if not entity:
        return []
    params = " ".join(fq.parameters[:1])
    queries: list[str] = []
    if fq.wants_standard:
        if params:
            queries.append(f"{entity} {params} not more than per cent by weight")
            queries.append(f"{entity} {params} maximum limit")
        queries.append(f"{entity} shall conform to the following standards")
        queries.append(f"{entity} standard requirements")
        queries.append(f"{entity} requirements moisture extraneous matter")
        queries.append(f"{entity} limits provision")
    elif fq.intent == "definition":
        queries.append(f"{entity} means")
        queries.append(f"definition of {entity}")
    elif fq.intent == "sampling":
        queries.append(f"{entity} sampling procedure")
        queries.append(f"sampling {entity} sealed divided")
    elif fq.intent == "licensing":
        queries.append(f"{entity} licence registration requirement")
    elif fq.intent == "prohibition":
        queries.append(f"{entity} prohibited shall not")
    elif fq.intent == "penalty":
        queries.append(f"{entity} penalty fine imprisonment")
    else:
        queries.append(f"{entity} requirements")
    return [q for q in queries if q.strip() and q.lower() != (query or "").lower()]


# ---------------------------------------------------------------------------
# KG relationship arm (task spec §11) — best-effort, never fabricates
# ---------------------------------------------------------------------------

#: Intent → graph relationship vocabulary.  The production KG models
#: provisions via APPLIES_TO / SUPPORTS edges to LegalProvision nodes; the
#: food-standard relationship names (HAS_STANDARD/HAS_LIMIT…) are *query
#: intent labels* mapped onto the existing traversal — when the graph has no
#: matching node, the arm returns empty and nothing is invented.
_KG_RELATION_BY_INTENT: dict[str, tuple[str, ...]] = {
    "food_standard": ("HAS_STANDARD", "APPLIES_TO"),
    "requirement": ("HAS_REQUIREMENT", "APPLIES_TO"),
    "parameter_specific_standard": ("HAS_LIMIT", "HAS_PARAMETER"),
    "compliance": ("HAS_STANDARD", "APPLIES_TO"),
    "prohibition": ("PROHIBITS", "APPLIES_TO"),
    "licensing": ("REQUIRES_LICENCE", "APPLIES_TO"),
    "sampling": ("HAS_PROCEDURE", "APPLIES_TO"),
    "definition": ("DEFINED_IN",),
}


def kg_fallback_queries(
    query: str,
    food: FoodQueryUnderstanding | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """KG relationship targets for the query (best-effort; [] when unavailable).

    Uses the production graph-RAG contract (``kg.queries.provisions_for_query``)
    restricted to the entity mention, so only *existing* graph relationships
    come back.  Any failure degrades to [] — never raises.
    """
    fq = food or FoodQueryUnderstanding.from_query(query)
    relations = _KG_RELATION_BY_INTENT.get(fq.intent, ("APPLIES_TO",))
    search_text = fq.entity or query
    try:
        from kg.queries import LegalKGQueries, provisions_for_query

        provisions = provisions_for_query(search_text, LegalKGQueries(), limit=limit)
    except Exception as exc:  # Neo4j unconfigured / unreachable — normal here
        logger.info("kg_fallback_queries: KG unavailable (%s)", type(exc).__name__)
        return []
    out: list[dict[str, Any]] = []
    for p in provisions:
        out.append(
            {
                "provision_id": p.get("provision_id"),
                "provision_number": p.get("provision_number"),
                "title": p.get("title") or "",
                "instrument_title": p.get("instrument_title") or "",
                "relationship": relations[0],
            }
        )
    return out
