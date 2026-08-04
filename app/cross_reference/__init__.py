"""Cross-reference engine (Phase 6).

Provides automatic cross-referencing for legal documents: extracting
paragraph / annexure / section references, linking them to annexure
metadata, and renumbering paragraphs and annexure letters after
insert/delete operations.
"""

from app.cross_reference.engine import (
    KNOWN_SECTIONS,
    CrossReference,
    CrossReferenceEngine,
    ReferenceKind,
    strip_html_to_text,
)

__all__ = [
    "KNOWN_SECTIONS",
    "CrossReference",
    "CrossReferenceEngine",
    "ReferenceKind",
    "generate_xref_report_data",
    "strip_html_to_text",
]


def generate_xref_report_data(
    html_content: str, case_id: int | None = None, adjudication_id: int | None = None
) -> dict:
    """Render the xref report data for a case or adjudication document.

    Takes the already-annotated HTML (enclosures filled in by
    ``annotate_html``), strips it to plain text, extracts cross-references,
    and resolves annexure/section links against the DB.

    Returns the same JSON-safe dict as :meth:`CrossReferenceEngine.link_references`
    plus a ``text_preview`` key with the first 500 chars of extracted text.
    """
    engine = CrossReferenceEngine()
    text = strip_html_to_text(html_content)
    report = engine.link_references(text, case_id=case_id, adjudication_id=adjudication_id)
    report["text_preview"] = text[:500]
    return report
