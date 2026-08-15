"""Legal-identifier detection & identifier-route query builder (production).

The "identifier route" is the single decisive lever measured across the V5 /
V5.5 evaluation arc: when a user query names an Act and/or section number,
a *lexical* query built from the canonical identifier (e.g. ``"Indian
Contract Act, 1872 section 73"``) recovers gold provisions that vector
retrieval misses — +13.3pp candidate-pool ceiling (0.705 → 0.838) and,
combined with the section-stamp backfill, a 91.9% → 100% pool ceiling.

This module ports the V5.5 rule-based detector
(``evaluation/query_expansion.py``) into the production RAG contract so the
retrieval pipeline can run the identifier as a parallel additive arm.  It is
a static vocabulary — not gold labels — and needs no LLM, no Qdrant, and no
Neo4j.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# Instrument vocabulary (static, deployment-time component — NOT gold labels)
# --------------------------------------------------------------------------- #
#: Canonical act names as they appear in the corpus / gold registry.  Used only
#: as a *vocabulary* to recognise act mentions inside the query text.
CANONICAL_ACTS: list[str] = [
    "Food Safety and Standards Act, 2006",
    "Environment (Protection) Act, 1986",
    "Air (Prevention and Control of Pollution) Act, 1981",
    "Water (Prevention and Control of Pollution) Act, 1974",
    "Plastic Waste Management Rules",
    "Plastic Waste Management (Amendment) Rules, 2022",
    "Solid Waste Management Rules, 2026",
    "Kolkata Municipal Corporation Act, 1980",
    "West Bengal Premises Tenancy Act, 1997",
    "West Bengal Meat Order, 1966",
    "Indian Contract Act, 1872",
    "Sale of Goods Act, 1930",
    "Indian Partnership Act, 1932",
    "Companies Act, 2013",
    "Limitation Act, 1963",
    "Consumer Protection Act, 2019",
    "Specific Relief Act, 2017",
    "Prevention of Cruelty to Animals Rules, 2017",
    "Bengal Diseases of Animals (Amendment) Act, 2008",
    "Bengal Livestock Import Quarantine Rules, 1944",
    "WB Prevention and Control of Infectious Diseases in Animals Rules, 2016",
    "Bharatiya Nyaya Sanhita, 2023",
]

#: Short-name aliases -> canonical act (for query text like "FSS Act 2006").
_ALIASES: dict[str, str] = {
    "fss act": "Food Safety and Standards Act, 2006",
    "fssa": "Food Safety and Standards Act, 2006",
    "fssai": "Food Safety and Standards Act, 2006",
    "food safety and standards act": "Food Safety and Standards Act, 2006",
    "environment (protection) act": "Environment (Protection) Act, 1986",
    "environment protection act": "Environment (Protection) Act, 1986",
    "epa": "Environment (Protection) Act, 1986",
    "air act": "Air (Prevention and Control of Pollution) Act, 1981",
    "water act": "Water (Prevention and Control of Pollution) Act, 1974",
    "plastic waste management rules": "Plastic Waste Management Rules",
    "kmc act": "Kolkata Municipal Corporation Act, 1980",
    "kolkata municipal corporation act": "Kolkata Municipal Corporation Act, 1980",
    "wbpt act": "West Bengal Premises Tenancy Act, 1997",
    "west bengal premises tenancy act": "West Bengal Premises Tenancy Act, 1997",
    "contract act": "Indian Contract Act, 1872",
    "indian contract act": "Indian Contract Act, 1872",
    "sale of goods act": "Sale of Goods Act, 1930",
    "partnership act": "Indian Partnership Act, 1932",
    "companies act": "Companies Act, 2013",
    "limitation act": "Limitation Act, 1963",
    "consumer protection act": "Consumer Protection Act, 2019",
    "specific relief act": "Specific Relief Act, 2017",
    "cruelty to animals": "Prevention of Cruelty to Animals Rules, 2017",
    "meat order": "West Bengal Meat Order, 1966",
}

#: Section-number mentions inside a query: "Section 31(2)", "Sec. 31", "s. 31", "u/s 31".
_SECTION_RE = re.compile(
    r"\b(?:section|sec\.|s\.|u/s)\s*(\d{1,4})(?:\s*\((\d{1,2})\))?",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def detect_act(query: str) -> str | None:
    """Return the canonical Act name mentioned in ``query``, if any."""
    q = _norm(query).lower()
    # 1) canonical-name containment (longest first so "Plastic Waste Management
    #    (Amendment) Rules, 2022" wins over "Plastic Waste Management Rules")
    for act in sorted(CANONICAL_ACTS, key=len, reverse=True):
        if act.lower() in q:
            return act
    # 2) alias match
    for alias, act in sorted(_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if alias in q:
            return act
    return None


def detect_section(query: str) -> tuple[str | None, str | None]:
    """Return ``(section_number, subsection)`` mentioned in ``query``, if any."""
    m = _SECTION_RE.search(query)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def identifier_query(query: str) -> tuple[str | None, dict[str, Any]]:
    """Build the identifier-route query from the query text alone.

    Returns ``(query, meta)``.  ``meta`` records what was detected so callers
    can report coverage.  Order: act+section when both are known; act alone
    when only the act is mentioned (the dominant production case); section
    alone otherwise.  ``None`` when nothing is detected (no identifier arm).
    """
    act = detect_act(query)
    section, subsection = detect_section(query)
    meta: dict[str, Any] = {
        "act": act,
        "section": section,
        "subsection": subsection,
        "form": "none",
    }
    if act and section:
        parts = [act, f"section {section}"]
        if subsection:
            parts.append(f"subsection {subsection}")
        meta["form"] = "act+section"
        return " ".join(parts), meta
    if act:
        meta["form"] = "act"
        return act, meta
    if section:
        meta["form"] = "section"
        return f"section {section}", meta
    return None, meta
