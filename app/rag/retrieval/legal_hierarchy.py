"""Legal hierarchy representation and relationship queries.

Implements the Act → Chapter → Section → Subsection → Clause hierarchy with
bidirectional traversal.  Works on both structured ``LegalIdentity`` objects
and free-text section strings (e.g. ``"31(2)(a)"``).

All functions are pure — no database, no Neo4j — and independently testable.
The hierarchical relationship model follows section 4 of the parallel spec:

    Act
     └── Chapter
          └── Section
               └── Subsection
                    └── Clause

Reverse lookup: clause → subsection → section → chapter → Act.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# Section-string parsing
# --------------------------------------------------------------------------- #

#: Matches a section number optionally followed by subsection/clause chain.
#: Example matches: "31", "31(2)", "31(2)(a)", "31(2)(a)(iii)".
_SECTION_CHAIN_RE = re.compile(r"^(\d{1,4})" + r"(?:\(([^()]*)\))*")


def parse_section_chain(section: str | None) -> list[str]:
    """Parse ``"31(2)(a)"`` → ``["31", "2", "a"]``.

    Returns `[]` when the input doesn't start with a base number.
    Sub-clauses like ``"31(2)(a)(iii)"`` all collapse into the chain.
    """
    if not section:
        return []
    base = re.match(r"\s*(\d{1,4})", str(section))
    if not base:
        return []
    chain = [base.group(1)]
    # Extract all parenthesised groups after the base number
    rest = str(section)[base.end():]
    for m in re.finditer(r"\(([^()]*)\)", rest):
        val = m.group(1).strip()
        if val:
            chain.append(val)
    return chain


def section_base(section: str | None) -> str | None:
    """Return the base section number (e.g. ``"31"``) from ``"31(2)(a)"``."""
    if not section:
        return None
    m = re.match(r"\s*(\d{1,4})", str(section))
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# Hierarchy level classification
# --------------------------------------------------------------------------- #

#: Depth from root: 1=Act root, 2=Chapter, 3=Section, 4=Subsection, 5=Clause.
def hierarchy_depth(section: str | None) -> int:
    """Return the hierarchy depth of a section string.

    ``"31"`` → 3 (section level — Act root + chapter are implicit)
    ``"31(2)"`` → 4 (subsection)
    ``"31(2)(a)"`` → 5 (clause)
    ``""``  → 1 (document root)
    """
    chain = parse_section_chain(section)
    if not chain:
        return 1
    if len(chain) == 1:
        return 3  # section
    if len(chain) == 2:
        return 4  # subsection
    return 5  # clause or deeper


@dataclass
class SectionRelationship:
    """Result of comparing two section strings for a structural relationship."""

    relationship: str  # "exact", "parent", "child", "sibling", "adjacent", "same_family", "none"
    depth: int  # difference in chain depth (0 = same level)
    common_prefix: list[str]  # the shared prefix of the two chains


# --------------------------------------------------------------------------- #
# Relationship functions
# --------------------------------------------------------------------------- #

_RELATIONSHIP_NAMES = {"exact", "parent", "child", "sibling", "adjacent", "same_family", "none"}


def _relationship(a: str | None, b: str | None) -> SectionRelationship:
    """Compute the structural relationship between two section strings."""
    ca = parse_section_chain(a)
    cb = parse_section_chain(b)

    if not ca and not cb:
        return SectionRelationship("none", 0, [])
    if not ca or not cb:
        return SectionRelationship("none", 0, [])

    # Exact match
    if ca == cb:
        return SectionRelationship("exact", 0, ca)

    # Determine common prefix length
    common_len = 0
    for x, y in zip(ca, cb, strict=False):
        if x == y:
            common_len += 1
        else:
            break

    if common_len == 0:
        # Different base sections — check adjacency (same Act implied)
        ba = section_base(a)
        bb = section_base(b)
        if ba == bb:
            return SectionRelationship("exact", 0, ca)
        try:
            if abs(int(ba or 0) - int(bb or 0)) == 1:
                return SectionRelationship("adjacent", 0, [])
        except (ValueError, TypeError):
            pass
        return SectionRelationship("none", 0, [])

    # Parent-child: one is a proper prefix of the other
    if common_len == min(len(ca), len(cb)) and len(ca) != len(cb):
        if len(ca) < len(cb):
            return SectionRelationship("parent", len(cb) - len(ca), ca)
        else:
            return SectionRelationship("child", len(ca) - len(cb), cb)

    # Same family (same base section)
    if section_base(a) == section_base(b) and common_len >= 1:
        if len(ca) == len(cb) and common_len == len(ca) - 1:
            return SectionRelationship("sibling", 0, ca[:common_len])
        if common_len == min(len(ca), len(cb)):
            return SectionRelationship("sibling", 0, ca[:common_len])
        return SectionRelationship("same_family", abs(len(ca) - len(cb)), ca[:common_len])

    # Adjacent (same parent, consecutive base sections)
    try:
        if abs(int(ca[0]) - int(cb[0])) == 1 and len(ca) == 1 and len(cb) == 1:
            return SectionRelationship("adjacent", 0, [])
    except (ValueError, TypeError):
        pass

    return SectionRelationship("none", 0, [])


def exact_section_match(a: str | None, b: str | None) -> bool:
    """True when two section strings resolve to the same full chain."""
    return _relationship(a, b).relationship == "exact"


def same_act(a_act: str | None, b_act: str | None) -> bool:
    """True when both chunks belong to the same Act (case-insensitive)."""
    if not a_act or not b_act:
        return False
    return a_act.lower().strip() == b_act.lower().strip()


def same_chapter(chunk_a: Any, chunk_b: Any) -> bool:
    """True when two chunks are in the same chapter of the same Act."""
    a_chain = parse_section_chain(getattr(chunk_a, "section_number", None))
    b_chain = parse_section_chain(getattr(chunk_b, "section_number", None))
    if not a_chain or not b_chain:
        return False
    if len(a_chain) < 1 or len(b_chain) < 1:
        return False
    # Chapter = base section (same first element)
    return a_chain[0] == b_chain[0] and same_act(
        getattr(chunk_a, "act_name", None), getattr(chunk_b, "act_name", None)
    )


def same_section_family(a: str | None, b: str | None) -> bool:
    """True when two section strings share the same base section number."""
    ba, bb = section_base(a), section_base(b)
    return ba is not None and ba == bb


def parent_child(a: str | None, b: str | None) -> bool:
    """True when ``a`` is a parent of ``b`` (or vice versa)."""
    rel = _relationship(a, b)
    return rel.relationship in ("parent", "child")


def sibling(a: str | None, b: str | None) -> bool:
    """True when ``a`` and ``b`` are siblings (same parent, same depth)."""
    return _relationship(a, b).relationship in ("sibling", "exact")


def adjacent_section(a: str | None, b: str | None) -> bool:
    """True when two base sections are numerically adjacent (e.g. 31 ↔ 32)."""
    return _relationship(a, b).relationship == "adjacent"


def subsection_relationship(a: str | None, b: str | None) -> bool:
    """True when ``a`` is a parent subsection of ``b`` or vice versa."""
    rel = _relationship(a, b)
    return rel.relationship in ("parent", "child") and rel.depth <= 2


# --------------------------------------------------------------------------- #
# Hierarchy-aware proximity score
# --------------------------------------------------------------------------- #

def hierarchy_proximity(a: str | None, b: str | None) -> float:
    """Return a [0, 1] proximity score based on hierarchical closeness.

    ``1.0`` = exact match, ``0.75`` = parent/child, ``0.5`` = same family,
    ``0.25`` = adjacent section, ``0.0`` = unrelated.
    """
    rel = _relationship(a, b)
    scores = {
        "exact": 1.0,
        "parent": 0.75,
        "child": 0.75,
        "sibling": 0.5,
        "same_family": 0.5,
        "adjacent": 0.25,
        "none": 0.0,
    }
    return scores.get(rel.relationship, 0.0)


# --------------------------------------------------------------------------- #
# Relationship from LegalIdentity objects
# --------------------------------------------------------------------------- #

def compare_identities(a: Any, b: Any) -> SectionRelationship:
    """Compare two chunk/identity objects for their hierarchical relationship."""
    a_sec = getattr(a, "section", None) or getattr(a, "section_number", None)
    b_sec = getattr(b, "section", None) or getattr(b, "section_number", None)
    return _relationship(a_sec, b_sec)


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #


def _legal_hierarchy_enabled() -> bool:
    """Check if legal hierarchy is enabled via env / Flask config."""
    try:
        from flask import current_app

        if current_app:
            return bool(current_app.config.get("ENABLE_HIERARCHY", True))
    except Exception:
        pass
    import os

    return os.environ.get("ENABLE_HIERARCHY", "true").lower() != "false"


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    assert parse_section_chain("31") == ["31"]
    assert parse_section_chain("31(2)") == ["31", "2"]
    assert parse_section_chain("31(2)(a)") == ["31", "2", "a"]
    assert parse_section_chain("31(2)(a)(iii)") == ["31", "2", "a", "iii"]
    assert parse_section_chain(None) == []
    assert parse_section_chain("") == []

    assert hierarchy_depth(None) == 1
    assert hierarchy_depth("31") == 3
    assert hierarchy_depth("31(2)") == 4
    assert hierarchy_depth("31(2)(a)") == 5

    assert exact_section_match("31(2)(a)", "31(2)(a)")
    assert not exact_section_match("31(2)", "31(2)(a)")
    assert same_section_family("31(2)", "31(2)(a)")
    assert parent_child("31", "31(2)")
    assert not parent_child("31", "32")
    assert adjacent_section("31", "32")
    assert not adjacent_section("31", "33")

    assert hierarchy_proximity("31(2)(a)", "31(2)(a)") == 1.0
    assert hierarchy_proximity("31(2)", "31(2)(a)") == 0.75
    assert hierarchy_proximity("31(2)", "31(3)") == 0.5
    assert hierarchy_proximity("31", "32") == 0.25
    assert hierarchy_proximity("31", "99") == 0.0

