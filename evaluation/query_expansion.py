"""RANKING_CEILING_V3 — rule-based query expansion (no LLM, no gold labels).

Motivation (from V1/V2): natural-language queries under-surface corpus-present
gold chunks, while lexical "Act section N" identifiers retrieve ~2x better.
A production query rewriter can append the *detected* legal identifier (Act
name + section number found in the user's own question text) to the query
before embedding — no gold labels required.

:func:`expand_query` returns ``(expanded_query, meta)`` where ``meta`` records
what was detected (act, section, subsection) so downstream reports can
stratify by coverage.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# Instrument vocabulary (static, deployment-time component — NOT gold labels)
# --------------------------------------------------------------------------- #
#: Canonical act names as they appear in the corpus / gold registry.  Used only
#: as a *vocabulary* to recognise act mentions inside the question text.
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

#: Short-name aliases -> canonical act (for question text like "FSS Act 2006").
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

#: Section-number mentions inside a question: "Section 31(2)", "Sec. 31", "s. 31", "u/s 31".
_SECTION_RE = re.compile(
    r"\b(?:section|sec\.|s\.|u/s)\s*(\d{1,3})(?:\s*\((\d{1,2})\))?",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def detect_act(question: str) -> str | None:
    """Return the canonical Act name mentioned in the question, if any."""
    q = question.lower()
    q = _norm(q)
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


def detect_section(question: str) -> tuple[str | None, str | None]:
    """Return (section_number, subsection) mentioned in the question, if any."""
    m = _SECTION_RE.search(question)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _canonical_in_question(question: str, canonical: str) -> bool:
    """True if the canonical Act name already appears in the question.

    Punctuation-normalized containment (the corpus drops commas from e.g.
    "Food Safety and Standards Act, 2006") plus a token-overlap fallback
    (>80% of the canonical's significant tokens present).
    """
    q_norm = re.sub(r"[^a-z0-9 ]", " ", question.lower())
    c_norm = re.sub(r"[^a-z0-9 ]", " ", canonical.lower())
    if c_norm.strip() in q_norm:
        return True
    c_toks = {t for t in c_norm.split() if len(t) > 2}
    q_toks = set(q_norm.split())
    if not c_toks:
        return False
    return len(c_toks & q_toks) / len(c_toks) >= 0.8


def _section_in_question(question: str, section: str) -> bool:
    """True if the question already contains a ``section N`` mention."""
    return re.search(rf"\bsection\s*{section}\b", question, re.IGNORECASE) is not None


def expand_query(question: str, dedup: bool = True) -> tuple[str, dict[str, Any]]:
    """Build the expanded retrieval query from the question text alone.

    Expansion: append ``"<Canonical Act name> section <N>"`` when detected.
    Unresolved (no act, no section) questions are returned verbatim.

    ``dedup=True`` (V4): skip appending an identifier already present in the
    question.  Appending the same Act name that the user already wrote biases
    both retrievers toward the same act-specific chunks and *reduces* retriever
    diversity — the V3 experiment showed this costs the plain hybrid −1.2pp
    R@10 while helping the fused union.  The canonical name is still appended
    when the question used only an alias (e.g. "FSS Act"), because the
    canonical form adds real tokens then.
    """
    act = detect_act(question)
    section, subsection = detect_section(question)
    meta: dict[str, Any] = {
        "act": act,
        "section": section,
        "subsection": subsection,
        "expanded": False,
        "dedup_act": False,
        "dedup_section": False,
    }
    parts: list[str] = []
    if act:
        if dedup and _canonical_in_question(question, act):
            meta["dedup_act"] = True
        else:
            parts.append(act)
    if section:
        if dedup and _section_in_question(question, section):
            meta["dedup_section"] = True
        else:
            parts.append(f"section {section}")
        if subsection:
            parts.append(f"subsection {subsection}")
    if parts:
        return f"{question} | {' '.join(parts)}", {**meta, "expanded": True}
    return question, meta


def coverage_report() -> dict[str, Any]:
    """Coverage stats over the frozen benchmark (diagnostic, no gold use)."""
    from evaluation.benchmark import load_questions

    questions = load_questions()
    n = len(questions)
    with_act = sum(1 for q in questions if detect_act(q.question))
    with_section = sum(1 for q in questions if detect_section(q.question)[0])
    expanded = sum(1 for q in questions if expand_query(q.question)[1]["expanded"])
    expanded_dedup = sum(1 for q in questions if expand_query(q.question, dedup=True)[1]["expanded"])
    dedup_act = sum(1 for q in questions if expand_query(q.question, dedup=True)[1]["dedup_act"])
    dedup_section = sum(1 for q in questions if expand_query(q.question, dedup=True)[1]["dedup_section"])
    return {
        "n_questions": n,
        "act_detected": with_act,
        "section_detected": with_section,
        "expanded": expanded,
        "expanded_dedup": expanded_dedup,
        "dedup_act": dedup_act,
        "dedup_section": dedup_section,
    }


if __name__ == "__main__":
    pass
