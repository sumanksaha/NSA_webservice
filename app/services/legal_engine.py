"""Service layer for the legal paragraph detection engine (T-46 integration).

Exposes the standalone ``legal_paragraph_detection_engine`` package to the
Flask app through a thin, JSON-safe wrapper.

Design notes:
- The engine is imported lazily so the app boots even when the package is not
  installed (e.g. minimal deployments that skip the engine). Callers that need
  the engine get a descriptive :class:`LegalEngineUnavailable` error instead.
- The engine is a process-wide singleton; its read-through cache (T-26) makes
  repeated analyses of the same text cheap.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Matches citation references produced by the engine, e.g. "section 52",
# "section 26(2)" — captures the top-level section number.
_SECTION_REF_RE = re.compile(r"section\s*(\d{1,3})", re.IGNORECASE)

_engine: Any = None


class LegalEngineUnavailable(RuntimeError):
    """Raised when the legal paragraph detection engine is not importable."""


def _get_engine() -> Any:
    """Return the shared engine instance, importing the package on first use."""
    global _engine
    if _engine is None:
        try:
            from legal_paragraph_detection_engine import LegalParagraphEngine
        except ImportError as exc:
            logger.warning("Legal paragraph detection engine not available: %s", exc)
            raise LegalEngineUnavailable(
                "The legal paragraph detection engine is not installed. "
                "Install it via `pip install -e ./legal_paragraph_detection_engine` "
                "or run from the repository root."
            ) from exc
        _engine = LegalParagraphEngine()
    return _engine


def analyze_legal_text(text: str, doc_type: str | None = None) -> dict[str, Any]:
    """Analyze legal document text and return a JSON-safe result.

    Args:
        text: Raw legal document text.
        doc_type: Optional document-type hint (e.g. "Notification").

    Returns:
        A dict with ``summary`` (aggregate statistics) and ``paragraphs``
        (the engine's structured paragraph list).

    Raises:
        LegalEngineUnavailable: If the engine package cannot be imported.
        ValueError: If ``text`` is empty/blank.
        RuntimeError: If the engine fails to process the text.
    """
    if not text or not text.strip():
        raise ValueError("No text provided to analyze.")

    engine = _get_engine()
    doc_type_info = {"type": doc_type} if doc_type else None
    paragraphs = engine.process_document(text, doc_type_info)

    return {
        "summary": _summarize(paragraphs),
        "paragraphs": paragraphs,
    }


def extract_section_references(analysis: dict[str, Any]) -> list[str]:
    """Return the sorted, de-duplicated top-level section numbers cited in an analysis.

    Shared by the case-file and adjudication auto-suggest features (T-46b):
    both consume :func:`analyze_legal_text` output, so this helper lives in the
    service layer rather than in any single blueprint.

    Args:
        analysis: Output of :func:`analyze_legal_text`.

    Returns:
        e.g. ``['26', '52']`` (sorted list of strings).
    """
    refs: set[str] = set()
    for para in analysis.get("paragraphs") or []:
        for citation in para.get("citations") or []:
            if citation.get("type") != "section":
                continue
            match = _SECTION_REF_RE.search(str(citation.get("reference", "")))
            if match:
                refs.add(match.group(1))
    return sorted(refs)


def _summarize(paragraphs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate lightweight statistics over the engine output."""
    total = len(paragraphs)
    paragraph_types: dict[str, int] = {}
    doc_types: set[str] = set()
    sections: set[str] = set()
    citations = 0
    meets_threshold = 0
    confidence_total = 0.0

    for para in paragraphs:
        para_type = str(para.get("paragraph_type", "unknown"))
        paragraph_types[para_type] = paragraph_types.get(para_type, 0) + 1
        doc_types.add(str(para.get("document_type", "unknown")))
        section = para.get("section")
        if isinstance(section, str) and section:
            sections.add(section)
        citations += len(para.get("citations") or [])
        if para.get("meets_confidence_threshold"):
            meets_threshold += 1
        scores = para.get("confidence_scores") or {}
        overall = scores.get("overall")
        if isinstance(overall, (int, float)):
            confidence_total += float(overall)

    return {
        "total_paragraphs": total,
        "paragraph_types": dict(sorted(paragraph_types.items())),
        "document_types": sorted(doc_types),
        "sections": sorted(sections),
        "total_citations": citations,
        "meets_threshold": meets_threshold,
        "avg_confidence": round(confidence_total / total, 3) if total else 0.0,
    }
