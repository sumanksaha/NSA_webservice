"""Evidence-set construction — deterministic post-reranking evidence selector.

Given a ranked list of retrieved chunks (after CE reranking), selects a
compact set of 2–5 complementary provisions to provide to the LLM.  The selector
prioritizes legal authority, exact provision match, hierarchy proximity, and
relationship diversity over sheer CE similarity scores.

This is a **deterministic baseline** — no ML model is trained here.  Its
contribution can later be measured by comparing evidence-set recall with and
without the selector.

Feature flag: ``ENABLE_EVIDENCE_SELECTOR`` (default false, per spec).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.rag.retrieval.legal_hierarchy import (
    parse_section_chain,
    section_base,
)
from app.rag.retrieval.legal_identity import detect_provision_type, parse_legal_identity
from app.shared.config import cfg

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Evidence types
# --------------------------------------------------------------------------- #

EVIDENCE_PRIMARY = "primary_provision"
EVIDENCE_DEFINITION = "definition"
EVIDENCE_EXCEPTION = "exception"
EVIDENCE_PENALTY = "penalty_provision"
EVIDENCE_CROSS_REFERENCE = "cross_reference"
EVIDENCE_AUTHORITY = "authority"
EVIDENCE_SUBSECTION = "subsection"
EVIDENCE_ADJACENT = "adjacent_section"
EVIDENCE_DUPLICATE = "duplicate"

_EVIDENCE_TYPES = {
    EVIDENCE_PRIMARY,
    EVIDENCE_DEFINITION,
    EVIDENCE_EXCEPTION,
    EVIDENCE_PENALTY,
    EVIDENCE_CROSS_REFERENCE,
    EVIDENCE_AUTHORITY,
    EVIDENCE_SUBSECTION,
    EVIDENCE_ADJACENT,
    EVIDENCE_DUPLICATE,
}


@dataclass
class EvidenceItem:
    """A single evidence provision selected for the evidence set.

    Attributes:
        chunk: The original ``RetrievedChunk`` (or dict-like).
        evidence_type: Primary / definition / exception / penalty / etc.
        confidence: [0, 1] confidence in this item's relevance.
        redundancy: [0, 1] redundancy score (1.0 = duplicate of an existing item).
        complementarity: [0, 1] complementarity score (1.0 = adds new coverage).
        legal_identity: Canonical identity string for this chunk.
        section_number: Section number if available.
        act_name: Act name if available.
    """

    chunk: Any
    evidence_type: str = EVIDENCE_PRIMARY
    confidence: float = 1.0
    redundancy: float = 0.0
    complementarity: float = 1.0
    legal_identity: str = ""
    section_number: str | None = None
    act_name: str | None = None
    text_snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        chunk_id = getattr(self.chunk, "chunk_id", None)
        if chunk_id is None and isinstance(self.chunk, dict):
            chunk_id = self.chunk.get("chunk_id")
        return {
            "chunk_id": chunk_id,
            "evidence_type": self.evidence_type,
            "confidence": round(self.confidence, 4),
            "redundancy": round(self.redundancy, 4),
            "complementarity": round(self.complementarity, 4),
            "legal_identity": self.legal_identity,
            "section_number": self.section_number,
            "act_name": self.act_name,
            "text_snippet": self.text_snippet[:200],
        }


@dataclass
class EvidenceSet:
    """A selected set of evidence provisions.

    Attributes:
        query: The original query string.
        items: Selected evidence items (ordered by priority).
        total_pool: Number of chunks in the input pool.
        selection_rationale: Human-readable explanation of the selection.
    """

    query: str
    items: list[EvidenceItem] = field(default_factory=list)
    total_pool: int = 0
    selection_rationale: str = ""

    @property
    def chunk_ids(self) -> list[str | None]:
        return [getattr(item.chunk, "chunk_id", None) for item in self.items]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "items": [item.to_dict() for item in self.items],
            "total_pool": self.total_pool,
            "selection_rationale": self.selection_rationale,
        }


# --------------------------------------------------------------------------- #
# Evidence type detection
# --------------------------------------------------------------------------- #


def _detect_evidence_type(chunk: Any, query_section: str | None) -> str:
    """Classify what kind of evidence a chunk provides.

    Heuristic-based — no ML.  Uses the chunk's section text, authority field,
    and relationship to the query's target section.
    """
    text = getattr(chunk, "text", "") or ""
    if isinstance(chunk, dict):
        text = chunk.get("text", "") or ""
    text_lower = text.lower()

    section_number = getattr(chunk, "section_number", None)
    if section_number is None and isinstance(chunk, dict):
        section_number = chunk.get("section_number")

    # Is this the primary provision (matches query section)?
    if query_section and section_number and section_base(section_number) == query_section:
        return EVIDENCE_PRIMARY

    # Exception clauses — contain "except", "notwithstanding", "does not apply"
    if any(kw in text_lower for kw in ["except", "notwithstanding", "does not apply", "shall not"]):
        return EVIDENCE_EXCEPTION

    # Penalty provisions — contain "penalty", "fine", "imprisonment", "punishment"
    if any(kw in text_lower for kw in ["penalty", "fine", "imprisonment", "punishment", "imprison"]):
        return EVIDENCE_PENALTY

    # Definition provisions — contain "means", "includes", "definition"
    if any(kw in text_lower for kw in ["means ", "means,", '"means', "includes", "for the purposes"]):
        return EVIDENCE_DEFINITION

    # Authority provisions — contain "authority", "power", "may", "shall"
    if any(kw in text_lower for kw in ["authority", "power to", "may "]):
        return EVIDENCE_AUTHORITY

    # Cross-reference — contains "section" references to other sections
    if re_search_section_ref(text):
        return EVIDENCE_CROSS_REFERENCE

    # Subsection of the primary section
    if query_section and section_number:
        chain = parse_section_chain(section_number)
        if len(chain) > 1:
            return EVIDENCE_SUBSECTION

    # Adjacent section (same Act, +1/-1 section number)
    if query_section and section_number:
        base = section_base(section_number)
        try:
            if abs(int(base or 0) - int(query_section)) == 1:
                return EVIDENCE_ADJACENT
        except (ValueError, TypeError):
            pass

    return EVIDENCE_PRIMARY  # default


def _get_query_section(query: str) -> str | None:
    """Extract the target section number from the query."""
    try:
        from app.rag.retrieval.identifier import detect_section

        sec, _ = detect_section(query)
        if sec:
            return section_base(sec)
    except Exception:
        pass
    return None


def re_search_section_ref(text: str) -> bool:
    """Check if text contains a cross-reference to another section."""
    return bool(re.search(r"(?:section|sec\.|s\.|u/s)\s+\d", text, re.IGNORECASE))


# --------------------------------------------------------------------------- #
# Legal-unit grouping (Phase 2, roadmap §6–§7)
# --------------------------------------------------------------------------- #


def _chunk_score(chunk: Any) -> float:
    score = getattr(chunk, "score", None)
    if score is None and isinstance(chunk, dict):
        score = chunk.get("score")
    try:
        return float(score or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _chunk_id(chunk: Any) -> str | None:
    chunk_id = getattr(chunk, "chunk_id", None)
    if chunk_id is None and isinstance(chunk, dict):
        chunk_id = chunk.get("chunk_id")
    return str(chunk_id) if chunk_id is not None else None


def canonical_unit_id(chunk: Any) -> str:
    """Canonical legal-unit id for a chunk (never fabricated).

    Different payload UUIDs representing the same gold legal unit (Exp A
    lesson) share one id.  Chunks with no parseable identity fall back to
    their chunk_id so they are never merged.
    """
    try:
        canonical = parse_legal_identity(chunk).canonical_id()
    except Exception:
        canonical = "UNKNOWN"
    if not canonical or canonical == "UNKNOWN":
        return f"chunk:{_chunk_id(chunk)}"
    return canonical


def group_chunks_by_unit(ranked_chunks: list[Any]) -> dict[str, list[Any]]:
    """Group ranked chunks by canonical legal-unit id.

    Best score first within each group; groups ordered by their best
    member's score.  The selector reasons over unit representatives, not
    payload UUIDs.
    """
    groups: dict[str, list[Any]] = {}
    for chunk in ranked_chunks:
        groups.setdefault(canonical_unit_id(chunk), []).append(chunk)
    for members in groups.values():
        members.sort(key=_chunk_score, reverse=True)
    return dict(sorted(groups.items(), key=lambda kv: _chunk_score(kv[1][0]), reverse=True))


# --------------------------------------------------------------------------- #
# Redundancy and complementarity scoring
# --------------------------------------------------------------------------- #


def _compute_redundancy(item: EvidenceItem, existing: list[EvidenceItem]) -> float:
    """Compute redundancy score [0, 1] — 1.0 = exact duplicate, 0.0 = no overlap."""
    if not existing:
        return 0.0

    max_overlap = 0.0
    for ex in existing:
        # Same section + act = high redundancy
        if (
            item.section_number
            and ex.section_number
            and item.act_name
            and ex.act_name
            and item.section_number == ex.section_number
            and item.act_name.lower() == ex.act_name.lower()
        ):
            return 1.0  # exact section duplicate

        # Text overlap via simple token Jaccard
        if item.text_snippet and ex.text_snippet:
            overlap = _token_jaccard(item.text_snippet, ex.text_snippet)
            max_overlap = max(max_overlap, overlap)

    return min(max_overlap, 1.0)


def _token_jaccard(a: str, b: str) -> float:
    """Simple token-level Jaccard similarity."""
    ta = set(w for w in a.lower().split() if len(w) > 3)
    tb = set(w for w in b.lower().split() if len(w) > 3)
    if not ta and not tb:
        return 0.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _compute_complementarity(item: EvidenceItem, existing: list[EvidenceItem]) -> float:
    """Compute complementarity score [0, 1] — 1.0 = maximal new coverage.

    Complements when it provides a different evidence type, a different
    section family, or a different relationship to the primary provision.
    """
    if not existing:
        return 1.0

    # Different evidence type → high complementarity
    existing_types = {ex.evidence_type for ex in existing}
    if item.evidence_type not in existing_types:
        return 1.0

    # Different section → moderate complementarity
    if item.section_number and existing:
        existing_sections = {ex.section_number for ex in existing if ex.section_number}
        if item.section_number not in existing_sections and section_base(item.section_number) != section_base(
            existing[0].section_number or ""
        ):
            return 0.75

    # Text novelty
    if item.text_snippet and existing:
        overlaps = [_token_jaccard(item.text_snippet, ex.text_snippet) for ex in existing if ex.text_snippet]
        max_overlap = max(overlaps) if overlaps else 0.0
        return max(0.0, 1.0 - max_overlap)

    return 0.5


# --------------------------------------------------------------------------- #
# Evidence-set construction
# --------------------------------------------------------------------------- #

#: Evidence type priority for selecting the primary provision
_EVIDENCE_TYPE_PRIORITY = {
    EVIDENCE_PRIMARY: 10,
    EVIDENCE_DEFINITION: 9,
    EVIDENCE_EXCEPTION: 8,
    EVIDENCE_PENALTY: 7,
    EVIDENCE_SUBSECTION: 6,
    EVIDENCE_CROSS_REFERENCE: 5,
    EVIDENCE_AUTHORITY: 4,
    EVIDENCE_ADJACENT: 3,
    EVIDENCE_DUPLICATE: 0,
}


def select_evidence_set(
    query: str,
    ranked_chunks: list[Any],
    max_size: int = 5,
    min_size: int = 2,
    act_hint: str | None = None,
) -> EvidenceSet:
    """Select a complementary evidence set from ranked chunks.

    Algorithm (deterministic):

    1. Classify each chunk's evidence type.
    2. Score each by: ``ce_score * 0.6 + type_priority * 0.2 + (1 - redundancy) * 0.2``.
    3. Greedily select, maximizing complementarity while minimizing redundancy.
    4. Ensure minimum size, prefer diverse evidence types.

    Args:
        query: The original user query.
        ranked_chunks: Chunks already reranked (top-K first).
        max_size: Maximum evidence items to select.
        min_size: Minimum evidence items (falls short only if pool is too small).
        act_hint: Optional Act name to attach to evidence items.

    Returns:
        ``EvidenceSet`` with selected items.
    """
    if not ranked_chunks:
        return EvidenceSet(query=query, items=[], total_pool=0)

    query_section = _get_query_section(query)
    items: list[EvidenceItem] = []
    units: list[str] = []

    for _i, chunk in enumerate(ranked_chunks):
        text = getattr(chunk, "text", "") or ""
        if isinstance(chunk, dict):
            text = chunk.get("text", "") or ""
        section_number = getattr(chunk, "section_number", None) or (
            chunk.get("section_number") if isinstance(chunk, dict) else None
        )
        act_name = (
            act_hint or getattr(chunk, "act_name", None) or (chunk.get("act_name") if isinstance(chunk, dict) else None)
        )

        evidence_type = _detect_evidence_type(chunk, query_section)
        confidence = _chunk_score(chunk)
        unit = canonical_unit_id(chunk)
        units.append(unit)

        item = EvidenceItem(
            chunk=chunk,
            evidence_type=evidence_type,
            confidence=confidence,
            legal_identity=unit
            if not unit.startswith("chunk:")
            else (
                f"{act_name}::{section_number}" if act_name and section_number else (act_name or section_number or "")
            ),
            section_number=section_number,
            act_name=act_name,
            text_snippet=text[:500],
        )
        item.redundancy = _compute_redundancy(item, items)
        item.complementarity = _compute_complementarity(item, items)
        items.append(item)

    # Phase 2: one representative per canonical unit (best score first —
    # items are in ranked order).  Same-unit payloads are explicit
    # EVIDENCE_DUPLICATE backfill, never silent diversity members.
    seen_units: set[str] = set()
    representatives: list[EvidenceItem] = []
    duplicates: list[EvidenceItem] = []
    for item, unit in zip(items, units, strict=True):
        if unit in seen_units:
            item.evidence_type = EVIDENCE_DUPLICATE
            item.redundancy = 1.0
            duplicates.append(item)
        else:
            seen_units.add(unit)
            representatives.append(item)

    # Score: CE score * 0.6 + type_priority * 0.2 + complementarity * 0.2 - redundancy * 0.1
    def _score(item: EvidenceItem) -> float:
        return (
            item.confidence * 0.6
            + _EVIDENCE_TYPE_PRIORITY.get(item.evidence_type, 5) * 0.02
            + item.complementarity * 0.2
            - item.redundancy * 0.3
        )

    # Greedy selection: pick highest-scoring non-redundant items, prioritizing
    # diversity of evidence types
    selected: list[EvidenceItem] = []
    remaining = list(representatives)

    # Always pick the primary provision first if available
    primary_items = [it for it in remaining if it.evidence_type == EVIDENCE_PRIMARY]
    non_primary = [it for it in remaining if it.evidence_type != EVIDENCE_PRIMARY]

    if primary_items:
        selected.append(primary_items[0])
        remaining = non_primary

    while len(selected) < max_size and remaining:
        # Sort remaining by score descending
        remaining.sort(key=_score, reverse=True)
        best = remaining.pop(0)

        # Skip if it's a duplicate (redundancy > 0.9) of something already selected
        if best.redundancy > 0.9 and len(selected) >= min_size:
            continue

        # Recompute redundancy against the current selection
        best.redundancy = _compute_redundancy(best, selected)
        best.complementarity = _compute_complementarity(best, selected)
        if best.redundancy > 0.95 and len(selected) >= min_size:
            continue

        selected.append(best)

    # Ensure minimum size: leftover representatives first, then explicit
    # same-unit duplicates (typed EVIDENCE_DUPLICATE above).
    if len(selected) < min_size:
        for item in representatives + duplicates:
            if item not in selected:
                selected.append(item)
                if len(selected) >= min_size:
                    break

    selected = selected[:max_size]

    rationale = (
        f"Selected {len(selected)} evidence items from "
        f"{len(ranked_chunks)} ranked chunks "
        f"({len(representatives)} distinct legal units). "
        f"Types: {[it.evidence_type for it in selected]}"
    )

    return EvidenceSet(
        query=query,
        items=selected,
        total_pool=len(ranked_chunks),
        selection_rationale=rationale,
    )


# --------------------------------------------------------------------------- #
# Definition / exception / cross-reference expansion (Phase 2, roadmap §7)
# --------------------------------------------------------------------------- #

#: Quoted defined term: 'Food' means ... / "Food" means ...
_DEFINED_TERM_RE = re.compile(r"""['"]([^'"]{2,40})['"]\s+means\b""", re.IGNORECASE)


def _chunk_text(chunk: Any) -> str:
    text = getattr(chunk, "text", "") or ""
    if isinstance(chunk, dict):
        text = chunk.get("text", "") or ""
    return text


def _defined_term(chunk: Any) -> str | None:
    match = _DEFINED_TERM_RE.search(_chunk_text(chunk))
    return match.group(1).lower() if match else None


def _mentions_section(text: str, section: str | None) -> bool:
    if not section:
        return False
    return re.search(rf"(?:section|sec\.|s\.|u/s)\s+{re.escape(section)}\b", text, re.IGNORECASE) is not None


def _as_item(chunk: Any, evidence_type: str, existing: list[EvidenceItem]) -> EvidenceItem:
    try:
        ident = parse_legal_identity(chunk)
        unit = ident.canonical_id()
        section_number = ident.section
        act_name = ident.act
    except Exception:
        unit, section_number, act_name = "", None, None
    item = EvidenceItem(
        chunk=chunk,
        evidence_type=evidence_type,
        confidence=_chunk_score(chunk),
        legal_identity=unit,
        section_number=section_number,
        act_name=act_name,
        text_snippet=_chunk_text(chunk)[:500],
    )
    item.redundancy = _compute_redundancy(item, existing)
    item.complementarity = _compute_complementarity(item, existing)
    return item


def expand_evidence_units(
    evidence: EvidenceSet,
    pool: list[Any] | None = None,
    *,
    reference_lookup: Callable[[str], list[Any]] | None = None,
    max_expansion: int = 3,
) -> EvidenceSet:
    """Expand an evidence set with missing definitions/exceptions/cross-refs.

    Roadmap §7 flow: after unit grouping, pull in the provisions the set
    *depends on* — definitions of used terms, exceptions qualifying the
    rules, and cross-referenced sections — from the CE reservoir (``pool``)
    plus an optional ``reference_lookup`` (the KG seam: unit id → related
    chunks; ``KGReasoner`` plugs in here once Neo4j is wired).

    Gaps are filled in definition → exception → cross-reference order,
    capped at ``max_expansion`` additions.  Units already in the set are
    never re-added.      Returns a new ``EvidenceSet``; the input is unchanged.
    """
    candidates: list[Any] = list(pool) if pool is not None else [it.chunk for it in evidence.items]
    if reference_lookup is not None:
        for item in evidence.items:
            try:
                extra = reference_lookup(item.legal_identity) or []
            except Exception:
                extra = []
            candidates.extend(extra)

    present_units = {canonical_unit_id(it.chunk) for it in evidence.items}
    present_types = {it.evidence_type for it in evidence.items}
    present_type_by_unit: dict[str, set[str]] = {}
    for it in evidence.items:
        present_type_by_unit.setdefault(canonical_unit_id(it.chunk), set()).add(it.evidence_type)
    set_texts = " ".join(_chunk_text(it.chunk).lower() for it in evidence.items)
    set_sections = {section_base(it.section_number or "") for it in evidence.items if it.section_number}

    def _unit(chunk: Any) -> str:
        return canonical_unit_id(chunk)

    def _take(predicate: Callable[[Any], bool], wanted: str) -> list[Any]:
        # A gap is (unit, provision-type): a same-section proviso chunk is
        # new coverage even though its unit is already present.
        found: list[Any] = []
        for chunk in candidates:
            unit = _unit(chunk)
            if wanted in present_type_by_unit.get(unit, set()):
                continue
            if any(_unit(c) == unit for c in found):
                continue
            if predicate(chunk):
                found.append(chunk)
        return found

    additions: list[tuple[Any, str]] = []

    # 1. Definitions of terms the set actually uses (word-boundary matched).
    if EVIDENCE_DEFINITION not in present_types:
        for chunk in _take(lambda c: detect_provision_type(_chunk_text(c)) == "definition", EVIDENCE_DEFINITION):
            term = _defined_term(chunk)
            if term and re.search(rf"\b{re.escape(term)}\b", set_texts):
                additions.append((chunk, EVIDENCE_DEFINITION))

    # 2. Exceptions qualifying the set's rules (same section family or cited).
    if EVIDENCE_EXCEPTION not in present_types:
        for chunk in _take(lambda c: detect_provision_type(_chunk_text(c)) == "exception", EVIDENCE_EXCEPTION):
            try:
                section = parse_legal_identity(chunk).section
            except Exception:
                section = None
            text = _chunk_text(chunk)
            if (section and section_base(section) in set_sections) or any(
                _mentions_section(text, s) for s in set_sections
            ):
                additions.append((chunk, EVIDENCE_EXCEPTION))

    # 3. Cross-referenced sections named by the set.
    wanted_refs: list[str] = []
    for item in evidence.items:
        try:
            wanted_refs.extend(parse_legal_identity(item.chunk).cross_references)
        except Exception:
            continue
    for ref in dict.fromkeys(wanted_refs):
        for chunk in candidates:
            try:
                section = parse_legal_identity(chunk).section
            except Exception:
                continue
            if section and section_base(section) == ref and _unit(chunk) not in present_units:
                if not any(_unit(c) == _unit(chunk) for c, _ in additions):
                    additions.append((chunk, EVIDENCE_CROSS_REFERENCE))
                break

    items = list(evidence.items)
    for chunk, evidence_type in additions[: max(0, max_expansion)]:
        items.append(_as_item(chunk, evidence_type, items))
        present_units.add(_unit(chunk))

    return EvidenceSet(
        query=evidence.query,
        items=items,
        total_pool=len(candidates),
        selection_rationale=evidence.selection_rationale
        + f" Expansion added {len(items) - len(evidence.items)} units.",
    )


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #


def _evidence_selector_enabled() -> bool:
    """Check if evidence-set selection is enabled (shared config seam)."""
    enabled: bool = cfg.evidence_selector
    return enabled
