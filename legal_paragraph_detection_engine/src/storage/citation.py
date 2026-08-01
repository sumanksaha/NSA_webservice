"""Citation extraction and normalization for legal documents.

Provides functionality to identify, classify, and normalize legal citations
found within document text, including court citations, statutory references,
section references, date references, and registry citations.
"""

import re
from enum import Enum
from typing import ClassVar

from ..utils.cache import evict_if_full, stable_key
from ..utils.performance import PerformanceProfiler


class CitationType(Enum):
    """Types of legal citations that can be extracted."""

    SUPREME_COURT = "supreme_court"
    HIGH_COURT = "high_court"
    STATUTORY = "statutory"
    SECTION = "section"
    DATE_REFERENCE = "date_reference"
    REGISTRY = "registry"
    CONSTITUTION = "constitution"


class LegalCitation:
    """Represents a single legal citation extracted from document text."""

    def __init__(
        self,
        citation_type: CitationType,
        normalized_text: str,
        details: dict[str, str],
        confidence: float,
        context: str,
        source_text: str | None = None,
    ):
        self.citation_type = citation_type
        self.normalized_text = normalized_text
        self.details = details
        self.confidence = confidence
        self.context = context
        self.source_text = source_text

    def __repr__(self) -> str:
        return (
            f"LegalCitation(type={self.citation_type.name}, "
            f"text={self.normalized_text!r}, confidence={self.confidence:.2f})"
        )

    def to_dict(self) -> dict[str, str | float | dict[str, str]]:
        """Convert citation to dictionary representation."""
        return {
            "citation_type": self.citation_type.name,
            "normalized_text": self.normalized_text,
            "details": self.details,
            "confidence": self.confidence,
            "context": self.context,
        }


