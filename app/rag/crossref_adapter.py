"""Cross-reference adapter (Agent A, Phase 2 — Day 7, §4).

Adapts the R1 ``CrossReferenceEngine`` (``app/cross_reference``) into the
§5.1 payload ``references`` list and the §5.2 ``LegalChunk.references`` JSON
shape::

    Qdrant payload  ->  ["Section 55", "Annexure A", "paragraph 3", ...]
    LegalChunk JSON ->  [{"target": "Section 55", "kind": "section"}, ...]

Section "known-ness" is act-aware (Phase 1 — de-FSSAI): by default it uses
the full FSS Act section set — ``FSS_ACT_SECTIONS`` (§1–104) from
``app/rag/legal_sections.py`` — and when the chunk carries an ``act_name``
it resolves against that act's registered section range (unknown acts
report ``known=None`` rather than a false negative).  NOT the app's
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

from app.rag.legal_sections import FSS_ACT_SECTIONS, is_known_section_for_act

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

    def extract(self, text: str, act_name: str | None = None) -> list[AdaptedReference]:
        """Extract + adapt all cross-references, in document order (engine dedupes).

        Args:
            text: Chunk/document text to scan.
            act_name: Optional owning Act (payload ``act_name``); when given,
                the ``known`` flag resolves against that act's sections.
        """
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
                known = self.is_known_section(target, act_name=act_name)
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

    def payload_references(self, text: str, act_name: str | None = None) -> list[str]:
        """§5.1 ``references`` payload — plain raw strings."""
        return [r.raw for r in self.extract(text, act_name=act_name)]

    def structured_references(self, text: str) -> list[dict[str, Any]]:
        """§5.2 ``LegalChunk.references`` JSON shape ``[{"target", "kind"}]``."""
        return [
            {"target": r.raw, "kind": r.kind}
            for r in self.extract(text)
        ]

    def enrich_chunk(self, chunk: Any) -> Any:
        """Set ``chunk.references`` from the chunk's own text; return the chunk.

        The ``known`` flag is resolved against the chunk's ``act_name`` when
        present (Phase 1 — multi-domain), else the FSS Act default.
        """
        text = str(getattr(chunk, "chunk_text", "") or "")
        if text and hasattr(chunk, "references"):
            act_name = getattr(chunk, "act_name", None)
            chunk.references = self.payload_references(text, act_name=act_name)
        return chunk

    # ------------------------------------------------------------------ #
    # Section knowledge (act-aware; FSS default — Phase 1 de-FSSAI)
    # ------------------------------------------------------------------ #

    @property
    def known_sections(self) -> frozenset[str]:
        """Full FSS Act, 2006 section set (§1–104) — default when no act given."""
        return FSS_ACT_SECTIONS

    def is_known_section(self, target: str, act_name: str | None = None) -> bool | None:
        """Whether a section reference (incl. ``"26(2)(ii)"``) names a real section.

        Args:
            target: Section reference, e.g. ``"55"`` or ``"26(2)(ii)"``.
            act_name: Optional owning Act; when ``None`` the FSS Act default
                is used (backward compatible).  Unknown acts report ``None``
                instead of a false negative.

        Sub-clause / marker suffixes are stripped so ``"26(2)(ii)"`` resolves
        against the base section number ``26``.  Without an ``act_name`` the
        FSS Act default is used (backward compatible — existing callers pin
        FSS semantics).
        """
        if act_name:
            return is_known_section_for_act(target, act_name)
        base = re.match(r"^\d{1,4}", str(target or "").strip())
        if not base:
            return False
        return base.group(0) in FSS_ACT_SECTIONS


# End of crossref_adapter.py
