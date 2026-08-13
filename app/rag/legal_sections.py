"""Per-act section knowledge for the multi-domain RAG corpus (Phase 1 — de-FSSAI).

Central registry mapping canonical act names to their section ranges.  Used
only for the advisory "known section" flag on extracted cross-references
(and the query-classifier's full-Act section set) — extraction and ranking
never depend on it.

Semantics:

- ``sections_for_act(act_name)`` returns the known section set for an act,
  or ``None`` when the act is not in the registry (unknown ≠ "not a real
  section" — we never claim a section is invalid just because the act is
  new to us).
- ``is_known_section_for_act(section, act_name)`` is ``True``/``False``
  when the act's sections are known and ``None`` when they are not.
- ``FSS_ACT_SECTIONS`` is the canonical Food Safety and Standards Act, 2006
  set (sections 1–104) — re-exported by ``app/rag/retrieval/query_classifier.py``
  for backward compatibility.

Act-name matching normalises leading articles and whitespace/case, so
"The Air (Prevention and Control of Pollution) Act, 1981" and
"Air (Prevention and Control of Pollution) Act, 1981" resolve identically.
"""

from __future__ import annotations

import re

#: Canonical act name -> inclusive section range (best-effort "known"
#: sections; advisory only — see module docstring).
ACT_SECTION_RANGES: dict[str, tuple[int, int]] = {
    # FSS family
    "Food Safety and Standards Act, 2006": (1, 104),
    # Environment (central)
    "Environment (Protection) Act, 1986": (1, 26),
    "Water (Prevention and Control of Pollution) Act, 1974": (1, 64),
    "Air (Prevention and Control of Pollution) Act, 1981": (1, 54),
    # Commercial / corporate (central)
    "Companies Act, 2013": (1, 470),
    "Indian Contract Act, 1872": (1, 238),
    "Sale of Goods Act, 1930": (1, 66),
    "Indian Partnership Act, 1932": (1, 74),
    "Limited Liability Partnership Act, 2008": (1, 81),
    "Limitation Act, 1963": (1, 32),
    "Specific Relief Act, 1963": (1, 44),
    "Consumer Protection Act, 2019": (1, 107),
    # State (West Bengal)
    "Kolkata Municipal Corporation Act, 1980": (1, 636),
    "West Bengal Premises Tenancy Act, 1997": (1, 60),
    # Criminal (central) — replaces the Indian Penal Code, 1860
    "Bharatiya Nyaya Sanhita, 2023": (1, 358),
}

#: Full FSS Act, 2006 section coverage (sections 1–104).
FSS_ACT_SECTIONS: frozenset[str] = frozenset(str(n) for n in range(1, 105))


def _normalise(name: str) -> str:
    """Lowercase, strip leading articles, collapse non-alphanumerics."""
    text = re.sub(r"^(?:the|an|a)\s+", "", str(name or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def sections_for_act(act_name: str | None) -> frozenset[str] | None:
    """Return the known section set for *act_name* (``None`` when unknown).

    Matches by normalised exact equality, falling back to containment
    (either direction) so "The X Act, 2013" and "X Act, 2013" resolve.
    """
    target = _normalise(act_name)
    if not target:
        return None
    for name, (lo, hi) in ACT_SECTION_RANGES.items():
        candidate = _normalise(name)
        if not candidate:
            continue
        if target == candidate:
            return frozenset(str(n) for n in range(lo, hi + 1))
        # Containment fallback only for reasonably specific names — a short
        # string like "Act" or "2013" must never resolve by substring.
        if len(target) >= 8 and (target in candidate or candidate in target):
            return frozenset(str(n) for n in range(lo, hi + 1))
    return None


def is_known_section_for_act(section: str | None, act_name: str | None) -> bool | None:
    """Whether *section* is a known section of *act_name*.

    Returns ``True``/``False`` when the act is registered and ``None`` when
    the act is unknown (advisory — never a legal assertion).  Sub-clause /
    marker suffixes (``"26(2)(ii)"``) resolve against the base number.
    """
    known = sections_for_act(act_name)
    if known is None:
        return None
    base = re.match(r"^\d{1,4}", str(section or "").strip())
    if not base:
        return False
    return base.group(0) in known


# End of legal_sections.py
