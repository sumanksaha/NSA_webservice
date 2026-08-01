import json
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .core.hierarchy import HierarchyDetector
from .core.paragraph import ParagraphInfo, ParagraphType, TextNormalizer
from .parsers.clause_parser import ClauseData, ClauseParser
from .parsers.legal_document import DocumentTypeClassifier
from .parsers.section_parser import SectionData, SectionParser
from .storage.citation import CitationExtractor, LegalCitation
from .utils.cache import evict_if_full, stable_key

# Default blend for the overall confidence score (T-29 calibration):
# 40% structure detection, 35% content quality, 25% citation presence.
DEFAULT_CONFIDENCE_WEIGHTS: dict[str, float] = {
    "structure": 0.40,
    "quality": 0.35,
    "citation": 0.25,
}

# Paragraph types that indicate a recognised legal structure. These get a
# strong base structure score; free prose gets a modest non-zero floor.
_STRUCTURAL_TYPES: frozenset[ParagraphType] = frozenset({
    ParagraphType.SECTION,
    ParagraphType.SUBSECTION,
    ParagraphType.SUBSUBSECTION,
    ParagraphType.CLAUSE,
    ParagraphType.SUBCLAUSE,
    ParagraphType.EXPLANATION,
    ParagraphType.NOTE,
    ParagraphType.PROVISO,
    ParagraphType.SCHEDULE,
    ParagraphType.TABLE,
})


