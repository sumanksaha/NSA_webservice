"""Core package initialization for Legal Paragraph Detection Engine.

This module provides the main interface for the Legal Paragraph Detection Engine,
importing and exposing all core components and functionality.
"""

from .core.hierarchy import (
    ClauseInfo,
    HierarchyDetector,
    LegalNode,
    SectionInfo,
)
from .core.paragraph import (
    ParagraphBoundaryDetector,
    ParagraphInfo,
    ParagraphType,
    TextNormalizer,
)
from .legal_engine import LegalParagraphEngine, ProcessingConfig, ProcessingMode
from .parsers.clause_parser import ClauseData, ClauseParser, ClauseType
from .parsers.legal_document import DocumentTypeClassifier, LegalDocument, LegalDocumentType
from .parsers.section_parser import SectionData, SectionParser, SectionType
from .storage.citation import CitationExtractor, CitationType, LegalCitation
from .storage.exporter import LegalParagraph, ParagraphExporter
from .utils.performance import PerformanceProfiler
from .utils.text_cleaner import TextCleaner, TextType

__all__ = [
    "CitationExtractor",
    "CitationType",
    "ClauseData",
    "ClauseInfo",
    "ClauseParser",
    "ClauseType",
    "DocumentTypeClassifier",
    "HierarchyDetector",
    "LegalCitation",
    "LegalDocument",
    "LegalDocumentType",
    "LegalNode",
    "LegalParagraph",
    "LegalParagraphEngine",
    "ParagraphBoundaryDetector",
    "ParagraphExporter",
    "ParagraphInfo",
    "ParagraphType",
    "PerformanceProfiler",
    "ProcessingConfig",
    "ProcessingMode",
    "SectionData",
    "SectionInfo",
    "SectionParser",
    "SectionType",
    "TextCleaner",
    "TextNormalizer",
    "TextType",
]

__version__ = "1.0.0"
__author__ = "Legal Intelligence Systems"
__description__ = "Intelligent Legal Paragraph Detection Engine for parsing hierarchical structures in legal documents"
