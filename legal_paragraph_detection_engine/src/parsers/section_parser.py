"""Section and subsection parsing module

Extracts section numbers, subsection information, and hierarchical section data
from legal documents.
"""

import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from ..utils.cache import evict_if_full, stable_key


class SectionType(Enum):
    """Types of sections in legal documents."""

    MAIN_SECTION = "main_section"
    SUBSECTION = "subsection"
    SUBSUBSECTION = "subsubsection"
    PARAGRAPH = "paragraph"
    SUBPARAGRAPH = "subparagraph"
    ROMAN_SECTION = "roman_section"


@dataclass
class SectionData:
    """Extracted section data."""

    section_number: str | None
    section_type: SectionType
    full_label: str
    level: int
    start_line: int
    end_line: int
    title: str | None = None
    content: str | None = None
    parent_id: str | None = None
    children: list["SectionData"] | None = None

    def __post_init__(self):
        if self.children is None:
            self.children = []


class SectionParser:
    """Parses sections and subsections from legal documents."""

    # Section number patterns
    SECTION_PATTERNS: ClassVar[dict[str, list[str]]] = {
        # Main sections (Arabic numerals)
        "main_section": [
            r"\bSection\s*(\d+)",
            r"\bSec\s*(\d+)",
            r"§\s*(\d+)",
            r"^\s*(\d+)\s*\.\s*(?:[A-Z][a-z]+)\s*$",
            r"^\s*(\d+)\s*$",
        ],
        # Subsections (parentheses)
        "subsection": [
            r"\bSub-section\s*\((\d+)\)\b",
            r"^\s*\((\d+)\)\s*\w",
            r"^\s*\((\d+)\)\s*$",
        ],
        # Sub-subsections (letters)
        "subsubsection": [
            r"\(([a-zA-Z])\)\s*\w",
            r"^\s*\(([a-zA-Z])\)\s*$",
            r"^\s*([a-zA-Z])\s*\.\s*\w",
            r"^\s*([a-zA-Z])\s*\(\s*[a-zA-Z]\s*\)",
        ],
        # Paragraphs
        "paragraph": [
            r"\bParagraph\s*(\d+)\b",
            r"\bPara\s*(\d+)\b",
        ],
        # Sub-paragraphs
        "subparagraph": [
            r"\bSub-paragraph\s*(\d+)\b",
            r"^\s*(\d+)\s*\.\s*(\d+)\s+\w",
            r"^\s*(\d+)\s*\.\s*(\d+)\s*\.",
            r"^\s*(\d+)\s*\.\s*(\d+)\s*$",
        ],
        # Roman numeral sections
        "roman_section": [
            r"^\s*(i|ii|iii|iv|v|vi|vii|viii|ix|x)\s+",
            r"^\s*([ivxIVX]{1,4})\s*\.",
        ],
        # Combined hierarchical patterns
        "combined": [
            r"\b(\d+)\s*\(\s*\d+\s*\)\s*\(\s*[a-zA-Z]\s*\)",  # 1(2)(a)
            r"\b(\d+)\s*\.\s*\d+\s*\.\s*[a-zA-Z]\s*\.\s*\d+",  # 1.2.3.4.
            r"^\s*\d+\.\d+\.\d+\.\d+\s*$",
            # Pure parenthetical marker chains ("subsection markers") such as
            # ``(1)(a)`` / ``(1)(2)(a)`` / ``(i)(ii)``. These are hierarchical
            # markers, NOT section titles, and must be recognised so the
            # chunker does not silently drop them (RAG_AGENT_A_SCOPE §2.3).
            # Single markers (``(1)``, ``(a)``) are handled by the specific
            # patterns above, so only multi-marker chains reach these.
            r"^\s*(?:\(\s*\d+\s*\)\s*)+(?:\(\s*[a-zA-Z]\s*\)\s*)+$",
            r"^\s*(?:\(\s*\d+\s*\)\s*)+$",
            r"^\s*(?:\(\s*[ivxIVX]+\s*\)\s*)+$",
            # Marker chain followed by content: ``(1)(a) First clause.`` — the
            # chain is a marker prefix, not a section title (§2.3).
            r"^\(\s*\d+\s*\)\s*\(\s*[a-zA-Z]\s*\).*",
            r"^\(\s*[ivxIVX]+\s*\)\s*\(.*",
        ],
    }

    # Title patterns
    TITLE_PATTERNS: ClassVar[list[str]] = [
        r"^\s*[A-Z][a-z\s]+\s*$",  # Capitalized line (title)
        r"^\s*[A-Z][a-z\s]+(?:of\s+[A-Z][a-z\s]+)?\s*$",
        r"^\s*(?:[A-Z][a-z\s]+)\s*(?:-|:)\s*[A-Z][a-z\s]*$",  # Title: Subtitle
        r"^\s*[A-Z][A-Z\s]+\s*$",  # All caps (often title)
        r"^\s*[A-Z][a-z]+\s*=\s*[A-Z][a-z\s]*$",  # Title = Subtitle
    ]

    # Section hierarchy markers
    SECTION_HIERARCHY_MARKERS: ClassVar[list[str]] = [
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
            cache_key = stable_key(text)
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

            evict_if_full(self._cache)
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
        for _section_type, patterns in self.SECTION_PATTERNS.items():
            for pattern in patterns:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    return match
        return None

    def _determine_section_type(self, line: str, match: re.Match) -> SectionType:
        """Determine the type of section based on line content."""
        line_lower = line.lower()

        # Check for specific patterns (subparagraph must be tested before the
        # generic paragraph token so "subparagraph" is never swallowed)
        if re.search(r"\b(?:section|sec\.|§)\s*\d+", line_lower):
            return SectionType.MAIN_SECTION
        elif re.search(r"\bsub[- ]?paragraph\b", line_lower):
            return SectionType.SUBPARAGRAPH
        elif re.search(r"\b(?:paragraph|para)\b", line_lower):
            return SectionType.PARAGRAPH
        # Subsection-marker chains (``(1)(a)``, ``3(1)(a)``, ``(i)(ii)``) are
        # classified by their deepest marker — never as a main section
        # (RAG_AGENT_A_SCOPE §2.3).
        elif self._has_deep_marker_chain(line):
            return self._marker_chain_section_type(line)
        elif re.match(r"^\s*\(\s*\d+\s*\)", line):
            return SectionType.SUBSECTION
        elif re.match(r"^\s*\([a-zA-Z]\)", line) or re.match(r"^\s*[a-zA-Z]\s*\.\s", line):
            return SectionType.SUBSUBSECTION
        elif re.match(r"^\s*[ivxIVX]{1,4}\s+", line) or re.match(r"^\s*[ivxIVX]{1,4}\s*\.", line):
            return SectionType.ROMAN_SECTION

        # Default to main section
        return SectionType.MAIN_SECTION

    def _has_deep_marker_chain(self, line: str) -> bool:
        """True when the line leads with a multi-marker chain.

        Matches ``(1)(a) ...``, ``3(1)(a) ...`` and ``(i)(ii) ...`` — lines
        whose leading parenthetical markers encode subsection/sub-subsection
        depth (RAG_AGENT_A_SCOPE §2.3).
        """
        stripped = line.lstrip()
        return bool(
            re.match(r"^\(\s*\d+\s*\)\s*\(", stripped)
            or re.match(r"^\d+\s*\(\s*\d+\s*\)\s*\(", stripped)
            or re.match(r"^\(\s*[ivxIVX]+\s*\)\s*\(", stripped)
        )

    def _marker_chain_section_type(self, line: str) -> SectionType:
        """Classify a marker chain by its deepest marker.

        Mapping (RAG_AGENT_A_SCOPE §2.3): ``(1)`` → SUBSECTION,
        ``(1)(a)`` → SUBSUBSECTION, ``(1)(a)(i)`` → PARAGRAPH, chains of
        four or more markers → SUBPARAGRAPH.
        """
        markers = re.findall(r"\(\s*([^()]+?)\s*\)", line)
        if len(markers) >= 4:
            return SectionType.SUBPARAGRAPH
        deepest = markers[-1] if markers else ""
        if re.fullmatch(r"[ivxIVX]+", deepest):
            return SectionType.PARAGRAPH
        if re.fullmatch(r"[a-zA-Z]+", deepest):
            return SectionType.SUBSUBSECTION
        return SectionType.SUBSECTION

    def _extract_section_number(self, line: str, match: re.Match) -> str | None:
        """Extract section number from matched line.

        A parenthetical such as ``(1)`` — or a pure marker chain such as
        ``(1)(a)`` — is a subsection *marker* and does not carry a section
        number of its own (spec decision, F-06a, extended to marker chains by
        RAG_AGENT_A_SCOPE §2.3).
        """
        # A line consisting purely of parenthetical markers has no section number
        if re.fullmatch(r"(?:\(\s*[\da-zA-Z]+\s*\)\s*)+", line.strip()):
            return None

        # A line leading with a subsection-marker chain (``(1)(a) First``)
        # references a section defined elsewhere — the markers are not a
        # section number (spec decision F-06a, extended to chains by §2.3).
        if line.lstrip().startswith("(") and self._has_deep_marker_chain(line):
            return None

        # Try to find the section number in the match
        groups = match.groups()
        if groups and groups[0]:
            return str(groups[0])

        # Try to extract from the line directly
        patterns = [
            r"(?:Section|Sec|§)\s*(\d+)",
            r"\b(?:Paragraph|Para)\s*(\d+)",
            r"^\s*\((\d+)\)",
            r"^\s*\(([a-zA-Z])\)",
            r"^\s*([a-zA-Z])\s*\.",
            r"^\s*([ivxIVX]{1,4})\s+",
            r"^\s*([ivxIVX]{1,4})\s*\.",
            r"^\s*(\d+)\s*$",
        ]

        for pattern in patterns:
            pattern_match = re.search(pattern, line)
            if pattern_match:
                return pattern_match.group(1)

        # Use the matched text itself
        return str(match.group(0).strip())

    def _extract_section_title(self, line: str, section_type: SectionType) -> str | None:
        """Extract title from section line if present.

        Subsection-marker chains (``(1)(a)``) are NEVER titles — they are
        stripped from the front of the line first, and when the line contains
        nothing but markers the title is ``None`` (RAG_AGENT_A_SCOPE §2.3).
        """
        # Strip a leading "Section N" / number / marker-chain prefix so
        # "Section 3(1)(a) Powers of the Food Authority" yields
        # "Powers of the Food Authority" — never "(1)(a)".
        stripped = self._strip_marker_prefix(line)
        if stripped is None:
            return None
        if stripped != line.strip():
            # A leading marker prefix was present; the remainder (if any) is
            # the title. It must contain real words (never bare markers/digits)
            # and must not itself begin with a section marker. A substring
            # check is deliberately avoided so words like "subsection" (which
            # contains "section") are accepted as titles.
            if stripped and re.search(r"[A-Za-z]", stripped) and not re.match(
                r"^(?:section|clause|provided)\b", stripped.lower()
            ):
                return stripped
            return None

        # Look for title patterns
        for pattern in self.TITLE_PATTERNS:
            match = re.match(pattern, line)
            if match:
                title = match.group(0).strip()
                # Exclude known section markers
                if not any(marker in title.lower() for marker in ["section", "clause", "provided"]):
                    return title

        # Check for "Section 3: Title" or "Section 3 Title" format
        match = re.match(r"^\s*(?:Section|Sec\.|§)\s*\d+\s*[:\-]?\s*(.+)", line, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            if title and not any(marker in title.lower() for marker in ["section", "clause", "provided"]):
                return title

        # Check if line starts with section number and has text after
        if section_type != SectionType.ROMAN_SECTION:
            match = re.match(r"^\s*\d+\s*\.?\s*([A-Z][a-z\s]+)", line)
            if match:
                return match.group(1).strip()

        # Check for Roman numeral sections with title
        if section_type == SectionType.ROMAN_SECTION:
            match = re.match(r"^\s*[ivxIVX]{1,4}\s+(.+)", line)
            if match:
                return match.group(1).strip()

        # Check for "(1)" with text after
        if section_type == SectionType.SUBSECTION:
            match = re.match(r"^\s*\(\d+\)\s*(.+)", line)
            if match:
                return match.group(1).strip()

        # Check for "(a)" with text after
        if section_type == SectionType.SUBSUBSECTION:
            match = re.match(r"^\s*\([a-zA-Z]\)\s*(.+)", line)
            if match:
                return match.group(1).strip()

        return None

    def _strip_marker_prefix(self, line: str) -> str | None:
        """Strip a leading section-number / marker-chain prefix from ``line``.

        ``"Section 3(1)(a) Powers"`` → ``"Powers"``, ``"(1)(a) First"`` →
        ``"First"``. Returns ``None`` when nothing remains (the line is only
        markers), otherwise the stripped remainder.
        """
        stripped = re.sub(
            r"^\s*(?:Section|Sec\.|§)\s*\d+\s*[:\-]?\s*", "", line.strip(), flags=re.IGNORECASE
        )
        stripped = re.sub(r"^\s*\d+\s*\.?\s*", "", stripped)
        stripped = re.sub(r"^(?:\(\s*[\da-zA-Z]+\s*\)\s*)+", "", stripped).strip()
        return stripped or None

    def _calculate_level(self, line: str) -> int:
        """Calculate hierarchy level of section.

        Level counts hierarchy *components*: a leading section number counts as
        one component (recognised both as a bare digit and behind a
        ``Section``/``Sec``/``§`` marker) and each ``(...)`` group / dotted
        segment adds one. A ``Section``-prefixed header that also carries
        parenthetical markers gets one extra component, so a subsection-marker
        chain pushes the level to 4+ as required (RAG_AGENT_A_SCOPE §2.3):
        ``Section 3`` → 1, ``(1)`` → 1, ``(a)`` → 1, ``1.2.3`` → 3,
        ``1(2)(a)`` → 3, ``Section 3(1)(a)`` → 4, ``3(1)(a)(i)`` → 4.
        """
        paren_groups = len(re.findall(r"\([^()]*\)", line))
        dot_segments = line.count(".")
        has_section_keyword = bool(re.match(r"^\s*(?:Section|Sec\.|§)\s*\d+", line, re.IGNORECASE))
        leading_number = 1 if re.match(r"^\s*\d+", line) or has_section_keyword else 0
        marker_bonus = 1 if has_section_keyword and paren_groups else 0
        return max(leading_number + paren_groups + dot_segments + marker_bonus, 1)

    def _build_hierarchy(self, sections: list[SectionData]) -> list[SectionData]:
        """Build hierarchy relationships between sections."""
        # Sort by level and number (None-safe for marker sections without a number)
        sections.sort(key=lambda x: (x.level, str(x.section_number or "")))

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
                    and section.section_number
                    and s.section_number
                    and (
                        str(section.section_number) in str(s.section_number)
                        or str(s.section_number) in str(section.section_number)
                    )
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
                parent = level_map[section.parent_id]
                if parent.children is None:
                    parent.children = []
                parent.children.append(section)

        return root_sections

    def clear_cache(self):
        """Clear the section parsing cache."""
        with self._lock:
            self._cache.clear()


# End of section_parser.py
