"""Canonical legal identity representation for legal chunks.

This module provides a normalized identity for every legal provision/chunk,
independent of the CE reranking experiment.  It parses the structured
identifier fields already available on ``RetrievedChunk`` (``act_name``,
``section_number``, ``document_title``, ``authority``, ``document_type``)
together with text-level section/subsection parsing to build a canonical
identity.  Identity fields are never fabricated — missing fields remain
``None``/empty.

Canonical identifier form:

    ACT::SECTION::SUBSECTION::CLAUSE

Example: ``"Food Safety and Standards Act, 2006::3::26(2)::(ii)"``

The identifier is lossy by design — only fields that are actually parsed
or present in the chunk payload are included.  Unknown components are
omitted rather than fabricated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.rag.retrieval.legal_hierarchy import parse_section_chain
from app.shared.config import cfg

# --------------------------------------------------------------------------- #
# Section / sub-structure parsing
# --------------------------------------------------------------------------- #

#: Leading section keyword in a section_number field ("Section 31(2)" → "31(2)").
_LEADING_SECTION_KW_RE = re.compile(r"^\s*(?:section|sec\.|s\.|u/s)\s+", re.IGNORECASE)

#: Cross-reference mentions inside provision text ("subject to Section 32").
_CROSS_REF_RE = re.compile(r"(?:section|sec\.|s\.|u/s)\s+(\d{1,4}[a-z]?)", re.IGNORECASE)

#: Rule / Schedule mentions ("under Rule 5", "Schedule 2") — kept distinct
#: from section numbers so section-family logic never confuses them.
_RULE_REF_RE = re.compile(r"\brule\s+(\d{1,4}[a-z]?)", re.IGNORECASE)
_SCHEDULE_REF_RE = re.compile(r"\bschedule\s+([a-z0-9\-]+)", re.IGNORECASE)

#: Provision-type detectors in priority order (roadmap §6).  First match wins —
#: provisos/exceptions override the operative rule they qualify, and "shall not"
#: must beat bare "shall".  Short markers are word-boundary regexes so plain
#: prose ("exceptional", "maybe") never counts as operative language.
#: Keyword lists stay local: sibling seams (evidence_selector,
#: answer_error_taxonomy) check context-vs-answer on both sides, while this
#: module classifies a single chunk.
_PROVISION_TYPE_RULES: tuple[tuple[str, tuple[Any, ...]], ...] = (
    (
        "exception",
        (
            "provided that",
            "provided further",
            "notwithstanding",
            re.compile(r"\bexcept\b"),
            "save as",
            "proviso",
            "does not apply",
            "shall not apply",
            "exempt",
        ),
    ),
    ("definition", ('"means"', "'means'", "definition", "for the purposes of", "shall have the meaning")),
    ("penalty", ("penalty", "fine", "imprisonment", "punishment", "imprison")),
    ("prohibition", (re.compile(r"\bshall not\b"), "no person shall", "prohibited", "prohibition")),
    ("permission", (re.compile(r"\bmay\b"),)),
    ("obligation", (re.compile(r"\bshall\b"),)),
    ("procedure", ("procedure", "appeal", "hearing", "tribunal")),
    ("authority", ("authority", "power to")),
    ("scope", ("applies to", "extends to", "scope")),
    ("condition", ("on condition that", "conditional upon", "subject to the condition")),
)

_EXCEPTION_PATTERNS: tuple[Any, ...] = (
    "provided that",
    "notwithstanding",
    re.compile(r"\bexcept\b"),
    "subject to",
    re.compile(r"\bunless\b"),
    "proviso",
    "exempt",
)
_DEFINITION_MARKERS = ('"means"', "'means'", "definition", "for the purposes of", "shall have the meaning")

#: Quoted term followed by bare "means" ('Food' means ...) — the classic
#: statutory definition shape.  Kept as a regex (not a substring) so plain
#: prose like "this means the result" does not count as a definition.
_QUOTED_MEANS_RE = re.compile(r"""['"][^'"]{1,60}['"]\s+means\b""")


@dataclass
class LegalIdentity:
    """Normalized legal identity for a legal chunk.

    All fields are optional — only populated when parseable from the chunk
    text or payload.  The canonical identifier is built from non-None fields.
    """

    act: str | None = None
    act_alias: str | None = None
    chapter: str | None = None
    part: str | None = None
    section: str | None = None
    subsection: list[str] = field(default_factory=list)
    clause: list[str] = field(default_factory=list)
    rule: str | None = None
    schedule: str | None = None
    authority: str | None = None
    jurisdiction: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    status: str | None = None
    source_document: str | None = None
    version: str | None = None
    # Document type as stamped on the chunk (Act / Rule / Schedule / ...).
    document_type: str | None = None
    # Raw section string as it appears (e.g. "31(2)(a)")
    raw_section: str | None = None
    # --- Phase 2 legal-unit enrichment (roadmap §6) ---
    # Provision type vocabulary: definition / obligation / prohibition /
    # permission / exception (incl. proviso and exemption) / condition /
    # procedure / authority / penalty / scope.  None when the text is generic.
    provision_type: str | None = None
    # Canonical id of the parent unit ("ACT::31(2)" for "ACT::31(2)(a)").
    parent_unit: str | None = None
    # Child canonical ids — empty at single-chunk parse time; the
    # corpus-wide grouping layer populates these.
    child_units: list[str] = field(default_factory=list)
    # Section numbers cross-referenced by the provision text (e.g. ["32"]),
    # plus Rule / Schedule mentions as "Rule 5" / "Schedule 2".
    cross_references: list[str] = field(default_factory=list)
    # Marker flags (word-boundary matched, never fabricated).
    has_definition: bool = False
    has_exception: bool = False
    has_cross_reference: bool = False

    def canonical_id(self) -> str:
        """Build a canonical identifier from available fields.

        Format: ``ACT::SECTION::SUBSECTION::CLAUSE`` where unavailable
        components are omitted (double-colon separators collapse).
        Never fabricates parts.
        """
        parts: list[str] = []
        if self.act:
            parts.append(self.act)
        if self.section:
            if self.subsection or self.clause:
                chain = self.section
                for s in self.subsection:
                    chain += f"({s})"
                for c in self.clause:
                    chain += f"({c})"
                parts.append(chain)
            else:
                parts.append(self.section)
        elif self.rule:
            parts.append(f"Rule {self.rule}")
        elif self.schedule:
            parts.append(f"Schedule {self.schedule}")
        elif self.chapter:
            parts.append(f"Chapter {self.chapter}")
        if not parts:
            return "UNKNOWN"
        return "::".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "act": self.act,
            "act_alias": self.act_alias,
            "chapter": self.chapter,
            "part": self.part,
            "section": self.section,
            "subsection": self.subsection,
            "clause": self.clause,
            "rule": self.rule,
            "schedule": self.schedule,
            "authority": self.authority,
            "jurisdiction": self.jurisdiction,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "status": self.status,
            "source_document": self.source_document,
            "version": self.version,
            "raw_section": self.raw_section,
            "canonical_id": self.canonical_id(),
            "provision_type": self.provision_type,
            "parent_unit": self.parent_unit,
            "child_units": list(self.child_units),
            "cross_references": list(self.cross_references),
            "has_definition": self.has_definition,
            "has_exception": self.has_exception,
            "has_cross_reference": self.has_cross_reference,
        }


def _marker_hit(marker: Any, lowered: str, raw: str) -> bool:
    if isinstance(marker, re.Pattern):
        return marker.search(raw.lower()) is not None
    return marker in lowered


def detect_provision_type(text: str) -> str | None:
    """Classify a provision by its operative language (first rule wins)."""
    low = (text or "").lower()
    for provision_type, markers in _PROVISION_TYPE_RULES:
        if any(_marker_hit(m, low, text or "") for m in markers):
            return provision_type
    if _QUOTED_MEANS_RE.search(text or ""):
        return "definition"
    return None


def _parent_canonical_id(act: str | None, chain: list[str]) -> str | None:
    """Canonical id of the parent unit, or None at the top rung."""
    if len(chain) < 2:
        return None
    parent_chain = chain[:-1]
    section = parent_chain[0]
    for part in parent_chain[1:]:
        section += f"({part})"
    return f"{act}::{section}" if act else section


# --------------------------------------------------------------------------- #
# Parsing functions
# --------------------------------------------------------------------------- #


def _resolve_act_alias(act_name: str | None, document_title: str | None) -> str | None:
    """Resolve the canonical Act from act_name or document_title.

    Reuses the existing vocabulary in ``identifier`` (CANONICAL_ACTS / aliases).
    """
    if not act_name and not document_title:
        return None
    text = f"{act_name or ''} {document_title or ''}"
    try:
        from app.rag.retrieval.identifier import detect_act

        result = detect_act(text)
        if result:
            return result
    except Exception:
        pass
    return act_name or None


def parse_legal_identity(chunk: Any) -> LegalIdentity:
    """Parse a legal identity from a ``RetrievedChunk`` (or chunk-like object).

    Uses the existing ``identifier`` module vocabulary (detect_act, detect_section)
    and the chunk's payload fields.  Missing fields stay ``None`` — no
    fabrication.
    """
    identity = LegalIdentity()

    # Act / Act alias
    act_name = getattr(chunk, "act_name", None) or ""
    document_title = getattr(chunk, "document_title", None) or ""
    authority = getattr(chunk, "authority", None) or ""

    identity.act = _resolve_act_alias(act_name or None, document_title or None)
    identity.act_alias = act_name if act_name and act_name != identity.act else None

    # Section parsing from chunk's section_number field, falling back to text.
    # Chain splitting has one home — ``legal_hierarchy.parse_section_chain``
    # — so base/subsection/clause can never disagree with the hierarchy view.
    # A leading section keyword ("Section 31(2)") is stripped first; the
    # field is a section ref by construction, so no keyword check is needed.
    section_number = getattr(chunk, "section_number", None) or ""
    identity.raw_section = section_number if section_number else None

    bare = _LEADING_SECTION_KW_RE.sub("", section_number)
    chain = parse_section_chain(bare)
    if chain:
        identity.section = chain[0]
        if len(chain) > 1:
            identity.subsection = chain[1:2]
            identity.clause = chain[2:]

    # Document-type inference (Rule, Schedule, Chapter)
    doc_type = getattr(chunk, "document_type", None) or ""
    text_lower = (document_title + " " + (getattr(chunk, "text", "") or "")).lower()

    if "schedule" in text_lower or "schedule" in doc_type.lower():
        sched_m = re.search(r"schedule\s+([a-z0-9\-]+)", text_lower)
        identity.schedule = sched_m.group(1) if sched_m else None

    if "rule" in doc_type.lower() and "rule" in text_lower:
        rule_m = re.search(r"rule\s+([0-9]+)", text_lower)
        identity.rule = rule_m.group(1) if rule_m else None

    if "chapter" in text_lower:
        ch_m = re.search(r"chapter\s+([a-z0-9\-]+)", text_lower)
        identity.chapter = ch_m.group(1) if ch_m else None

    if "part" in text_lower:
        part_m = re.search(r"part\s+([a-z0-9\-]+)", text_lower)
        identity.part = part_m.group(1) if part_m else None

    # Authority / jurisdiction / source
    identity.authority = authority if authority else None
    identity.source_document = document_title if document_title else None
    identity.document_type = doc_type if doc_type else None

    # --- Phase 2 enrichment: hierarchy links + operative signals ---
    identity.parent_unit = _parent_canonical_id(identity.act, chain)
    chunk_text = getattr(chunk, "text", "") or ""
    if isinstance(chunk, dict):
        chunk_text = chunk.get("text", "") or ""
    identity.provision_type = detect_provision_type(chunk_text)
    low_text = chunk_text.lower()
    identity.has_exception = any(_marker_hit(m, low_text, chunk_text) for m in _EXCEPTION_PATTERNS)
    identity.has_definition = any(m in low_text for m in _DEFINITION_MARKERS) or bool(
        _QUOTED_MEANS_RE.search(chunk_text)
    )
    identity.cross_references = sorted(
        set(_CROSS_REF_RE.findall(chunk_text))
        | {f"Rule {r}" for r in _RULE_REF_RE.findall(chunk_text)}
        | {f"Schedule {s}" for s in _SCHEDULE_REF_RE.findall(chunk_text)}
    )
    identity.has_cross_reference = bool(identity.cross_references)

    return identity


# Backward-compatible alias
def chunk_identity(chunk: Any) -> LegalIdentity:
    """Alias for :func:`parse_legal_identity`."""
    return parse_legal_identity(chunk)


# --------------------------------------------------------------------------- #
# Feature flags
# --------------------------------------------------------------------------- #


def _legal_identity_enabled() -> bool:
    """Check if legal identity parsing is enabled (shared config seam)."""
    enabled: bool = cfg.legal_identity
    return enabled


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Minimal self-check
    from dataclasses import dataclass

    @dataclass
    class FakeChunk:
        chunk_id: str
        score: float
        text: str
        section_number: str | None
        document_title: str
        act_name: str
        document_type: str
        authority: str
        hierarchy_level: int = 3
        parent_chunk_id: str | None = None
        chunk_index: int = 0

    c = FakeChunk(
        chunk_id="test",
        score=0.9,
        text="Some text about Section 31(2)(a)",
        section_number="31(2)(a)",
        document_title="Food Safety and Standards Act, 2006",
        act_name="Food Safety and Standards Act, 2006",
        document_type="Act",
        authority="FSSAI",
    )
    ident = parse_legal_identity(c)
    assert ident.section == "31", ident.to_dict()
    assert ident.subsection == ["2"], ident.to_dict()
    assert ident.clause == ["a"], ident.to_dict()
    assert "Food Safety" in ident.canonical_id()
