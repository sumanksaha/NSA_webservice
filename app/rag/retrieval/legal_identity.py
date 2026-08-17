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

# --------------------------------------------------------------------------- #
# Section / sub-structure parsing
# --------------------------------------------------------------------------- #

#: Matches a section reference with optional subsection/clause chains.
#: Captures: section number, optional "(sub)" groups.
_SECTION_FULL_RE = re.compile(
    r"(?:section|sec\.|s\.|u/s)\s*(\d{1,4})"
    r"((?:\s*\(\s*\w+[^()]*\))*)\s*",
    re.IGNORECASE,
)

#: Matches the full canonical act::section::subsection chain from a text
#: snippet (e.g. "FSS Act, 2006, Section 31(2)(a)").
_FULL_REF_RE = re.compile(
    r"(.*?)\s*,\s*(?:Section\s+(\d+)((?:\s*\([^()]+\))*))",
    re.IGNORECASE,
)


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
    # Raw section string as it appears (e.g. "31(2)(a)")
    raw_section: str | None = None

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
        }


# --------------------------------------------------------------------------- #
# Parsing functions
# --------------------------------------------------------------------------- #


def _parse_subsection_chain(s: str) -> list[str]:
    """Parse ``"(2)(a)(iii)"`` → ``["2", "a", "iii"]``."""
    return re.findall(r"\(([^()]+)\)", s)


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

    # Section parsing from chunk's section_number field, falling back to text
    section_number = getattr(chunk, "section_number", None) or ""
    identity.raw_section = section_number if section_number else None

    # Try to parse section from the section_number field first
    q_sec, _ = (None, None)
    try:
        from app.rag.retrieval.identifier import detect_section

        q_sec, _ = detect_section(section_number) if section_number else (None, None)
    except Exception:
        pass

    if q_sec:
        identity.section = q_sec
        # Parse subsections from the raw section string
        if section_number:
            subs = _parse_subsection_chain(section_number)
            if subs:
                if len(subs) == 1:
                    identity.subsection = subs
                else:
                    identity.subsection = subs[:1]
                    identity.clause = subs[1:]
    else:
        # Fall back to text-level parsing of section_number as text
        text = section_number or ""
        m = re.match(r"(\d{1,4})", text)
        if m:
            identity.section = m.group(1)
            identity.raw_section = section_number
            subs = _parse_subsection_chain(text)
            if subs:
                if len(subs) == 1:
                    identity.subsection = subs
                else:
                    identity.subsection = subs[:1]
                    identity.clause = subs[1:]

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

    return identity


# Backward-compatible alias
def chunk_identity(chunk: Any) -> LegalIdentity:
    """Alias for :func:`parse_legal_identity`."""
    return parse_legal_identity(chunk)


# --------------------------------------------------------------------------- #
# Feature flags
# --------------------------------------------------------------------------- #


def _legal_identity_enabled() -> bool:
    """Check if legal identity parsing is enabled via env / Flask config."""
    try:
        from flask import current_app

        if current_app:
            return bool(current_app.config.get("ENABLE_LEGAL_IDENTITY", True))
    except Exception:
        pass
    import os

    return os.environ.get("ENABLE_LEGAL_IDENTITY", "true").lower() != "false"


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
