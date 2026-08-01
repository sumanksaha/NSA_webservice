"""
Legal Paragraph Detection Engine
"""

from __future__ import annotations

from typing import Any

__version__ = "1.0.0"
__author__ = "Legal Intelligence Systems"
__description__ = "Intelligent Legal Paragraph Detection Engine"

# Import core components
from .src.core.hierarchy import HierarchyDetector, LegalNode
from .src.core.paragraph import ParagraphBoundaryDetector, ParagraphInfo, TextNormalizer
from .src.legal_engine import LegalParagraphEngine, ProcessingConfig, ProcessingMode
from .src.parsers.clause_parser import ClauseParser
from .src.parsers.legal_document import DocumentTypeClassifier
from .src.parsers.section_parser import SectionParser
from .src.storage.citation import CitationExtractor
from .src.storage.exporter import ParagraphExporter
from .src.utils.performance import PerformanceProfiler

# Import utilities
from .src.utils.text_cleaner import TextCleaner

__all__ = [
    "CitationExtractor",
    "ClauseParser",
    "DocumentTypeClassifier",
    "HierarchyDetector",
    "LegalNode",
    "LegalParagraphEngine",
    "ParagraphBoundaryDetector",
    "ParagraphExporter",
    "ParagraphInfo",
    "PerformanceProfiler",
    "ProcessingConfig",
    "ProcessingMode",
    "SectionParser",
    "TextCleaner",
    "TextNormalizer",
]


# Simple function for quick usage
def process_legal_document(text: str, config: ProcessingConfig | None = None) -> list[dict[str, Any]]:
    """
    Quick function to process legal document text.

    Args:
        text: Legal document text to process
        config: Optional processing configuration

    Returns:
        List of processed paragraphs
    """
    from .src.legal_engine import LegalParagraphEngine

    engine = LegalParagraphEngine(config)
    return engine.process_document(text)
