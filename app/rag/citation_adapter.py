"""Citation extractor adapter (Agent A, Phase 2 — Day 6, §4).

Adapts the R1 ``CitationExtractor`` (``legal_paragraph_detection_engine`` —
fixed per §2.3, so full statute names are captured and ``"of the Act"`` is
never emitted) into the §5.1 payload ``citations`` list and the §5.2
``LegalChunk.citations`` JSON shape::

    Qdrant payload  ->  ["Section 55", "Food Safety and Standards Act", ...]
    LegalChunk JSON ->  [{"section": "55", "type": "section"}, ...]

``enrich_chunk`` re-runs extraction per chunk text and sets ``chunk.citations``
— the §5.1 payload-shape list of plain reference strings (matching the
``Chunk`` dataclass field).  Use :meth:`structured_citations` when writing the
§5.2 ``LegalChunk.citations`` JSON column instead.
(§5.1 ``references`` / cross-references are the Day 7 ``CrossRef`` adapter's
job and are intentionally left untouched.)

The extractor is injectable (mock-injection pattern) and imported lazily.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExtractedCitation:
    """One adapted citation (engine ``LegalCitation`` -> payload shape)."""

    citation_type: str  # e.g. "section", "statutory", "supreme_court", ...
    reference: str  # normalized text, e.g. "Section 55" / statute name / "2020 SC 123/456"
    details: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_type": self.citation_type,
            "reference": self.reference,
            "details": dict(self.details),
            "confidence": round(self.confidence, 4),
        }


class CitationAdapter:
    """Map :class:`CitationExtractor` output onto §5.1/§5.2 citation fields.

    Args:
        extractor: Optional pre-built ``CitationExtractor`` (injected for
            tests; the real one is built lazily).
    """

    def __init__(self, extractor: Any | None = None) -> None:
        self._extractor = extractor

    # ------------------------------------------------------------------ #
    # Lazy accessor
    # ------------------------------------------------------------------ #

    def _get_extractor(self) -> Any:
        if self._extractor is None:
            from legal_paragraph_detection_engine import CitationExtractor

            self._extractor = CitationExtractor()
        return self._extractor

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def extract(self, text: str) -> list[ExtractedCitation]:
        """Extract + adapt all citations, de-duplicated and in order."""
        citations = self._get_extractor().extract_citations(text)
        seen: set[tuple[str, str]] = set()
        adapted: list[ExtractedCitation] = []
        for citation in citations:
            raw_type = citation.citation_type
            if isinstance(raw_type, str):
                citation_type = raw_type.lower()
            else:
                citation_type = str(getattr(raw_type, "name", "")).lower()
            reference = str(getattr(citation, "normalized_text", "") or "").strip()
            key = (citation_type, reference.lower())
            if not reference or key in seen:
                continue
            seen.add(key)
            adapted.append(
                ExtractedCitation(
                    citation_type=citation_type,
                    reference=reference,
                    details=dict(getattr(citation, "details", None) or {}),
                    confidence=float(getattr(citation, "confidence", 0.0) or 0.0),
                )
            )
        return adapted

    def payload_citations(self, text: str) -> list[str]:
        """§5.1 ``citations`` payload — plain reference strings."""
        return [c.reference for c in self.extract(text)]

    def structured_citations(self, text: str) -> list[dict[str, Any]]:
        """§5.2 ``LegalChunk.citations`` JSON shape ``[{"section","type"}]``."""
        result: list[dict[str, Any]] = []
        for c in self.extract(text):
            section = (
                c.details.get("section_reference")
                or c.details.get("statute_name")
                or c.details.get("case_number")
                or c.reference
            )
            result.append(
                {
                    "section": section,
                    "type": c.citation_type,
                    "confidence": round(c.confidence, 4),
                }
            )
        return result

    def enrich_chunk(self, chunk: Any) -> Any:
        """Set ``chunk.citations`` from the chunk's own text; return the chunk."""
        text = str(getattr(chunk, "chunk_text", "") or "")
        if text and hasattr(chunk, "citations"):
            chunk.citations = self.payload_citations(text)
        return chunk


# End of citation_adapter.py
