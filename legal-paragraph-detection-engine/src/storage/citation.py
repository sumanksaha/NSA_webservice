import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ProcessingMode(Enum):
    """Processing modes for the engine."""

    FAST = "fast"
    ACCURATE = "accurate"
    COMPREHENSIVE = "comprehensive"


@dataclass
class ProcessingConfig:
    """Configuration for legal document processing."""

    mode: ProcessingMode = ProcessingMode.ACCURATE
    max_depth: int = 10
    confidence_threshold: float = 0.7
    preserve_citations: bool = True
    normalize_text: bool = True
    detect_special_patterns: bool = True
    output_format: str = "json"
    export_path: str = "output"
    cache_size: int = 1000


class LegalParagraphEngine:
    """
    Main engine for processing legal documents and extracting structured paragraphs.

    Features:
    - Hierarchical numbering detection
    - Paragraph boundary detection
    - Section/subsection extraction
    - Clause/subclause parsing
    - Citation preservation
    - Parent-child relationship tracking
    - JSON export functionality
    - Thread-safe operation
    """

    def __init__(self, config: ProcessingConfig | None = None):
        self.config = config or ProcessingConfig()
        from .storage.citation import CitationExtractor

        self.citation_extractor = CitationExtractor()
        from .parsers.section_parser import SectionParser

        self.section_parser = SectionParser()
        from .core.paragraph import TextNormalizer

        self.text_normalizer = TextNormalizer()
        from .core.hierarchy import HierarchyDetector

        self.hierarchy_detector = HierarchyDetector()
        from .parsers.clause_parser import ClauseParser

        self.clause_parser = ClauseParser()

        self._lock = threading.RLock()
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._processing_stats = {
            "total_documents": 0,
            "successful_extractions": 0,
            "failed_extractions": 0,
            "average_processing_time": 0.0,
        }

    def process_document(self, text: str, doc_type_info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        Process a legal document and extract structured paragraphs.

        Args:
            text: Legal document text
            doc_type_info: Optional information about document type

        Returns:
            List of processed paragraphs in structured format
        """
        with self._lock:
            start_time = datetime.now()

            try:
                # Clear internal caches
                self.text_normalizer.clear_cache()
                self.citation_extractor.clear_cache()
                self.section_parser.clear_cache()

                # Step 1: Normalize and clean text
                if self.config.normalize_text:
                    normalized_text = self.text_normalizer.clean_text(text)
                else:
                    normalized_text = text

                # Step 2: Extract document metadata
                document_type = "unknown"
                if doc_type_info:
                    document_type = doc_type_info.get("type", "unknown")

                # Step 3: Parse sections using section parser
                sections = self.section_parser.parse_sections(normalized_text)

                # Step 4: Parse clauses
                clauses_data = self.clause_parser.parse_clauses(normalized_text)

                # Step 5: Extract citations
                citations = []
                if self.config.preserve_citations:
                    citations = self.citation_extractor.extract_citations(normalized_text)

                # Step 6: Detect paragraph boundaries
                paragraphs = self.paragraph_detector.detect_paragraph_boundaries(normalized_text)

                # Step 7: Build hierarchical structure
                structured_output = self._build_hierarchical_structure(
                    sections, clauses_data, citations, paragraphs, document_type
                )

                # Step 8: Update statistics
                self._update_statistics(True, start_time)

                # Step 9: Cache result
                cache_key = hash(text)
                if len(self._cache) < self.config.cache_size:
                    self._cache[cache_key] = structured_output

                return structured_output

            except Exception as e:
                # Update statistics on error
                self._update_statistics(False, start_time)
                raise RuntimeError(f"Failed to process document: {e!s}")

    def _build_hierarchical_structure(
        self,
        sections: list[SectionData],
        clauses_data: list[dict[str, Any]],
        citations: list,
        paragraphs: list,
        document_type: str,
    ) -> list[dict[str, Any]]:
        """Build complete hierarchical structure."""
        structured_output = []

        for paragraph in paragraphs:
            # Extract hierarchy information
            section = paragraph.section or self._extract_section_from_text(paragraph.text)
            clause = paragraph.clause or self._extract_clause_from_text(paragraph.text)
            subclause = paragraph.subclause or self._extract_subclause_from_text(paragraph.text)

            # Collect citations relevant to paragraph
            relevant_citations = self._find_citations_for_paragraph(paragraph.text, citations, section, clause)

            # Build paragraph structure
            paragraph_dict = {
                "paragraph_id": paragraph.id,
                "section": section,
                "clause": clause,
                "subclause": subclause,
                "paragraph_type": paragraph.paragraph_type.value,
                "text": paragraph.text,
                "citations": [
                    {"type": c.citation_type.value, "reference": c.normalized_text} for c in relevant_citations
                ],
                "parent_id": paragraph.parent_id,
                "children": paragraph.children,
                "hierarchy_depth": paragraph.hierarchy_depth,
                "word_count": paragraph.word_count,
                "document_type": document_type,
                "extraction_timestamp": datetime.now(UTC).isoformat(),
                "confidence_scores": self._calculate_confidence_scores(paragraph, relevant_citations),
                "metadata": {
                    "section_info": section if section else {},
                    "clause_info": clause if clause else {},
                    "processing_config": self.config.__dict__,
                },
            }

            structured_output.append(paragraph_dict)

        return structured_output

    def _extract_section_from_text(self, text: str) -> str | None:
        """Extract section number from paragraph text."""
        section_match = re.search(r"Section\s*(\d+)", text, re.IGNORECASE)
        if section_match:
            return section_match.group(1)
        return None

    def _extract_clause_from_text(self, text: str) -> str | None:
        """Extract clause number from paragraph text."""
        clause_match = re.search(r"Clause\s*([a-zA-Z]\d*)", text, re.IGNORECASE)
        if clause_match:
            return clause_match.group(1)
        return None

    def _extract_subclause_from_text(self, text: str) -> str | None:
        """Extract subclause number from paragraph text."""
        subclause_match = re.search(r"Sub-clause\s*([a-zA-Z]\d*)", text, re.IGNORECASE)
        if subclause_match:
            return subclause_match.group(1)
        return None

    def _find_citations_for_paragraph(
        self, paragraph_text: str, all_citations: list, section: str | None, clause: str | None
    ) -> list:
        """Find citations relevant to a specific paragraph."""
        relevant = []

        for citation in all_citations:
            # Check if citation appears in paragraph text
            if citation.normalized_text in paragraph_text:
                relevant.append(citation)
                continue

            # Check section/clause relevance
            if citation.details and (
                (section and str(section) in str(citation.details).lower())
                or (clause and clause in str(citation.details).lower())
            ):
                relevant.append(citation)

        return relevant

    def _calculate_confidence_scores(self, paragraph, citations: list) -> dict[str, float]:
        """Calculate confidence scores for extracted paragraph."""
        scores = {}

        # Confidence based on structure detection
        scores["structure_detection"] = min(paragraph.hierarchy_depth / self.config.max_depth, 1.0)

        # Confidence based on length
        scores["content_quality"] = min(paragraph.word_count / 50, 1.0)

        # Confidence based on citation presence
        scores["citation_presence"] = min(len(citations) / 10, 1.0)

        # Overall confidence
        scores["overall"] = (
            scores["structure_detection"] * 0.4 + scores["content_quality"] * 0.3 + scores["citation_presence"] * 0.3
        )

        return scores

    def _update_statistics(self, success: bool, start_time: datetime) -> None:
        """Update processing statistics."""
        processing_time = (datetime.now() - start_time).total_seconds()

        self._processing_stats["total_documents"] += 1

        if success:
            self._processing_stats["successful_extractions"] += 1
        else:
            self._processing_stats["failed_extractions"] += 1

        # Update average processing time
        total = self._processing_stats["total_documents"]
        current_avg = self._processing_stats["average_processing_time"]
        self._processing_stats["average_processing_time"] = (current_avg * (total - 1) + processing_time) / total

    def get_processing_stats(self) -> dict[str, Any]:
        """Get processing statistics."""
        return self._processing_stats.copy()

    def clear_cache(self) -> None:
        """Clear all caches."""
        with self._lock:
            self._cache.clear()
