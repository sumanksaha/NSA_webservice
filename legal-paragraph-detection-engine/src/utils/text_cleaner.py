"""Text cleaning and normalization utilities

Provides utilities for cleaning and normalizing legal text before processing,
including whitespace normalization, pattern preservation, and text preprocessing.
"""

import re
import threading
from enum import Enum


class TextType(Enum):
    """Types of text elements in legal documents."""

    LEGAL_CONTENT = "legal_content"
    PAGE_NUMBER = "page_number"
    HEADER = "header"
    FOOTER = "footer"
    MARKER = "marker"
    EMPTY = "empty"
    MIXED = "mixed"


class TextCleaner:
    """Cleans and normalizes legal text for processing."""

    # Patterns to preserve (important legal elements)
    PRESERVE_PATTERNS = {
        "legal_citations": [
            r"\(\d{4}\s*SC\s*[A-Z]?\d+\/\d+\)",
            r"\([Hh]onorable\s*[Jj]udicial?[\w\s]+-[Hh]c\s*[A-Z]?\d+\/\d+\)",
            r"\bSC\s*\d{4}\s*[A-Z]?\d+\/\d+\b",
            r"\b[A-Z]\s*HC\s*[A-Z]?\d+\/\d+\b",
        ],
        "statutory_references": [
            r"\b[A-Z][a-z\s]+(?:Act|Code|Rules|Regulations)\b(?:\s+\d{4})?",
            r"\b[A-Z]\s+(?:Sec|Section|Clause|Article)\s+\d+",
            r"\b(?:Section|Clause|Article)\s*\d+\s*(?:of\s+[A-Z][a-z\s]+)?",
        ],
        "document_markers": [
            r"\b(?:Section|Sec\.|§)\s*\d+\s*$",
            r"\b\(\s*[a-zA-Z]\s*\)\s*$",
            r"\b\[\s*[a-zA-Z]\s*\]\s*$",
            r"\b[i-ivIVX]{1,4}\s*$",
            r"\b\d+\.\d+\.\d+\s*$",
            r"^\s*Explanation\s*$",
            r"^\s*Note\s*:",
            r"^\s*Provided\s*$",
            r"^\s*PROVISO\s*$",
            r"^\s*Schedule\s*[IVX0-9]*",
            r"^\s*Table\s*[IVX0-9]*",
        ],
        "dates_and_numbers": [
            r"\b\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\b",
            r"\b\d+\s*(?:th|nd|rd|st)\s*(?:of\s+)?[A-Z][a-z]+\s+\d{4}\b",
        ],
    }

    # Patterns to clean or remove
    CLEAN_PATTERNS = [
        r"^\s*Page\s*\d+\s*$",
        r"^\s*\d+\s*\*\s*$",
        r"^\s*(?:Illustration|Example|Figure)\s*\d*\s*:",
        r"^\s*(?:Table|Fig)\s*[A-Z]\s*\d*\s*:",
        r"^\s*[A-Z]\s*=\s*[\s\S]*?\s*\n\s*[A-Z][a-z]+\s*=\s*",
        r"^\s*Said\s+(?:to|that)\s*$",
        r"^\s*The\s+(?:said|above)\s+(?:as)\s*$",
        r"^\s*-\s*[A-Z][a-z]+\s*:",
        r"^\s*•\s*[A-Z][a-z]+\s*:",
    ]

    # Whitespace normalization patterns
    WHITESPACE_PATTERNS = [
        (r"\r\n", "\n"),
        (r"\t", "    "),  # Replace with 4 spaces
        (r"\s{4,}", "    "),  # Replace multiple spaces with 4 spaces
        (r" \n", "\n"),  # Remove space before newline
        (r"\n\s*\n\s*\n+", "\n\n"),  # Normalize multiple newlines
    ]

    # Legal hierarchy markers
    HIERARCHY_MARKERS = [
        r"\b\d+\s*\.\s*[a-zA-Z]",  # 1.a, 2.b
        r"\(\s*[a-zA-Z]\s*\)",  # (a), (b)
        r"\[\s*[a-zA-Z]\s*\]",  # [a], [b]
        r"\(\s*[ivxIVX]{1,4}\s*\)",  # (i), (ii)
        r"\b\d+\.\d+\.\d+",  # 1.2.3
        r"\b\d+\s*\(\s*\d+\s*\)",  # 1(2)
        r"\b\d+\s*\(\s*[a-zA-Z]\s*\)",  # 1(a)
        r"\b\d+\s*\(\s*[i-ivIVX]{1,4}\s*\)",  # 1(i)
    ]

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._lock = threading.RLock()

    def clean_text(self, text: str) -> str:
        """
        Clean and normalize legal text.

        Args:
            text: Raw legal text

        Returns:
            Cleaned and normalized text
        """
        with self._lock:
            # Check cache first
            cache_key = hash(text)
            if cache_key in self._cache:
                return self._cache[cache_key]

            # Step 1: Preserve important legal patterns
            preserved_text = self._preserve_legal_patterns(text)

            # Step 2: Clean unwanted artifacts
            cleaned_text = self._clean_artifacts(preserved_text)

            # Step 3: Normalize whitespace
            normalized_text = self._normalize_whitespace(cleaned_text)

            # Step 4: Segment into paragraphs
            segmented_text = self._segment_into_paragraphs(normalized_text)

            result = segmented_text.strip()
            self._cache[cache_key] = result
            return result

    def _preserve_legal_patterns(self, text: str) -> str:
        """Preserve important legal patterns while removing noise."""
        lines = text.split("\n")
        processed_lines = []

        for line in lines:
            line_stripped = line.strip()

            if not line_stripped:
                processed_lines.append("")
                continue

            # Check if line contains important legal patterns
            line_type = self._classify_line_type(line_stripped)

            if line_type in [TextType.LEGAL_CONTENT, TextType.MARKER, TextType.DATES_AND_NUMBERS]:
                processed_lines.append(line_stripped)
            elif line_type == TextType.HEADER:
                # Keep headers but normalize formatting
                processed_lines.append(self._normalize_header(line_stripped))
            elif line_type == TextType.FOOTER:
                # Keep footers if they contain legal information
                if self._contains_legal_info(line_stripped):
                    processed_lines.append(line_stripped)
            else:
                # Clean and potentially keep
                cleaned = self._clean_non_legal_line(line_stripped)
                if cleaned:
                    processed_lines.append(cleaned)

        return "\n".join(processed_lines)

    def _classify_line_type(self, line: str) -> TextType:
        """Classify line type based on content."""
        line_lower = line.lower()

        # Check for legal citation patterns
        for category, patterns in self.PRESERVE_PATTERNS.items():
            if any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
                if category in ["legal_citations", "statutory_references"]:
                    return TextType.LEGAL_CONTENT
                elif category == "document_markers":
                    return TextType.MARKER
                elif category == "dates_and_numbers":
                    return TextType.DATES_AND_NUMBERS

        # Check for page numbers
        if re.match(r"^\s*\d+\s*$", line):
            return TextType.PAGE_NUMBER

        # Check for section headers
        if re.match(r"^\s*[A-Z][a-z\s]+:$", line):
            return TextType.HEADER

        # Check for footers
        if re.search(r"(?:copyright|page \d+ of|\d+ / \d+ pages)", line_lower):
            return TextType.FOOTER

        # Check for empty or whitespace-only lines
        if not line.strip():
            return TextType.EMPTY

        # Default to mixed or legal content
        if (
            re.search(r"\b\d+\s*\.\s*[a-zA-Z]\b", line)
            or re.search(r"\(\s*[a-zA-Z]\s*\)\s*$", line)
            or re.search(r"\[\s*[a-zA-Z]\s*\]\s*$", line)
        ):
            return TextType.MARKER

        return TextType.LEGAL_CONTENT

    def _normalize_header(self, line: str) -> str:
        """Normalize header formatting."""
        # Remove excess punctuation
        line = re.sub(r"^\s*[A-Z][a-z\s]+:", "", line)
        line = re.sub(r"^\s*[:\s]+", "", line)
        line = re.sub(r"\s+$", "", line)

        # Standardize case
        return line.strip()

    def _contains_legal_info(self, line: str) -> bool:
        """Check if footer contains legal information."""
        legal_indicators = [
            "copyright",
            "all rights reserved",
            "confidential",
            "internal use",
            "legal notice",
            "terms of use",
            "privacy policy",
        ]

        line_lower = line.lower()
        return any(indicator in line_lower for indicator in legal_indicators)

    def _clean_non_legal_line(self, line: str) -> str:
        """Clean non-legal lines while preserving structure."""
        # Check if line is a legal marker
        if self._is_legal_marker(line):
            return line

        # Remove formatting artifacts
        cleaned = line

        # Remove page number patterns
        cleaned = re.sub(r"^\s*\d+\s*\*\s*", "", cleaned)

        # Remove illustration/figure references
        cleaned = re.sub(r"^\s*(?:Illustration|Figure)\s*\d*\s*:", "", cleaned)

        # Remove table references
        cleaned = re.sub(r"^\s*(?:Table|Fig)\s*[A-Z]\s*\d*\s*:", "", cleaned, re.IGNORECASE)

        # Remove bullet points
        cleaned = re.sub(r"^\s*[-•]\s*", "", cleaned)

        # Clean up excess punctuation
        cleaned = re.sub(r"^\s*[:\s]+", "", cleaned)
        cleaned = re.sub(r"\s+$", "", cleaned)

        return cleaned.strip()

    def _is_legal_marker(self, line: str) -> bool:
        """Check if line is a legal hierarchy marker."""
        # Common legal markers
        marker_patterns = [
            r"^\s*\d+\s*\.\s*[a-zA-Z]\s*$",
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*$",
            r"^\s*\[\s*[a-zA-Z]\s*\]\s*$",
            r"^\s*[ivxIVX]{1,4}\s*$",
            r"^\s*\d+\.\d+\.\d+\s*$",
            r"^\s*\d+\s*\(\s*\d+\s*\)\s*$",
            r"^\s*\d+\s*\(\s*[a-zA-Z]\s*\)\s*$",
            r"^\s*\d+\s*\(\s*[ivxIVX]{1,4}\s*\)\s*$",
            r"^\s*Explanation\s*$",
            r"^\s*Provided\s*$",
            r"^\s*PROVISO\s*$",
            r"^\s*Note\s*:",
            r"^\s*Schedule\s*[IVX0-9]*",
            r"^\s*Table\s*[IVX0-9]*",
        ]

        return any(re.match(pattern, line, re.IGNORECASE) for pattern in marker_patterns)

    def _clean_artifacts(self, text: str) -> str:
        """Remove unwanted artifacts and formatting."""
        lines = text.split("\n")
        cleaned_lines = []

        for line in lines:
            if not line.strip():
                cleaned_lines.append("")
                continue

            # Apply cleaning patterns
            cleaned = line

            for pattern in self.CLEAN_PATTERNS:
                cleaned = re.sub(pattern, "", cleaned, flags=re.MULTILINE | re.IGNORECASE)

            cleaned_lines.append(cleaned)

        return "\n".join(cleaned_lines)

    def _normalize_whitespace(self, text: str) -> str:
        """Normalize whitespace in text."""
        for old_pattern, new_pattern in self.WHITESPACE_PATTERNS:
            text = re.sub(old_pattern, new_pattern, text)

        # Remove trailing whitespace from lines
        lines = [line.rstrip() for line in text.split("\n")]
        return "\n".join(lines)

    def _segment_into_paragraphs(self, text: str) -> str:
        """
        Segment text into paragraphs based on legal document structure.

        Args:
            text: Cleaned text

        Returns:
            Text with paragraph breaks
        """
        lines = text.split("\n")
        paragraphs = []
        current_para = []

        for line in lines:
            line_stripped = line.strip()

            if not line_stripped:
                if current_para:
                    paragraphs.append("\n".join(current_para))
                    current_para = []
                continue

            # Check if line continues current paragraph
            if self._continues_previous_line(current_para[-1], line_stripped) if current_para else False:
                current_para.append(line_stripped)
            else:
                if current_para:
                    paragraphs.append("\n".join(current_para))
                current_para = [line_stripped]

        if current_para:
            paragraphs.append("\n".join(current_para))

        # Join paragraphs with double newline
        return "\n\n".join(paragraphs)

    def _continues_previous_line(self, prev_line: str, current_line: str) -> bool:
        """Check if current line continues the previous line."""
        # Don't continue if current line starts with hierarchy marker
        if self._is_legal_marker(current_line):
            return False

        # Don't continue if current line is a new section
        if current_line.startswith(("Section ", "Clause ", "Article ", "Chapter ")):
            return False

        # Don't continue if current line is an explanation or note
        if current_line.lower() in ["explanation:", "note:", "provided:", "proviso:"]:
            return False

        # General continuation heuristic: if previous line ends with punctuation
        # or if current line doesn't look like a new hierarchy item
        return prev_line.endswith((".", ":", ";")) or len(current_line.split()) <= 3

    def find_legal_sections(self, text: str) -> list[str]:
        """
        Find and extract legal sections from text.

        Args:
            text: Legal text

        Returns:
            List of section markers found
        """
        sections = []

        # Section patterns
        section_patterns = [
            r"\b(?:Section|Sec\.|§)\s*\d+(?:\s*\(.*\))?",
            r"\bClause\s*[a-zA-Z]\d*(?:\s*\(.*\))?",
            r"\bArticle\s*\d+(?:\s*\(.*\))?",
            r"\bChapter\s*\d+(?:\s*\(.*\))?",
            r"\b\d+\s*\.\s*[a-zA-Z]\d*(?:\s*\(.*\))?",
        ]

        for pattern in section_patterns:
            sections.extend(re.findall(pattern, text, re.IGNORECASE))

        return list(set(sections))  # Remove duplicates

    def extract_citations_from_text(self, text: str) -> list[dict[str, str]]:
        """
        Extract legal citations from text.

        Args:
            text: Legal text

        Returns:
            List of citation dictionaries
        """
        citations = []

        # Supreme Court citations
        sc_pattern = r"\((\d{4})\s*SC\s*([A-Z]?\d+\/\d+)\)"
        for match in re.finditer(sc_pattern, text, re.IGNORECASE):
            citations.append({
                "type": "supreme_court",
                "year": match.group(1),
                "citation": match.group(2),
                "full": match.group(0),
            })

        # High Court citations
        hc_pattern = r"\([Hh]onorable\s*[Jj]udicial?[\w\s]*-[Hh]c\s*([A-Z]?\d+\/\d+)\)"
        for match in re.finditer(hc_pattern, text):
            citations.append({"type": "high_court", "citation": match.group(1), "full": match.group(0)})

        # Statute references
        statute_pattern = r"\b([A-Z][a-z\s]+(?:Act|Code|Rules|Regulations))\b(?:\s+\d{4})?"
        for match in re.finditer(statute_pattern, text, re.IGNORECASE):
            citations.append({"type": "statutory", "name": match.group(1), "full": match.group(0)})

        return citations

    def clear_cache(self):
        """Clear the text cleaning cache."""
        with self._lock:
            self._cache.clear()