class CitationExtractor:
    """Extracts and normalizes legal citations from document text.

    Supports multiple citation types including Supreme Court references,
    High Court references, statutory citations, section references,
    date references, and registry citations.

    Features:
        - Pattern-based extraction with regex
        - Confidence scoring for each citation
        - Context extraction around citations
        - Caching for repeated text processing
        - Thread-safe operation
    """

    # Supreme Court citation patterns: (2020 SC 123/456)
    SUPREME_COURT_PATTERNS: ClassVar[list[str]] = [
        r"\((\d{4})\s*SC\s*(\d+)/(\d+)\)",
        r"\((\d{4})\s*Supreme\s*Court\s*(\d+)/(\d+)\)",
        r"\((\d{4})\s*SC\s*(\d+)\)",
    ]

    # High Court citation patterns: (HC 123/456), (Honorable HC 123/456)
    HIGH_COURT_PATTERNS: ClassVar[list[str]] = [
        r"\(Honorable\s+HC\s+(\d+)/(\d+)\)",
        r"\(Hon'ble\s+HC\s+(\d+)/(\d+)\)",
        r"\(Honorable\s+High\s*Court\s+(\d+)/(\d+)\)",
        r"\(HC\s+(\d+)/(\d+)\)",
        r"\(High\s*Court\s+(\d+)/(\d+)\)",
    ]

    # Statutory citation patterns: The Indian Penal Code, Constitution of India, Air Pollution Act
    STATUTORY_PATTERNS: ClassVar[list[str]] = [
        r"The\s+(?:Indian\s+)?([A-Z][A-Za-z\s]+?)\s*(?:\s+Act)?",
        r"([A-Z][A-Za-z\s]+?)\s+Act",
        r"Constitution\s+of\s+India",
        r"([A-Z][A-Za-z\s]+?)\s+(?:Act|Statute|Law|Regulation)",
    ]

    # Section citation patterns: Section 5(2), Clause (a) of the Act
    SECTION_PATTERNS: ClassVar[list[str]] = [
        r"Section\s+(\d+(?:\(\d+\))?)",
        r"Clause\s+\(([a-z])\)",
        r"clause\s+(\d+(?:\(\d+\))?)",
    ]

    # Date reference patterns: 01/01/2020 to 31/12/2025
    DATE_PATTERNS: ClassVar[list[str]] = [
        r"\b(\d{2}/\d{2}/\d{4})\s*(?:to|-)\s*(\d{2}/\d{2}/\d{4})\b",
        r"\b(\d{1,2}\s+\w+\s+\d{4})\b",
    ]

    # Registry citation patterns: A 1234/2021, B 5678/2022
    REGISTRY_PATTERNS: ClassVar[list[str]] = [
        r"\b([A-Z]\s*\d+/\d{4})\b",
    ]

    def __init__(self, cache_size: int = 1000):
        """Initialize the citation extractor.

        Args:
            cache_size: Maximum number of entries to cache
        """
        self._cache: dict[str, list[LegalCitation]] = {}
        self._cache_size = cache_size
        self._profiler = PerformanceProfiler()
        self._compiled_patterns: dict[str, list[re.Pattern[str]]] = {}
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns for performance."""
        self._compiled_patterns = {
            "supreme_court": [re.compile(p, re.IGNORECASE) for p in self.SUPREME_COURT_PATTERNS],
            "high_court": [re.compile(p, re.IGNORECASE) for p in self.HIGH_COURT_PATTERNS],
            "statutory": [re.compile(p, re.IGNORECASE) for p in self.STATUTORY_PATTERNS],
            "section": [re.compile(p, re.IGNORECASE) for p in self.SECTION_PATTERNS],
            "date": [re.compile(p, re.IGNORECASE) for p in self.DATE_PATTERNS],
            "registry": [re.compile(p, re.IGNORECASE) for p in self.REGISTRY_PATTERNS],
        }

    def extract_citations(self, text: str) -> list[LegalCitation]:
        """Extract all legal citations from the given text.

        Args:
            text: Legal document text to analyze

        Returns:
            List of LegalCitation objects found in the text
        """
        # Create cache key
        normalized_text = re.sub(r"\s+", " ", text.strip())
        text_hash = stable_key(normalized_text)

        # Check cache first
        if text_hash in self._cache:
            return self._cache[text_hash]

        citations: list[LegalCitation] = []

        # Extract each citation type
        citations.extend(self._extract_supreme_court(text))
        citations.extend(self._extract_high_court(text))
        citations.extend(self._extract_statutory(text))
        citations.extend(self._extract_section(text))
        citations.extend(self._extract_date(text))
        citations.extend(self._extract_registry(text))

        # Cache results (bounded, FIFO)
        evict_if_full(self._cache, self._cache_size)
        self._cache[text_hash] = citations
        return citations

    def _get_context(self, text: str, match: re.Match[str], window: int = 50) -> str:
        """Extract context around a match.

        Args:
            text: Full text
            match: Regex match object
            window: Number of characters before/after match

        Returns:
            Context string around the match
        """
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        return text[start:end].strip()

    def _extract_supreme_court(self, text: str) -> list[LegalCitation]:
        """Extract Supreme Court citations."""
        citations: list[LegalCitation] = []
        for pattern in self._compiled_patterns["supreme_court"]:
            for match in pattern.finditer(text):
                groups = match.groups()
                year_str = str(groups[0]) if groups else ""

                # Build normalized text based on number of groups
                if len(groups) >= 3:
                    normalized = f"{groups[0]} SC {groups[1]}/{groups[2]}"
                elif len(groups) == 2:
                    normalized = f"{groups[0]} SC {groups[1]}"
                else:
                    normalized = match.group(0)

                case_number = str(groups[1]) if len(groups) >= 2 else ""

                citation = LegalCitation(
                    citation_type=CitationType.SUPREME_COURT,
                    normalized_text=normalized,
                    details={"year": year_str, "case_number": case_number},
                    confidence=0.95,
                    context=self._get_context(text, match),
                    source_text=match.group(0),
                )
                citations.append(citation)
        return citations

    def _extract_high_court(self, text: str) -> list[LegalCitation]:
        """Extract High Court citations."""
        citations: list[LegalCitation] = []
        for pattern in self._compiled_patterns["high_court"]:
            for match in pattern.finditer(text):
                normalized = match.group(0).strip("()")
                case_number = f"{match.group(1)}/{match.group(2)}" if match.groups() else ""

                citation = LegalCitation(
                    citation_type=CitationType.HIGH_COURT,
                    normalized_text=normalized,
                    details={"case_number": case_number},
                    confidence=0.90,
                    context=self._get_context(text, match),
                    source_text=match.group(0),
                )
                citations.append(citation)
        return citations

    def _extract_statutory(self, text: str) -> list[LegalCitation]:
        """Extract statutory citations including constitutions."""
        citations: list[LegalCitation] = []
        for pattern in self._compiled_patterns["statutory"]:
            for match in pattern.finditer(text):
                matched_text = match.group(0)
                normalized = matched_text.strip()

                # Determine if this is a constitution reference
                if "Constitution" in matched_text:
                    citation = LegalCitation(
                        citation_type=CitationType.CONSTITUTION,
                        normalized_text=normalized,
                        details={"jurisdiction": "India"},
                        confidence=0.85,
                        context=self._get_context(text, match),
                        source_text=matched_text,
                    )
                else:
                    citation = LegalCitation(
                        citation_type=CitationType.STATUTORY,
                        normalized_text=normalized,
                        details={"statute_name": normalized},
                        confidence=0.80,
                        context=self._get_context(text, match),
                        source_text=matched_text,
                    )
                citations.append(citation)
        return citations

    def _extract_section(self, text: str) -> list[LegalCitation]:
        """Extract section citations."""
        citations: list[LegalCitation] = []
        for pattern in self._compiled_patterns["section"]:
            for match in pattern.finditer(text):
                normalized = match.group(0).strip()
                section_ref = str(match.group(1)) if match.groups() else ""

                citation = LegalCitation(
                    citation_type=CitationType.SECTION,
                    normalized_text=normalized,
                    details={"section_reference": section_ref},
                    confidence=0.85,
                    context=self._get_context(text, match),
                    source_text=match.group(0),
                )
                citations.append(citation)
        return citations

    def _extract_date(self, text: str) -> list[LegalCitation]:
        """Extract date references."""
        citations: list[LegalCitation] = []
        for pattern in self._compiled_patterns["date"]:
            for match in pattern.finditer(text):
                normalized = match.group(0).strip()
                year = ""
                for group in match.groups():
                    if group:
                        year_match = re.search(r"(\d{4})", str(group))
                        if year_match:
                            year = year_match.group(1)
                            break

                citation = LegalCitation(
                    citation_type=CitationType.DATE_REFERENCE,
                    normalized_text=normalized,
                    details={"year": year, "date": normalized},
                    confidence=0.75,
                    context=self._get_context(text, match),
                    source_text=match.group(0),
                )
                citations.append(citation)
        return citations

    def _extract_registry(self, text: str) -> list[LegalCitation]:
        """Extract registry citations."""
        citations: list[LegalCitation] = []
        for pattern in self._compiled_patterns["registry"]:
            for match in pattern.finditer(text):
                normalized = match.group(1)
                # Extract year from the matched text
                year_match = re.search(r"(\d{4})", normalized)
                year = year_match.group(1) if year_match else ""

                citation = LegalCitation(
                    citation_type=CitationType.REGISTRY,
                    normalized_text=normalized,
                    details={"year": year, "registry_reference": normalized},
                    confidence=0.85,
                    context=self._get_context(text, match),
                    source_text=match.group(0),
                )
                citations.append(citation)
        return citations

    def clear_cache(self) -> None:
        """Clear the citation extraction cache."""
        self._cache.clear()

    def get_cache_stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return {
            "cache_size": len(self._cache),
            "max_cache_size": self._cache_size,
        }


# End of citation.py
