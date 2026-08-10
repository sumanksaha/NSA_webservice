"""Cross-reference adapter (Agent A, Phase 2 — Day 7, §4).

Adapts the R1 ``CrossReferenceEngine`` (``app/cross_reference``) into the
§5.1 payload ``references`` list and the §5.2 ``LegalChunk.references`` JSON
shape::

    Qdrant payload  ->  ["Section 55", "Annexure A", "paragraph 3", ...]
    LegalChunk JSON ->  [{"target": "Section 55", "kind": "section"}, ...]

Section "known-ness" uses the full FSS Act section set — ``FSS_ACT_SECTIONS``
(§1–104) reused from ``app.rag.retrieval.query_classifier`` — NOT the app's
``CrossReferenceEngine.KNOWN_SECTIONS`` (11 petition-relevant sections pinned
by ``test_cross_reference.py`` and ``xref_report.html``; scope §9 warning #6
is satisfied inside the RAG layer only, so no existing feature is disturbed).

``enrich_chunk`` re-runs extraction per chunk text and sets ``chunk.references``
(the §5.1 payload-shape list; use :meth:`structured_references` for the §5.2
``LegalChunk.references`` JSON column).

The engine is injectable (mock-injection pattern) and imported lazily.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AdaptedReference:
    """One adapted cross-reference (engine ``CrossReference`` -> payload shape)."""

    kind: str  # "section" | "annexure" | "paragraph"
    target: str  # e.g. "55", "A", "3"
    raw: str  # normalized text, e.g. "Section 55" / "Annexure A" / "paragraph 3"
    confidence: float = 0.0
    #: For section refs: whether the base section number is in the full FSS Act
    #: (§1–104). ``None`` for annexure/paragraph refs (not applicable).
    known: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "raw": self.raw,
            "confidence": round(self.confidence, 4),
            "known": self.known,
        }


class CrossRefAdapter:
    """Map :class:`CrossReferenceEngine` output onto §5.1/§5.2 reference fields.

    Args:
        engine: Optional pre-built ``CrossReferenceEngine`` (injected for
            tests; the real one is built lazily).
    """

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ #
    # Lazy accessor
    # ------------------------------------------------------------------ #

    def _get_engine(self) -> Any:
        if self._engine is None:
            from app.cross_reference.engine import CrossReferenceEngine

            self._engine = CrossReferenceEngine()
        return self._engine

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def extract(self, text: str) -> list[AdaptedReference]:
        """Extract + adapt all cross-references, in document order (engine dedupes)."""
        refs = self._get_engine().extract_references(text)
        adapted: list[AdaptedReference] = []
        for ref in refs:
            raw = str(getattr(ref, "raw", "") or "").strip()
            kind = str(getattr(getattr(ref, "kind", None), "value", "") or "")
            if not raw or not kind:
                continue
            target = str(getattr(ref, "target", "") or "")
            known = None
            if kind == "section":
                known = self.is_known_section(target)
            adapted.append(
                AdaptedReference(
                    kind=kind,
                    target=target,
                    raw=raw,
                    confidence=float(getattr(ref, "confidence", 0.0) or 0.0),
                    known=known,
                )
            )
        return adapted

    def payload_references(self, text: str) -> list[str]:
        """§5.1 ``references`` payload — plain raw strings."""
        return [r.raw for r in self.extract(text)]

    def structured_references(self, text: str) -> list[dict[str, Any]]:
        """§5.2 ``LegalChunk.references`` JSON shape ``[{"target", "kind"}]``."""
        return [
            {"target": r.raw, "kind": r.kind}
            for r in self.extract(text)
        ]

    def enrich_chunk(self, chunk: Any) -> Any:
        """Set ``chunk.references`` from the chunk's own text; return the chunk."""
        text = str(getattr(chunk, "chunk_text", "") or "")
        if text and hasattr(chunk, "references"):
            chunk.references = self.payload_references(text)
        return chunk

    # ------------------------------------------------------------------ #
    # Section knowledge (full FSS Act, RAG-layer only)
    # ------------------------------------------------------------------ #

    @property
    def known_sections(self) -> frozenset[str]:
        """Full FSS Act, 2006 section set (§1–104) — reused from Agent B."""
        from app.rag.retrieval.query_classifier import FSS_ACT_SECTIONS

        return FSS_ACT_SECTIONS

    def is_known_section(self, target: str) -> bool:
        """Whether a section reference (incl. ``"26(2)(ii)"``) names a real section.

        Sub-clause / marker suffixes are stripped so ``"26(2)(ii)"`` resolves
        against the base section number ``26``.
        """
        base = re.match(r"^\d{1,3}", str(target or "").strip())
        if not base:
            return False
        return base.group(0) in self.known_sections


# End of crossref_adapter.py
