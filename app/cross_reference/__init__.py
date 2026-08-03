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
)

__all__ = ["KNOWN_SECTIONS", "CrossReference", "CrossReferenceEngine", "ReferenceKind"]
