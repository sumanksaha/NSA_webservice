"""Cross-reference extraction from legal text.

Extracts legal references from chunk text and produces confidence-scored
reference objects.  Only HIGH-confidence references should automatically
enter candidate expansion.

Reference patterns detected:

    - ``Section X`` / ``Sec. X`` / ``s. X`` / ``u/s X``
    - ``Section X(1)`` / ``Section X(1)(a)``  (subsection chains)
    - ``Rule X`` / ``Schedule X`` / ``Chapter X``
    - Textual relation patterns: "subject to", "as provided under",
      "in accordance with", "read with", "notwithstanding", etc.

Confidence levels:

    HIGH   — explicit ``Section N(...)`` with subsection chain
    MEDIUM — explicit ``Section N`` without subsection, or ``Rule N``/``Schedule N``
    LOW    — textual relation pattern without an explicit section number
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.rag.retrieval.legal_hierarchy import parse_section_chain, section_base

# --------------------------------------------------------------------------- #
# Confidence levels
# --------------------------------------------------------------------------- #

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"


@dataclass
class Reference:
    """A single legal reference extracted from text.

    Attributes:
        act: Canonical Act name if the reference is scoped to an Act.
        section: Base section number (e.g. ``"31"``).
        subsection: Full subsection chain (e.g. ``["2", "a"]``).
        rule: Rule number if this is a rule reference.
        schedule: Schedule reference.
        chapter: Chapter reference.
        relation: Textual relation keyword (e.g. ``"subject to"``).
        confidence: HIGH / MEDIUM / LOW.
        span_start: Character offset in the source text.
        span_end: Character offset (exclusive) in the source text.
        raw: The raw matched text.
        target_provision_id: Provision ID if resolvable (often None).
    """

    act: str | None = None
    section: str | None = None
    subsection: list[str] = field(default_factory=list)
    clause: list[str] = field(default_factory=list)
    rule: str | None = None
    schedule: str | None = None
    chapter: str | None = None
    relation: str | None = None
    confidence: str = CONFIDENCE_MEDIUM
    span_start: int = 0
    span_end: int = 0
    raw: str = ""
    target_provision_id: str | None = None

    def canonical_ref(self) -> str:
        """Build a canonical reference string like ``"Section 31(2)(a)"``."""
        if self.section:
            subs = "".join(f"({s})" for s in self.subsection)
            clss = "".join(f"({c})" for c in self.clause)
            return f"Section {self.section}{subs}{clss}"
        if self.rule:
            return f"Rule {self.rule}"
        if self.schedule:
            return f"Schedule {self.schedule}"
        if self.chapter:
            return f"Chapter {self.chapter}"
        if self.act:
            return self.act
        if self.relation:
            return f"(via '{self.relation}')"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "act": self.act,
            "section": self.section,
            "subsection": self.subsection,
            "rule": self.rule,
            "schedule": self.schedule,
            "chapter": self.chapter,
            "relation": self.relation,
            "confidence": self.confidence,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "raw": self.raw,
            "target_provision_id": self.target_provision_id,
            "canonical_ref": self.canonical_ref(),
        }


# --------------------------------------------------------------------------- #
# Regex patterns
# --------------------------------------------------------------------------- #

#: Matches Section/Sec./s./u/s with optional subsection chain.
#: Examples: "Section 31", "Sec. 31(2)", "s. 31(2)(a)", "u/s 31".
#: Note: ``s\.`` requires a period to avoid matching the "s" in "is 3".
_SECTION_PAT = re.compile(
    r"\b(?:section|sec\.|s\.|u/s)\s*(\d{1,4})((?:\s*\([^()]*\))*)",
    re.IGNORECASE,
)

#: Matches "Rule N", "Schedule N", "Chapter N".
_RULE_PAT = re.compile(r"(rule|regulation)\s+(\d{1,4}[a-z]?)\b", re.IGNORECASE)
_SCHEDULE_PAT = re.compile(r"schedule\s+(\d{1,4}[a-z]?)\b", re.IGNORECASE)
_CHAPTER_PAT = re.compile(r"chapter\s+(\d{1,4}[a-z]?)\b", re.IGNORECASE)

#: Textual relation patterns — these signal a legal reference even without
#: an explicit section number (lower confidence).
_RELATION_PATTERNS: list[tuple[str, str]] = [
    ("subject to", "subject_to"),
    ("as provided under", "as_provided_under"),
    ("as provided in", "as_provided_in"),
    ("in accordance with", "in_accordance_with"),
    ("read with", "read_with"),
    ("read together with", "read_together"),
    ("notwithstanding", "notwithstanding"),
    ("referred to in", "referred_to_in"),
    ("as specified in", "as_specified_in"),
    ("except as provided by", "except_as_provided_by"),
    ("in terms of", "in_terms_of"),
    ("pursuant to", "pursuant_to"),
    ("in addition to", "in_addition_to"),
    ("subject to the provisions of", "subject_to_provisions_of"),
    ("in contravention of", "in_contravention_of"),
    ("in breach of", "in_breach_of"),
    ("as defined in", "as_defined_in"),
    ("meaning of", "meaning_of"),
    ("interpretation of", "interpretation_of"),
]

#: Build a single regex for all relation patterns.
_RELATION_RE = re.compile(
    "|".join(re.escape(pat) for pat, _ in _RELATION_PATTERNS),
    re.IGNORECASE,
)


def extract_references(
    text: str,
    act_hint: str | None = None,
    min_confidence: str = CONFIDENCE_LOW,
) -> list[Reference]:
    """Extract all legal references from *text*.

    Args:
        text: The legal text to scan.
        act_hint: Optional Act name to attach to section references that
            don't mention an Act explicitly.
        min_confidence: Minimum confidence level to include (HIGH / MEDIUM / LOW).

    Returns:
        List of ``Reference`` objects, ordered by position in the text.
    """
    if not text:
        return []

    refs: list[Reference] = []
    seen_spans: set[tuple[int, int]] = set()

    _CONFIDENCE_RANK = {CONFIDENCE_HIGH: 3, CONFIDENCE_MEDIUM: 2, CONFIDENCE_LOW: 1}
    min_rank = _CONFIDENCE_RANK.get(min_confidence, 1)

    def _add(ref: Reference) -> None:
        span = (ref.span_start, ref.span_end)
        if span in seen_spans:
            return
        seen_spans.add(span)
        if _CONFIDENCE_RANK.get(ref.confidence, 0) >= min_rank:
            refs.append(ref)

    # 1. Section references (with optional subsection chain)
    for m in _SECTION_PAT.finditer(text):
        sec = m.group(1)
        sub_chain_raw = m.group(2) or ""
        all_subs = [s.strip() for s in re.findall(r"\(([^()]*)\)", sub_chain_raw) if s.strip()]
        subsection = all_subs[:1] if all_subs else []
        clause = all_subs[1:] if len(all_subs) > 1 else []
        confidence = CONFIDENCE_HIGH if subsection else CONFIDENCE_MEDIUM
        # Trim trailing whitespace from match end
        end = m.end()
        while end > m.start() and text[end - 1].isspace():
            end -= 1
        _add(Reference(
            act=act_hint,
            section=sec,
            subsection=subsection,
            clause=clause,
            relation=None,
            confidence=confidence,
            span_start=m.start(),
            span_end=end,
            raw=text[m.start():end],
        ))

    # 2. Rule references
    for m in _RULE_PAT.finditer(text):
        _add(Reference(
            act=act_hint, rule=m.group(2), confidence=CONFIDENCE_MEDIUM,
            span_start=m.start(), span_end=m.end(), raw=m.group(0),
        ))

    # 3. Schedule references
    for m in _SCHEDULE_PAT.finditer(text):
        _add(Reference(
            act=act_hint, schedule=m.group(1), confidence=CONFIDENCE_MEDIUM,
            span_start=m.start(), span_end=m.end(), raw=m.group(0),
        ))

    # 4. Chapter references
    for m in _CHAPTER_PAT.finditer(text):
        _add(Reference(
            act=act_hint, chapter=m.group(1), confidence=CONFIDENCE_MEDIUM,
            span_start=m.start(), span_end=m.end(), raw=m.group(0),
        ))

    # 5. Textual relation patterns (LOW confidence — no explicit section)
    for m in _RELATION_RE.finditer(text):
        matched_text = m.group(0)
        relation_name = None
        for pat, name in _RELATION_PATTERNS:
            if matched_text.lower() == pat.lower():
                relation_name = name
                break
        if relation_name:
            _add(Reference(
                act=act_hint, relation=relation_name,
                confidence=CONFIDENCE_LOW,
                span_start=m.start(), span_end=m.end(),
                raw=matched_text,
            ))

    refs.sort(key=lambda r: r.span_start)
    return refs


def high_confidence_refs(refs: list[Reference]) -> list[Reference]:
    """Filter to only HIGH-confidence references (explicit section + subsection)."""
    return [r for r in refs if r.confidence == CONFIDENCE_HIGH]


def resolve_ref_to_provision(ref: Reference) -> str | None:
    """Attempt to resolve a Reference to a provision_id.

    Uses the legal section registry (advisory only — never fabricates).
    Returns ``None`` when the reference can't be confidently resolved.
    """
    if not ref.section:
        return None
    try:
        from app.rag.retrieval.legal_sections import is_known_section_for_act

        base = section_base(ref.section)
        if ref.act and is_known_section_for_act(base, ref.act):
            # Build a provisional provision_id — advisory, not authoritative
            return f"{ref.act}::{base}"
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #


def _reference_extraction_enabled() -> bool:
    """Check if reference extraction is enabled via env / Flask config."""
    try:
        from flask import current_app

        if current_app:
            return bool(current_app.config.get("ENABLE_REFERENCE_EXTRACTION", True))
    except Exception:
        pass
    import os

    return os.environ.get("ENABLE_REFERENCE_EXTRACTION", "true").lower() != "false"


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    text = "Section 31(2)(a) of the Act shall not apply. Subject to Section 55, this provision is read with Section 73."
    refs = extract_references(text, act_hint="Food Safety and Standards Act, 2006")

    assert len(refs) >= 3, f"Expected >=3 refs, got {len(refs)}: {[r.canonical_ref() for r in refs]}"

    section_refs = [r for r in refs if r.section]
    assert any(r.section == "31" and r.subsection == ["2"] and r.clause == ["a"] for r in section_refs), \
        f"Expected Section 31(2)(a), got: {[(r.section, r.subsection, r.clause) for r in section_refs]}"
    assert any(r.section == "55" for r in section_refs), \
        f"Expected Section 55, got: {[(r.section, r.subsection) for r in section_refs]}"

    high = high_confidence_refs(refs)
    assert all(r.confidence == CONFIDENCE_HIGH for r in high if r.section)

    relation_refs = [r for r in refs if r.relation]
    assert any(r.relation == "subject_to" for r in relation_refs), \
        f"Expected 'subject_to' relation, got: {[r.relation for r in relation_refs]}"

    print("Self-check passed. References found:")
    for r in refs:
        print(f"  {r.confidence}: {r.canonical_ref()}")
