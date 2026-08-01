"""Section and subsection parsing module

Extracts section numbers, subsection information, and hierarchical section data
from legal documents.
"""

import re
import threading
from dataclasses import dataclass
from enum import Enum


class SectionType(Enum):
    """Types of sections in legal documents."""

    MAIN_SECTION = "main_section"
    SUBSECTION = "subsection"
    SUBSUBSECTION = "subsubsection"
    PARAGRAPH = "paragraph"
    SUBPARAGRAPH = "subparagraph"


@dataclass
class SectionData:
    """Extracted section data."""

    section_number: str
    section_type: SectionType
    full_label: str
    level: int
    start_line: int
    end_line: int
    title: str | None = None
    content: str | None = None
    parent_id: str | None = None
    children: list["SectionData"] = None

    def __post_init__(self):
        if self.children is None:
            self.children = []


class SectionParser:
    """Parses sections and subsections from legal documents."""

    # Section number patterns
    SECTION_PATTERNS = {
        # Main sections (Arabic numerals)
        "main_section": [
            r"\bSection\s*(\d+)\b",
            r"\bSec\s*(\d+)\b",
            r"\b§\s*(\d+)\b",
            r"^\s*(\d+)\s*\.\s*(?:[A-Z][a-z]+)\s*$",
            r"^\s*(\d+)\s*$",
        ],
        # Subsections (parentheses)
        "subsection": [
            r"\bSub-section\s*\((\d+)\)\b",
            r"\b\((\d+)\)\s*of\s*(?:Section|Clause)",
            r"^\s*\((\d+)\)\s*$",
            r"^\s*(\d+)\s*\.\s*\(",
        ],
        # Sub-subsections (letters)
        "subsubsection": [
            r"\b\([a-zA-Z]\)\s*of\s*(?:Section|Subsection)",
            r"^\s*\([a-zA-Z]\)\s*$",
            r"^\s*[a-zA-Z]\s*\.\s*",  # a. or a.b.
            r"^\s*[a-zA-Z]\s*\(\s*[a-zA-Z]\s*\)",  # a(b)
        ],
        # Paragraphs
        "paragraph": [
            r"\bParagraph\s*(\d+)\b",
            r"\bPara\s*(\d+)\b",
            r"^\s*\d+\s*\.\s*\d+\.",
            r"^\s*\d+\s*\.\s*\d+\.",
        ],
        # Sub-paragraphs
        "subparagraph": [
            r"\bSub-paragraph\s*(\d+)\b",
            r"^\s*\d+\s*\.\s*\d+\s*\.\s*\d+\.",
            r"^\s*[a-z]\s*\.\s*\d+\.",
        ],
        # Roman numeral sections
        "roman_section": [
            r"^\s*(i|ii|iii|iv|v|vi|vii|viii|ix|x)\s*\.",
            r"^\s*\([i-ivIVX]{1,4}\)(?:\s*[a-zA-Z])?",
        ],
        # Combined hierarchical patterns
        "combined": [
            r"\b(\d+)\s*\(\s*\d+\s*\)\s*\(\s*[a-zA-Z]\s*\)",  # 1(2)(a)
            r"\b(\d+)\s*\.\s*\d+\s*\.\s*[a-zA-Z]\s*\.\s*\d+",  # 1.2.3.4.
            r"^\s*\d+\.\d+\.\d+\.\d+\s*$",
        ],
    }

    # Title patterns
    TITLE_PATTERNS = [
        r"^\s*[A-Z][a-z\s]+\s*$",  # Capitalized line (title)
        r"^\s*[A-Z][a-z\s]+(?:of\s+[A-Z][a-z\s]+)?\s*$",
        r"^\s*(?:[A-Z][a-z\s]+)\s*(?:-|:)\s*[A-Z][a-z\s]*$",  # Title: Subtitle
        r"^\s*[A-Z][A-Z\s]+\s*$",  # All caps (often title)
        r"^\s*[A-Z][a-z]+\s*=\s*[A-Z][a-z\s]*$",  # Title = Subtitle
    ]

    # Section hierarchy markers
    SECTION_HIERARCHY_MARKERS = [
        r"^\s*Explanation\s*$",
        r"^\s*Provided\s*$",
        r"^\s*PROVISO\s*$",
        r"^\s*Note\s*:",
        r"^\s*Table\s*[IVX0-9]*",
        r"^\s*Schedule\s*[IVX0-9]*",
        r"^\s*For\s*(?:the)?\s*purpose\s*:",
        r"^\s*(?:Subject\s+to|Unless|Except)\s*$",
        r"^\s*[A-Z][a-z]+\s*:\s*$",
        r"^\s*(?:i)\s*$",  # Roman numeral standalone
        r"^\s*(?:ii)\s*$",
        r"^\s*(?:iii)\s*$",
        r"^\s*(?:iv)\s*$",
    ]

    def __init__(self):
        self._cache: dict[str, list[SectionData]] = {}
        self._lock = threading.RLock()

    def parse_sections(self, text: str) -> list[SectionData]:
        """Parse sections from legal document text.

        Args:
            text: Clean legal document text

        Returns:
            List of SectionData objects
        """
        with self._lock:
            # Check cache
            cache_key = hash(text)
            if cache_key in self._cache:
                return self._cache[cache_key]

            lines = text.split("\n")
            sections = []

            for line_num, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue

                section = self._extract_section_info(line, line_num)
                if section:
                    sections.append(section)

            # Build hierarchy
            sections = self._build_hierarchy(sections)

            self._cache[cache_key] = sections
            return sections

    def _extract_section_info(self, line: str, line_num: int) -> SectionData | None:
        """Extract section information from a line."""
        # First check for section header
        section_match = self._match_section_pattern(line)
        if not section_match:
            return None

        # Determine section type
        section_type = self._determine_section_type(line, section_match)

        # Extract section number
        section_number = self._extract_section_number(line, section_match)
        full_label = section_match.group(0)

        # Determine hierarchy level
        level = self._calculate_level(line)

        # Extract title (if any)
        title = self._extract_section_title(line, section_type)

        return SectionData(
            section_number=section_number,
            section_type=section_type,
            full_label=full_label,
            level=level,
            start_line=line_num,
            end_line=line_num,
            title=title,
            content=line,
        )

    def _match_section_pattern(self, line: str) -> re.Match | None:
        """Match section patterns."""
        for section_type, patterns in self.SECTION_PATTERNS.items():
            for pattern in patterns:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    return match
        return None

    def _determine_section_type(self, line: str, match: re.Match) -> SectionType:
        """Determine the type of section based on patterns."""
        # Check for specific patterns
        line_lower = line.lower()

        if re.search(r"\b(?:Section|Sec\.|§)\s*\d+", line_lower):
            return SectionType.MAIN_SECTION
        elif re.search(r"\b(?:Sub-section|subsection)\s*\(", line_lower):
            return SectionType.SUBSECTION
        elif re.search(r"\(\s*[a-zA-Z]\s*\)\s*(?:of\s*(?:Section|Subsection))", line_lower):
            return SectionType.SUBSUBSECTION
        elif re.search(r"\b(?:Paragraph|Para)\b", line_lower):
            return SectionType.PARAGRAPH
        elif re.search(r"\b(?:Sub-paragraph)\b", line_lower):
            return SectionType.SUBPARAGRAPH
        elif re.search(r"\([i-ivIVX]{1,4}\)(?:\s*[a-zA-Z])?", line_lower):
            return SectionType.ROMAN_SECTION

        # Check based on pattern used
        pattern = match.re.pattern
        if "main_section" in pattern:
            return SectionType.MAIN_SECTION
        elif "subsection" in pattern:
            return SectionType.SUBSECTION
        elif "sub subsection" in pattern.lower():
            return SectionType.SUBSUBSECTION

        # Default based on line content
        if re.match(r"^\s*\(\s*[a-zA-Z]\s*\)\s*$", line):
            return SectionType.SUBSUBSECTION
        elif re.match(r"^\s*[a-zA-Z]\s*\.\s*", line):
            return SectionType.PARAGRAPH

        return SectionType.MAIN_SECTION

    def _extract_section_number(self, line: str, match: re.Match) -> str:
        """Extract section number from matched line."""
        # Try to find the section number in the match
        patterns = [
            r"(\d+)\s*\.\s*[A-Z][a-z]*",
            r"(\d+)\s*\(",
            r"\b(\d+)\b",
            r"\([a-zA-Z]\)",
            r"\b[i-ivIVX]\b",
        ]

        for pattern in patterns:
            pattern_match = re.search(pattern, line)
            if pattern_match:
                return pattern_match.group(1)

        # Use the matched text itself
        return match.group(0).strip()

    def _extract_section_title(self, line: str, section_type: SectionType) -> str | None:
        """Extract title from section line if present."""
        # Look for title patterns
        for pattern in self.TITLE_PATTERNS:
            match = re.match(pattern, line)
            if match:
                title = match.group(0).strip()
                # Exclude known section markers
                if not any(marker in title.lower() for marker in ["section", "clause", "provided"]):
                    return title

        # Check if line starts with section number and has text after
        if section_type != SectionType.ROMAN_SECTION:
            match = re.match(r"^\s*\d+\s*\.?\s*([A-Z][a-z\s]+)", line)
            if match:
                return match.group(1).strip()

        return None

    def _calculate_level(self, line: str) -> int:
        """Calculate hierarchy level of section."""
        level = 1  # Default level

        # Count dots for hierarchical numbering
        level += line.count(".")

        # Count opening parentheses
        level += line.count("(") - line.count(")")

        # Special handling for Roman numerals
        if re.search(r"\([i-ivIVX]{1,4}\)(?:\s*[a-zA-Z])?", line):
            level += 2

        # Count brackets
        level += line.count("[") - line.count("]")

        return max(level, 1)

    def _build_hierarchy(self, sections: list[SectionData]) -> list[SectionData]:
        """Build hierarchy relationships between sections."""
        # Sort by level and number
        sections.sort(key=lambda x: (x.level, x.section_number))

        # Group sections by parent
        root_sections = []
        level_map = {}

        for section in sections:
            # Find parent section at previous level
            parent = None
            if section.level > 1:
                # Find all sections at previous level
                candidates = [
                    s
                    for s in sections
                    if s.level == section.level - 1
                    and (section.section_number in s.section_number or s.section_number in section.section_number)
                ]
                if candidates:
                    parent = candidates[-1]  # Use the last match

            section.parent_id = parent.section_number if parent else None
            level_map[section.section_number] = section

            if not parent:
                root_sections.append(section)

        # Add children to parent
        for section in sections:
            if section.parent_id and section.parent_id in level_map:
                level_map[section.parent_id].children.append(section)

        return root_sections

    def clear_cache(self):
        """Clear the section parsing cache."""
        with self._lock:
            self._cache.clear()
