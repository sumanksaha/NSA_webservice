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
import os
import re
from dataclasses import dataclass, field
from typing import Any

from app.rag.retrieval.legal_hierarchy import (
    hierarchy_proximity,
    section_base,
    section_base as _section_base,
)
from app.rag.retrieval.reference_extractor import Reference

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
    EVIDENCE_PRIMARY, EVIDENCE_DEFINITION, EVIDENCE_EXCEPTION,
    EVIDENCE_PENALTY, EVIDENCE_CROSS_REFERENCE, EVIDENCE_AUTHORITY,
    EVIDENCE_SUBSECTION, EVIDENCE_ADJACENT, EVIDENCE_DUPLICATE,
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
    if any(kw in text_lower for kw in ["means ", "means,", "\"means", "includes", "for the purposes"]):
        return EVIDENCE_DEFINITION

    # Authority provisions — contain "authority", "power", "may", "shall"
    if any(kw in text_lower for kw in ["authority", "power to", "may "]):
        return EVIDENCE_AUTHORITY

    # Cross-reference — contains "section" references to other sections
    if re_search_section_ref(text):
        return EVIDENCE_CROSS_REFERENCE

    # Subsection of the primary section
    if query_section and section_number:
        chain = parse_chain(section_number)
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


def parse_chain(section: str | None) -> list[str]:
    """Parse a section string into its chain components."""
    if not section:
        return []
    return section_base_chain(section)


def section_base_chain(section: str | None) -> list[str]:
    """Parse ``"31(2)(a)"`` → ``["31", "2", "a"]``."""
    if not section:
        return []
    import re as _re

    base = _re.match(r"\s*(\d{1,4})", str(section))
    if not base:
        return []
    chain = [base.group(1)]
    rest = str(section)[base.end():]
    for m in _re.finditer(r"\(([^()]*)\)", rest):
        val = m.group(1).strip()
        if val:
            chain.append(val)
    return chain


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
        if item.section_number not in existing_sections:
            if item.section_number and section_base(item.section_number) != section_base(
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

    for i, chunk in enumerate(ranked_chunks):
        text = getattr(chunk, "text", "") or ""
        if isinstance(chunk, dict):
            text = chunk.get("text", "") or ""
        section_number = getattr(chunk, "section_number", None) or (
            chunk.get("section_number") if isinstance(chunk, dict) else None
        )
        act_name = act_hint or getattr(chunk, "act_name", None) or (
            chunk.get("act_name") if isinstance(chunk, dict) else None
        )

        evidence_type = _detect_evidence_type(chunk, query_section)
        confidence = float(getattr(chunk, "score", 0.0) or 0.0)

        item = EvidenceItem(
            chunk=chunk,
            evidence_type=evidence_type,
            confidence=confidence,
            legal_identity=f"{act_name}::{section_number}" if act_name and section_number else (act_name or section_number or ""),
            section_number=section_number,
            act_name=act_name,
            text_snippet=text[:500],
        )
        item.redundancy = _compute_redundancy(item, items)
        item.complementarity = _compute_complementarity(item, items)
        items.append(item)

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
    remaining = list(items)

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

    # Ensure minimum size
    if len(selected) < min_size and items:
        for item in items:
            if item not in selected:
                selected.append(item)
                if len(selected) >= min_size:
                    break

    selected = selected[:max_size]

    rationale = (
        f"Selected {len(selected)} evidence items from "
        f"{len(ranked_chunks)} ranked chunks. "
        f"Types: {[it.evidence_type for it in selected]}"
    )

    return EvidenceSet(
        query=query,
        items=selected,
        total_pool=len(ranked_chunks),
        selection_rationale=rationale,
    )


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #


def _evidence_selector_enabled() -> bool:
    """Check if evidence-set selection is enabled."""
    try:
        from flask import current_app

        if current_app:
            return bool(current_app.config.get("ENABLE_EVIDENCE_SELECTOR", False))
    except Exception:
        pass
    return os.environ.get("ENABLE_EVIDENCE_SELECTOR", "false").lower() == "true"
