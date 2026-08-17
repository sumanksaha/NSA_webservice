"""Chunk data model + LegalParagraphEngine → Chunk adapter (Agent A, Phase 1).

The :class:`Chunk` dataclass mirrors the Qdrant payload schema defined in
``RAG_AGENT_A_SCOPE.md`` §5.1 so Agent B's :class:`RetrievedChunk`
(``app/rag/retrieval/result.py``) can consume the corpus without
transformation.  The :class:`Chunker` adapts ``LegalParagraphEngine``
paragraph output (via ``app.services.legal_engine``) into :class:`Chunk`
objects.

§2.3 note: the paragraph engine's ``hierarchy_depth`` maps directly to the
payload ``hierarchy_level``; subsection-marker chains (``(1)(a)``) carry no
section number and are surfaced via ``subsection``.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.rag.entity_extractor import _plain_entity_names

logger = logging.getLogger(__name__)

#: Payload keys indexed/filterable in Qdrant — see §5.1 of RAG_AGENT_A_SCOPE.md.
PAYLOAD_INDEX_FIELDS: tuple[str, ...] = (
    "document_id",
    "document_uri",
    "document_type",
    "authority",
    "jurisdiction",
    "state",
    "is_current",
    "chunk_index",
    "section_number",
    "section_title",
    "subsection",
    "clause_number",
    "hierarchy_level",
)


@dataclass
class Chunk:
    """A single indexed chunk — mirrors the Qdrant payload schema (§5.1).

    ``chunk_id`` is the Qdrant point id (uuid4 hex string); it is also
    included in the payload as ``chunk_id`` for convenience.
    """

    chunk_id: str
    document_id: str
    chunk_index: int
    chunk_text: str
    chunk_char_count: int = 0
    word_count: int = 0
    document_uri: str = ""
    document_title: str = ""
    document_type: str = ""
    authority: str = ""
    jurisdiction: str = ""
    state: str = ""
    #: Owning Act name (e.g. "Air (Prevention and Control of Pollution) Act,
    #: 1981") — multi-domain stamp from the ingestion manifest (Phase 1).
    act_name: str = ""
    effective_date: str | None = None
    enactment_date: str | None = None
    amended_date: str | None = None
    is_current: bool = True
    section_number: str | None = None
    section_title: str | None = None
    subsection: str | None = None
    #: Leading dotted regulatory clause number (``2.4.15``, ``3.04``) — the
    #: FSSAI-regulation / rules-style numbering that the parenthetical
    #: ``subsection`` regex cannot see (G6, 2026-08-17).  Semantically distinct
    #: from ``subsection`` (clause numbers are NOT section subsections), so it
    #: lives in its own payload field.
    clause_number: str | None = None
    hierarchy_level: int = 0
    parent_chunk_id: str | None = None
    citations: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    #: Legal entities named in this chunk (§3.4) — plain entity names in the
    #: payload (dual shape mirrors ``citations``/``references``; the structured
    #: ``[{name, type, confidence}]`` form lives in §5.2 ``LegalChunk.entities``).
    entities: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = ""
    embedding_model: str = ""
    #: SHA-256 of the normalized chunk text (Agent A Day 5 dedup; §5.2
    #: ``LegalChunk.content_hash``).  Empty until the deduper stamps it.
    content_hash: str = ""
    #: Every in-range section header found in the chunk text (any-position,
    #: paren-tolerant) — lets multi-section / section-index chunks resolve
    #: against any covered section (V7-gap fix, 2026-08-13; written by
    #: ``_l4_section_headers`` and the backfill's L4 layer).
    sections_covered: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe Qdrant payload dict (§5.1 schema)."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document_uri": self.document_uri,
            "document_title": self.document_title,
            "document_type": self.document_type,
            "authority": self.authority,
            "jurisdiction": self.jurisdiction,
            "state": self.state,
            "act_name": self.act_name,
            "effective_date": self.effective_date,
            "enactment_date": self.enactment_date,
            "amended_date": self.amended_date,
            "is_current": self.is_current,
            "chunk_index": self.chunk_index,
            "chunk_text": self.chunk_text,
            "chunk_char_count": self.chunk_char_count,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "subsection": self.subsection,
            "clause_number": self.clause_number,
            "hierarchy_level": self.hierarchy_level,
            "parent_chunk_id": self.parent_chunk_id,
            "citations": list(self.citations),
            "references": list(self.references),
            "entities": list(self.entities),
            "confidence": round(float(self.confidence), 6),
            "created_at": self.created_at,
            "embedding_model": self.embedding_model,
            "content_hash": self.content_hash,
            "sections_covered": list(self.sections_covered),
        }

    @classmethod
    def from_paragraph(
        cls,
        paragraph: dict[str, Any],
        document: dict[str, Any] | None = None,
        chunk_index: int = 0,
        parent_chunk_id: str | None = None,
        embedding_model: str = "",
    ) -> Chunk:
        """Build a :class:`Chunk` from one engine paragraph dict.

        Args:
            paragraph: One paragraph dict from ``LegalParagraphEngine``
                ``process_document`` output.
            document: Optional document-level metadata (``document_id``,
                ``document_uri``, ``title``, ``type``, ``authority``,
                ``jurisdiction``, ``state``, ``effective_date``,
                ``enactment_date``, ``amended_date``, ``is_current``).
            chunk_index: Sequential index of this chunk within the document.
            parent_chunk_id: Chunk id of the parent paragraph (chunk hierarchy).
            embedding_model: Embedding model name stamped on the payload.
        """
        doc = document or {}
        text = paragraph.get("text", "")
        section = paragraph.get("section")
        # L4 fallback (2026-08-13): when the engine did not surface a section,
        # stamp from any-position, act-range-validated headers in the chunk
        # text (the rule that closed the V7 candidate gap).  The engine's own
        # section stays authoritative when present; ``sections_covered`` is
        # always recorded so multi-section chunks resolve against any covered
        # section.
        act_name = doc.get("act_name") or ""
        covered = _l4_section_headers(text, act_name)
        if not section and covered:
            section = covered[0]
        return cls(
            chunk_id=str(uuid.uuid4()),
            document_id=str(doc.get("document_id") or uuid.uuid4()),
            chunk_index=chunk_index,
            chunk_text=text,
            chunk_char_count=len(text),
            word_count=int(paragraph.get("word_count", 0)),
            document_uri=doc.get("document_uri", ""),
            document_title=doc.get("title") or doc.get("document_title") or "",
            document_type=paragraph.get("document_type") or doc.get("type") or "unknown",
            authority=doc.get("authority", ""),
            jurisdiction=doc.get("jurisdiction", ""),
            state=doc.get("state", ""),
            act_name=doc.get("act_name") or "",
            effective_date=_as_iso(doc.get("effective_date")),
            enactment_date=_as_iso(doc.get("enactment_date")),
            amended_date=_as_iso(doc.get("amended_date")),
            is_current=bool(doc.get("is_current", True)),
            section_number=str(section) if section else None,
            sections_covered=covered,
            section_title=_extract_section_title(text),
            subsection=_extract_subsection_markers(text),
            clause_number=_extract_clause_number(text),
            hierarchy_level=int(paragraph.get("hierarchy_depth", 0) or 0),
            parent_chunk_id=parent_chunk_id,
            citations=[c.get("reference", "") for c in (paragraph.get("citations") or []) if c.get("reference")],
            references=list(doc.get("references") or []),
            entities=_plain_entity_names(doc.get("entities") or []),
            confidence=float((paragraph.get("confidence_scores") or {}).get("overall", 0.0) or 0.0),
            created_at=datetime.now(UTC).isoformat(),
            embedding_model=embedding_model,
        )


#: Any-position section-header pattern (paren-tolerant) — the L4 rule from the
#: V7-gap backfill (2026-08-13).  Catches mid-line statute headers that the
#: LegalParagraphEngine's line-anchored detection misses, e.g.
#: ``…coercion. 73. Compensation for loss…`` and ``45. ( I) The West Bengal…``.
_L4_HEADER_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,4})\s*\.\s*(?:\(\s*)?[A-Z]")


def _l4_section_headers(text: str, act_name: str | None) -> list[str]:
    """All section headers in *text* that fall inside the act's known range.

    Fail-closed: when the act is not in ``app.rag.legal_sections``
    ``ACT_SECTION_RANGES``, returns ``[]`` (an unknown act must never be
    guessed).  Mirrors the backfill's L4 layer so ingestion and remediation
    stamp identically.
    """
    from app.rag.legal_sections import sections_for_act

    known = sections_for_act(act_name)
    if not known:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _L4_HEADER_RE.finditer(text or ""):
        sec = m.group(1)
        if sec in known and sec not in seen:
            seen.add(sec)
            out.append(sec)
    return out


class Chunker:
    """Adapt ``LegalParagraphEngine`` paragraph output into :class:`Chunk`\\ s.

    Args:
        engine: Optional pre-built ``LegalParagraphEngine`` instance (injected
            for tests); when omitted one is created lazily via
            ``app.services.legal_engine.get_legal_engine``.
        embedding_model: Model name to stamp on chunks; when omitted the
            ``RAG_EMBEDDING_MODEL`` config value is used if available.
    """

    def __init__(self, engine: Any | None = None, embedding_model: str | None = None) -> None:
        self._engine = engine
        self._embedding_model = embedding_model

    @property
    def embedding_model(self) -> str:
        """Resolve the embedding-model stamp, reading config lazily."""
        if self._embedding_model:
            return self._embedding_model
        try:
            from flask import current_app

            return current_app.config.get("RAG_EMBEDDING_MODEL", "")
        except Exception:
            return ""

    def _get_engine(self) -> Any:
        """Return the (cached) LegalParagraphEngine instance."""
        if self._engine is None:
            from app.services.legal_engine import get_legal_engine

            self._engine = get_legal_engine()()
        return self._engine

    def chunk_text(self, text: str, document: dict[str, Any] | None = None) -> list[Chunk]:
        """Chunk legal *text* into :class:`Chunk` objects.

        Args:
            text: Clean legal document text.
            document: Optional document-level metadata passed through to each
                chunk's payload (see :meth:`Chunk.from_paragraph`).

        Returns:
            List of chunks in document order; empty for empty/blank input.
        """
        if not text or not text.strip():
            return []

        doc = document or {}
        doc_type_info = {"type": doc["type"]} if doc.get("type") else None
        paragraphs = self._get_engine().process_document(text, doc_type_info)
        embedding_model = self.embedding_model

        chunks: list[Chunk] = []
        chunk_by_paragraph: dict[str, str] = {}
        for paragraph in paragraphs:
            chunk = Chunk.from_paragraph(
                paragraph,
                doc,
                chunk_index=len(chunks),
                embedding_model=embedding_model,
            )
            chunk_by_paragraph[paragraph.get("paragraph_id", "")] = chunk.chunk_id
            chunks.append(chunk)

        # Wire chunk hierarchy from the engine's parent_id links (second pass
        # so parent chunks are always registered first).
        for paragraph, chunk in zip(paragraphs, chunks, strict=True):
            parent_id = paragraph.get("parent_id")
            if parent_id and parent_id in chunk_by_paragraph:
                chunk.parent_chunk_id = chunk_by_paragraph[parent_id]

        return chunks


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _as_iso(value: Any) -> str | None:
    """Coerce a date/datetime/str into an ISO-8601 string (or None)."""
    if value is None or value == "":
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _extract_section_title(text: str) -> str | None:
    """Extract a section title from ``"Section N: Title"``-style text."""
    match = re.match(r"^\s*(?:Section|Sec\.|§)\s*\d+\s*[:\-]?\s*(.+)$", text, re.IGNORECASE)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return None


def _extract_subsection_markers(text: str) -> str | None:
    """Extract a leading subsection-marker chain, e.g. ``(1)(a)``.

    Marker chains carry no section number of their own (§2.3) — they are
    surfaced here so the payload's ``subsection`` field is filterable.
    """
    match = re.match(r"^(?:\d+\s*)?((?:\([^()]*\))+)", text)
    if match:
        return match.group(1)
    return None


#: Leading dotted regulatory clause-number pattern (G6, 2026-08-17).
#:
#: Matches ``2.4.15`` / ``3.04`` / ``5.2.4`` style clause numbers that anchor
#: FSSAI-regulation / rules paragraphs, validated against the 27,345-chunk
#: corpus: the guard ``(?=\s*[A-Z])`` (uppercase letter after optional space)
#: excludes the false positives the bare dotted pattern would capture —
#: measurements (``0.75 g-1.25 g``), dates (``23.3.2001``, ``22.12.1997``),
#: standalone numbers (``0.001``), ranges (``6.5-7.5``) and OCR residue
#: (``1.2 1.2 1.2``).  Segments after the first are capped at 2 digits so a
#: 4-digit year can never be consumed as a clause segment.
_DOTTED_CLAUSE_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,2}){1,3})(?=(?:\s*:\s*|\s*)[A-Z])")


def _extract_clause_number(text: str) -> str | None:
    """Extract a leading dotted regulatory clause number, e.g. ``2.4.15``.

    Returns ``None`` for anything the guard rejects (dates, measurements,
    bare numbers) — ``clause_number`` must never be polluted with a value
    that is not a clause identifier.
    """
    match = _DOTTED_CLAUSE_RE.match(text or "")
    return match.group(1) if match else None


# End of chunker.py