def _make_citation_pattern(reference_text: str) -> re.Pattern[str]:
    r"""Compile a case-insensitive match pattern for a citation reference.

    Word-boundary guards are added on the first/last character when it is a
    word character, so a citation like ``Section 5`` no longer matches inside
    ``Section 50`` (the trailing ``(?!\w)`` rejects it). References that start
    or end with non-word characters (parentheses, slashes) are matched as
    literal text.
    """
    if not reference_text:
        # Never-match pattern for empty references.
        return re.compile(r"(?!x)x")
    escaped = re.escape(reference_text)
    prefix = r"(?<!\w)" if reference_text[0].isalnum() else ""
    suffix = r"(?!\w)" if reference_text[-1].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


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
    confidence_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_CONFIDENCE_WEIGHTS))
    # --- Configurable heuristics (T-34: no more magic numbers) ---
    paragraph_boundary_chars: int = 100
    content_quality_word_curve: float = 150.0


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
        self.citation_extractor = CitationExtractor()
        self.section_parser = SectionParser()
        self.text_normalizer = TextNormalizer(paragraph_boundary_chars=self.config.paragraph_boundary_chars)
        self.hierarchy_detector = HierarchyDetector()
        self.clause_parser = ClauseParser()
        self.document_classifier = DocumentTypeClassifier()
        from .core.paragraph import ParagraphBoundaryDetector

        self.paragraph_detector = ParagraphBoundaryDetector()

        self._lock = threading.RLock()
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._citation_pattern_cache: dict[str, re.Pattern[str]] = {}
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
            doc_type_info: Optional information about document type. A string
                ``"type"`` key is normalized to a canonical value when
                recognised (e.g. "Analysis Report" → "report"); when no hint
                is supplied the document type is auto-detected from the text by
                the :class:`DocumentTypeClassifier` (best-effort — falls back
                to "unknown" on any classification error).

        Returns:
            List of processed paragraphs in structured format

        Note:
            Repeated calls with the same ``(text, doc_type_info)`` return the same
            cached object (shared — treat as read-only). Cached hits skip
            :meth:`get_processing_stats`, so ``total_documents`` counts actual
            parses, not calls.
        """
        with self._lock:
            start_time = datetime.now()

            try:
                # Read-through cache: return the previously computed result for
                # the same (text, doc_type_info) pair instead of re-parsing.
                cache_key = self._make_cache_key(text, doc_type_info)
                if cache_key in self._cache:
                    return self._cache[cache_key]

                # Step 1: Normalize and clean text
                normalized_text = self.text_normalizer.clean_text(text) if self.config.normalize_text else text

                # Step 2: Extract document metadata
                # A supplied hint is authoritative (normalized to a canonical
                # value where recognised); without a hint the document type is
                # auto-detected from the text by the DocumentTypeClassifier
                # (T-46d / v6.6) instead of defaulting to "unknown".
                document_type = "unknown"
                if doc_type_info:
                    hint = doc_type_info.get("type")
                    document_type = (
                        self.document_classifier.normalize_doc_type(hint) if isinstance(hint, str) else "unknown"
                    )
                else:
                    try:
                        document_type = self.document_classifier.classify_document(normalized_text).type.value
                    except Exception:
                        # Classification is best-effort metadata — never fail the parse.
                        document_type = "unknown"

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

                # Step 9: Cache result (read-through, bounded FIFO)
                evict_if_full(self._cache, self.config.cache_size)
                self._cache[cache_key] = structured_output

                return structured_output

            except Exception as e:
                # Update statistics on error
                self._update_statistics(False, start_time)
                raise RuntimeError(f"Failed to process document: {e!s}") from e

    def _make_cache_key(self, text: str, doc_type_info: dict[str, Any] | None) -> str:
        """Build a stable cache key for a (text, doc_type_info) input pair.

        ``doc_type_info`` influences the output (the ``document_type`` field), so
        it must be part of the key — two calls that differ only in doc_type_info
        must not share a cache entry.
        """
        if doc_type_info is None:
            return stable_key(text)
        try:
            info_json = json.dumps(doc_type_info, sort_keys=True, default=str)
        except (TypeError, ValueError):
            info_json = str(doc_type_info)
        return stable_key(text + "\x00" + info_json)

    def _build_hierarchical_structure(
        self,
        sections: list[SectionData],
        clauses_data: list[ClauseData],
        citations: list[LegalCitation],
        paragraphs: list[ParagraphInfo],
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

            # Calculate calibrated confidence scores
            confidence_scores = self._calculate_confidence_scores(paragraph, relevant_citations)

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
                "confidence_scores": confidence_scores,
                # Honest signal: with the default 0.7 threshold most paragraphs
                # are below it (recalibrated scores are conservative). It is a
                # consumer-facing flag, not a filter.
                "meets_confidence_threshold": confidence_scores["overall"] >= self.config.confidence_threshold,
                # Re-export the active heuristic thresholds (T-34) so consumers
                # can interpret word_count/boundary behavior.
                "heuristic_thresholds": {
                    "paragraph_boundary_chars": self.config.paragraph_boundary_chars,
                    "content_quality_word_curve": self.config.content_quality_word_curve,
                },
                "metadata": {
                    "section_info": section if section else {},
                    "clause_info": clause if clause else {},
                    "processing_config": {
                        "mode": self.config.mode.value,
                        "max_depth": self.config.max_depth,
                        "confidence_threshold": self.config.confidence_threshold,
                        "preserve_citations": self.config.preserve_citations,
                        "normalize_text": self.config.normalize_text,
                        "detect_special_patterns": self.config.detect_special_patterns,
                        "output_format": self.config.output_format,
                        "export_path": self.config.export_path,
                        "cache_size": self.config.cache_size,
                        "confidence_weights": dict(self.config.confidence_weights),
                    },
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

    def _get_citation_pattern(self, reference_text: str) -> re.Pattern[str]:
        """Return a cached, compiled match pattern for a citation reference.

        Patterns are keyed by a stable SHA-256 digest of the reference text and
        the cache is bounded by ``ProcessingConfig.cache_size`` (FIFO eviction).

        Note: this mutates ``_citation_pattern_cache`` and is only safe when
        called under the engine ``RLock`` (as it is from
        :meth:`_find_citations_for_paragraph`, itself invoked under
        :meth:`process_document`'s lock).
        """
        key = stable_key(reference_text)
        pattern = self._citation_pattern_cache.get(key)
        if pattern is None:
            pattern = _make_citation_pattern(reference_text)
            evict_if_full(self._citation_pattern_cache, self.config.cache_size)
            self._citation_pattern_cache[key] = pattern
        return pattern

    def _find_citations_for_paragraph(
        self,
        paragraph_text: str,
        all_citations: list[LegalCitation],
        section: str | None,
        clause: str | None,
    ) -> list[LegalCitation]:
        """Find citations relevant to a specific paragraph.

        Matching strategy (T-30, replaces naive substring matching):
        - Compiled, case-insensitive regex patterns with word-boundary guards
          (see :func:`_make_citation_pattern`) so "Section 5" no longer
          matches "Section 50" and case variations are caught.
        - Both ``normalized_text`` and the original ``source_text`` are tried.
        - Falls back to section/clause relevance via citation ``details``,
          also case-insensitive and word-boundary aware.
        """
        relevant: list[LegalCitation] = []

        for citation in all_citations:
            # Primary: compiled pattern match on normalized text, then source
            if self._get_citation_pattern(citation.normalized_text).search(paragraph_text) or (
                citation.source_text
                and citation.source_text != citation.normalized_text
                and self._get_citation_pattern(citation.source_text).search(paragraph_text)
            ):
                relevant.append(citation)
                continue

            # Fallback: section/clause relevance via citation details
            if citation.details:
                details_lower = " ".join(str(v).lower() for v in citation.details.values())
                section_hit = bool(section and re.search(rf"(?<!\w){re.escape(section.lower())}(?!\w)", details_lower))
                clause_hit = bool(clause and re.search(rf"(?<!\w){re.escape(clause.lower())}(?!\w)", details_lower))
                if section_hit or clause_hit:
                    relevant.append(citation)

        return relevant

    def _calculate_confidence_scores(
        self, paragraph: ParagraphInfo, citations: list[LegalCitation]
    ) -> dict[str, float]:
        """Calculate calibrated confidence scores for an extracted paragraph.

        Calibration rules (T-29):
        - ``structure_detection`` is type-aware: recognised structural
          paragraphs (section/clause/subclause/explanation/note/proviso/…)
          get a strong base score (0.70+) that grows with nesting depth;
          free prose gets a modest floor (0.25-0.60). No paragraph scores 0.
        - ``content_quality`` uses a floored, gradual curve on word count so
          short-but-valid legal fragments are not zeroed out.
        - ``citation_presence`` has a neutral floor; even a single citation
          contributes meaningfully.
        - ``overall`` blends the three using
          :attr:`ProcessingConfig.confidence_weights`
          (default 40% structure / 35% quality / 25% citations).
        """
        weights = {**DEFAULT_CONFIDENCE_WEIGHTS, **self.config.confidence_weights}

        # Type-aware structure detection (never 0.0 for structural elements)
        depth = min(paragraph.hierarchy_depth, 6)
        if paragraph.paragraph_type in _STRUCTURAL_TYPES:
            structure_detection = min(0.70 + depth * 0.05, 1.0)
        else:
            structure_detection = min(0.25 + depth * 0.05, 0.60)

        # Floored content quality: 0.25 base, reaching 1.0 at the configured
        # ``content_quality_word_curve`` word count (default 150).
        content_quality = min(0.25 + paragraph.word_count / self.config.content_quality_word_curve, 1.0)

        # Citation presence: neutral floor, +0.20 per citation, capped at 1.0
        citation_presence = min(0.20 + len(citations) * 0.20, 1.0)

        # Weighted overall blend, clamped to [0, 1]
        overall = max(
            0.0,
            min(
                1.0,
                weights["structure"] * structure_detection
                + weights["quality"] * content_quality
                + weights["citation"] * citation_presence,
            ),
        )

        return {
            "structure_detection": structure_detection,
            "content_quality": content_quality,
            "citation_presence": citation_presence,
            "overall": overall,
        }

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
        """Clear the engine result cache and every component cache."""
        with self._lock:
            self._cache.clear()
            self._citation_pattern_cache.clear()
            self.text_normalizer.clear_cache()
            self.citation_extractor.clear_cache()
            self.section_parser.clear_cache()
            self.clause_parser.clear_cache()
            self.document_classifier.clear_cache()
            self.hierarchy_detector.clear_cache()
            self.paragraph_detector.clear_cache()


# End of LegalParagraphEngine
