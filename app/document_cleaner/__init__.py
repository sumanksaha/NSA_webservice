"""High-performance Legal Document Cleaning Pipeline.

Usage::

    from app.document_cleaner import DocumentCleaner, CleaningConfig

    cleaner = DocumentCleaner()
    result = cleaner.clean(raw_text)
    print(result.clean_text)
    print(result.report.model_dump_json(indent=2))
"""

from __future__ import annotations

from app.document_cleaner.config import PRESETS
from app.document_cleaner.models import (
    CleanedDocument,
    CleaningConfig,
    CleaningReport,
    RemovedItem,
)
from app.document_cleaner.pipeline import DocumentCleaner

__all__ = [
    "PRESETS",
    "CleanedDocument",
    "CleaningConfig",
    "CleaningReport",
    "DocumentCleaner",
    "RemovedItem",
]
