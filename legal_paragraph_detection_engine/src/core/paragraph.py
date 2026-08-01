"""Core paragraph boundary detection module

Detects paragraph boundaries, explanations, notes, provisos, schedules, tables,
and provides text cleaning and normalization for legal documents.
"""

import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

from ..utils.cache import evict_if_full, stable_key


class ParagraphType(Enum):
    """Types of paragraphs in legal documents."""

    NORMAL = "normal"
    SECTION = "section"
    CLAUSE = "clause"
    SUBCLAUSE = "subclause"
    EXPLANATION = "explanation"
    NOTE = "note"
    PROVISO = "proviso"
    SCHEDULE = "schedule"
    TABLE = "table"
    SUBSECTION = "subsection"
    SUBSUBSECTION = "subsubsection"
    BOUNDARY = "boundary"


@dataclass
class ParagraphInfo:
    """Information about a detected paragraph."""

    id: str
    text: str
    paragraph_type: ParagraphType
    start_line: int
    end_line: int
    section: str | None = None
    clause: str | None = None
    subclause: str | None = None
    hierarchy_depth: int = 0
    word_count: int = 0
    metadata: dict[str, Any] | None = None
    parent_id: str | None = None
    children: list[str] | None = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.children is None:
            self.children = []


@dataclass
class BoundaryInfo:
    """Information about a paragraph boundary."""

    line: int
    boundary_type: str  # 'text', 'section', 'hierarchy', 'empty'
    content: str | None = None
    confidence: float = 0.0


class TextNormalizer:
    """Cleans and normalizes legal text before parsing."""

    # Patterns to preserve (important legal elements)
    PRESERVE_PATTERNS: ClassVar[list[str]] = [
        r"\(\d{4}\s*SC\s*[\d\w/]+\)",  # Supreme Court citations
        r"\([Hh]onorable\s*[Jj]ud\w+\s*[Hh]c\s*[\d/]+\)",  # High Court citations
        r"\[[A-Z][a-z\s,/&]+\d+\]",  # Statutory references
        r"\[[A-Za-z0-9\s.&,]+\s*\(\d{4}\)[,\s]*\d+\]",  # Case citations
        r"Section\s*\d+",  # Section references
        r"Sub-section\s*\d+",  # Subsection references
        r"Clauses?\s*[0-9]",  # Clause references
        r"Article\s*\d+",  # Article references
        r"Chapter\s*\d+",  # Chapter references
    ]

    # Patterns to clean (inline removal)
    CLEAN_PATTERNS: ClassVar[list[str]] = [
        r"Page\s*\d+\s*",
        r"\d+\s*\*\s*",
        r"(?:Illustration|Example|Figure)\s*\d+\s*:",
        r"(?:Table|Fig)\s*[A-Z]\s*\d+\s*:",
        r"(?:Said|And)\s*(?:to|that)\s*$",
        r"The\s*(?:said|above)\s*(?:as)\s*$",
    ]

    # Whitespace normalization
    WHITESPACE_PATTERNS: ClassVar[list[tuple[str, str]]] = [
        (r"\r\n", "\n"),
        (r"\t", " "),
        (r"[^\S\n]{4,}", "    "),  # Multiple spaces/tabs (excluding newlines)
        (r" \n", "\n"),  # Space before newline
    ]

    def __init__(self, paragraph_boundary_chars: int = 100):
        self._cache: dict[str, str] = {}
        self._lock = threading.RLock()
        self._paragraph_boundary_chars = paragraph_boundary_chars

    def clean_text(self, text: str) -> str:
        """Clean and normalize legal text."""
        with self._lock:
            # Check cache first using a stable sha256 key
            cache_key = stable_key(text)
            if cache_key in self._cache:
                return self._cache[cache_key]

            # Single-pass preservation using regex callbacks to avoid O(n²) complexity
            def preserve_match(match):
                return match.group(0).strip()

            preserved = text
            for pattern in self.PRESERVE_PATTERNS:
                preserved = re.sub(pattern, preserve_match, preserved, flags=re.IGNORECASE)

            # Apply cleanup patterns (inline removal)
            cleaned = preserved
            for pattern in self.CLEAN_PATTERNS:
                cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

            # Normalize whitespace
            normalized = cleaned
            for old_pattern, new_pattern in self.WHITESPACE_PATTERNS:
                normalized = re.sub(old_pattern, new_pattern, normalized)

            # Collapse multiple spaces into single space
            normalized = re.sub(r" +", " ", normalized)

            # Remove duplicate empty lines while preserving legal structure
            normalized = re.sub(r"\n\s*\n\s*\n+", "\n\n", normalized)

            result = normalized.strip()
            evict_if_full(self._cache)
            self._cache[cache_key] = result
            return result

    def find_legal_sections(self, text: str) -> list[str]:
        """Find legal section references in text.

        Args:
            text: Legal document text

        Returns:
            List of section reference strings
        """
        sections: list[str] = []
        # Pattern to match section references including nested patterns
        section_pattern = r"Section\s*\d+(?:\([^)]+\))*"
        matches = re.findall(section_pattern, text, re.IGNORECASE)
        sections.extend(matches)
        return sections

    def extract_citations_from_text(self, text: str) -> list[dict[str, str]]:
        """Extract citations from text.

        Args:
            text: Legal document text

        Returns:
            List of citation dictionaries with type and reference
        """
        citations: list[dict[str, str]] = []

        # Supreme Court citations: (2020 SC 123/456)
        sc_pattern = r"\((\d{4})\s*SC\s*[\d\w/]+\)"
        for match in re.finditer(sc_pattern, text):
            citations.append({
                "type": "supreme_court",
                "reference": match.group(0),
                "year": match.group(1),
            })

        # Statutory references: [M1/2023] or [Some Act]
        statutory_pattern = r"\[[A-Z][A-Za-z0-9\s,/&]*\d+\]"
        for match in re.finditer(statutory_pattern, text):
            citations.append({
                "type": "statutory",
                "reference": match.group(0),
            })

        return citations

    def split_into_paragraphs(self, text: str) -> list[str]:
        """Split text into paragraphs based on legal document conventions."""
        lines = text.split("\n")
        paragraphs: list[str] = []
        current_para: list[str] = []
        line_num = 0

        while line_num < len(lines):
            line = lines[line_num].strip()
            if not line:
                if self._is_paragraph_boundary(current_para, lines, line_num):
                    if current_para:
                        paragraphs.append("\n".join(current_para))
                    current_para = []
                line_num += 1
                continue

            hierarchy_break = self._detect_hierarchy_break(line)
            if hierarchy_break and current_para:
                paragraphs.append("\n".join(current_para))
                current_para = []

            current_para.append(line)
            line_num += 1

        if current_para:
            paragraphs.append("\n".join(current_para))

        return paragraphs

    def _is_paragraph_boundary(self, current_para: list[str], lines: list[str], line_num: int) -> bool:
        """Determine if empty line represents a paragraph boundary."""
        if not current_para:
            return False

        last_line = current_para[-1].strip() if current_para else ""
        next_line = lines[line_num + 1] if line_num + 1 < len(lines) else ""

        if next_line and self._detect_hierarchy_break(next_line.strip()):
            return True

        if re.search(r"\b(Section|Sec\.|§)\s*\d+", last_line):
            return True

        if re.search(r"\b\d+\s*\(.*?\)", last_line):
            return True

        if re.search(r"explanation", last_line, re.IGNORECASE):
            return True

        return len(" ".join(current_para).strip()) > self._paragraph_boundary_chars

    def _detect_hierarchy_break(self, line: str) -> bool:
        """Detect if line represents a hierarchy break."""
        hierarchy_patterns = [
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*$",
            r"^\s*\[\s*[a-zA-Z]\s*\]\s*$",
            r"^\s*[ivxIVX]{1,4}\s*$",
            r"^\s*\d+\.\s*\d+\.\s*\d+\.",
            r"^\s*\d+\.\d+\.\d+\s*$",
            r"^\s*\d+\s*\(.*?\)",
            r"^\s*[A-Z][a-z]+\s*:?\s*$",
        ]

        for pattern in hierarchy_patterns:
            if re.match(pattern, line):
                return True

        special_patterns = [
            r"^\s*Explanation\s*$",
            r"^\s*Note\s*:",
            r"^\s*Schedule\s*[IVX0-9]*",
            r"^\s*Table\s*[IVX0-9]*",
            r"^\s*Proviso\s*$",
            r"^\s*Provided\s*$",
        ]

        return any(re.match(pattern, line, re.IGNORECASE) for pattern in special_patterns)

    def clear_cache(self):
        """Clear the normalization cache."""
        with self._lock:
            self._cache.clear()


class ParagraphBoundaryDetector:
    """Detects paragraph boundaries and classifies paragraph types."""

    # Legal document structural markers
    STRUCTURAL_MARKERS: ClassVar[dict[str, list[str]]] = {
        "section": [
            r"^\s*(?:Section|Sec\.|§)\s*\d+",
            r"^\s*§?\s*\d+\s*$",
            r"^\s*Clause\s*\d+",
            r"^\s*Article\s*\d+",
            r"^\s*Chapter\s*\d+",
        ],
        "subsection": [
            r"^\s*Sub-section\s*\(\d+\)",
            r"^\s*\(\s*\d+\s*\)\s*(?:of\s*(?:Section|Clause))",
        ],
        "subsub_section": [
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*(?:of\s*(?:Subsection|Clause))",
            r"^\s*[a-zA-Z]\s*\.\s*(?:of\s*(?:Section|Clause))",
        ],
        "explanation": [
            r"^\s*Explanation\s*$",
            r"^\s*Explanation of\s*(?:the)?\s*(?:above)",
            r"^\s*(?:Illustration|Example)\s*\d*\s*$",
            r"^\s*Provided.*?\s*$",
        ],
        "note": [
            r"^\s*Note\s*:",
            r"^\s*Notes\s*:",
            r"^\s*IMPORTANT\s*N[O]*T[E]*:",
            r"^\s*[A-Z][a-z]+\s*:?\s*$",
            r"^\s*See\s*(?:also)?\s*[A-Z]",
        ],
        "proviso": [
            r"^\s*Proviso\s*$",
            r"^\s*Provided\s*$",
            r"^\s*BE IT FURTHER PROVIDED",
            r"^\s*Provided further",
            r"^\s*Except\s*(?:that)?\s*$",
        ],
        "schedule": [
            r"^\s*Schedule\s*[IVX0-9]*",
            r"^\s*Schedule\s*(?:of\s*(?:the)?)?\s*[A-Z]\w*",
        ],
        "table": [
            r"^\s*Table\s*[IVX0-9]*",
            r"^\s*Table\s*[A-Z]\w*",
            r"^\s*T\s*\d+\s*:",
            r"^\s*TABLE\s*$",
        ],
        "clause": [
            r"^\s*\d+\s*\.\s*[a-zA-Z]\s*\.\s*\d+\.",
            r"^\s*\(\s*\d+\s*\)\s*\(\s*[a-zA-Z]\s*\)",
            r"^\s*\d+\s*\.\s*\d+\.",
        ],
        "subclause": [
            r"^\s*\(\s*[a-zA-Z]\s*\)",
            r"^\s*\[\s*[a-zA-Z]\s*\]",
            r"^\s*[ivxIVX]{1,4}\s*$",
        ],
    }

    # Citation patterns
    CITATION_PATTERNS: ClassVar[list[str]] = [
        r"\(\d{4}\s*SC\s*[\d\w/]+\)",
        r"\([Hh]onorable\s*[Jj]ud\w+\s*[Hh]c\s*[\d/]+\)",
        r"\[[A-Z][a-z\s,/&]+\d+\]",
        r"\[[A-Za-z0-9\s.&,]+\s*\(\d{4}\)[,\s]*\d+\]",
        r"\bSection\s*\d+\s*of",
        r"\bClause\s*[a-zA-Z]",
        r"\bArticle\s*[a-zA-Z]",
        r"\bChapter\s*[a-zA-Z]",
    ]

    def __init__(self):
        self._cache: dict[str, list[ParagraphInfo]] = {}
        self._lock = threading.RLock()

    def detect_paragraph_boundaries(self, text: str) -> list[ParagraphInfo]:
        """Detect paragraph boundaries and classify paragraph types.

        Segmentation rules:
        - A blank line always ends the current paragraph.
        - A line that starts a new structural marker (section/clause/
          subclause/explanation/note/proviso/schedule/table) also ends the
          current paragraph and begins a new one.
        - Any other line continues the current paragraph.

        Args:
            text: Clean legal document text

        Returns:
            List of ParagraphInfo objects
        """
        with self._lock:
            cache_key = stable_key(text)
            if cache_key in self._cache:
                return self._cache[cache_key]

            lines = text.split("\n")
            paragraphs: list[ParagraphInfo] = []
            current_para_lines: list[str] = []
            start_line = 0

            for line_num, line in enumerate(lines):
                line_stripped = line.strip()

                if not line_stripped:
                    # Blank line: flush the current paragraph (if any)
                    if current_para_lines:
                        para_info = self._create_paragraph_info(
                            current_para_lines, start_line, line_num - 1, len(paragraphs)
                        )
                        if para_info:
                            paragraphs.append(para_info)
                        current_para_lines = []
                    start_line = line_num + 1
                    continue

                # A new structural marker begins a new paragraph
                if current_para_lines and self._starts_new_structure(line_stripped):
                    para_info = self._create_paragraph_info(
                        current_para_lines, start_line, line_num - 1, len(paragraphs)
                    )
                    if para_info:
                        paragraphs.append(para_info)
                    current_para_lines = []
                    start_line = line_num

                current_para_lines.append(line)

            # Flush the final paragraph
            if current_para_lines:
                para_info = self._create_paragraph_info(current_para_lines, start_line, len(lines) - 1, len(paragraphs))
                if para_info:
                    paragraphs.append(para_info)

            result = sorted(paragraphs, key=lambda x: (x.start_line, x.hierarchy_depth))
            evict_if_full(self._cache)
            self._cache[cache_key] = result
            return result

    def _starts_new_structure(self, line: str) -> bool:
        """Determine whether a line begins a new structural unit.

        A line starts a new structure when it matches a structural marker,
        matches a hierarchy-level pattern (e.g. ``3(1)(a)``), or classifies
        as a non-normal paragraph type.
        """
        if self._detect_structure_start(line):
            return True
        if self._detect_hierarchy_level(line) is not None:
            return True
        return self._classify_paragraph_type(line) != ParagraphType.NORMAL

    def _detect_hierarchy_level(self, line: str) -> dict[str, Any] | None:
        """Detect the hierarchy level of a line.

        Returns a dict with 'type' and 'depth' keys, or None if not a hierarchy element.
        """
        # Check for section patterns
        if re.search(r"\b(?:Section|Sec\.|§)\s*\d+", line, re.IGNORECASE):
            return {"type": "section", "label": line.strip(), "depth": 1}

        # Check for simple numbered sections (e.g., "3")
        if re.match(r"^\s*\d+\s*$", line):
            return {"type": "section", "label": line.strip(), "depth": 1}

        # Check for clause patterns (e.g., 3.1, 1.2.3.4)
        if re.match(r"^\s*\d+\.\d+", line):
            depth = line.count(".") + 1
            return {"type": "clause", "label": line.strip(), "depth": depth}

        # Check for nested clause patterns (e.g., 3(1)(a))
        if re.match(r"^\s*\d+\s*\(", line):
            depth = 1 + line.count("(")
            return {"type": "clause", "label": line.strip(), "depth": depth}

        # Check for Roman numeral patterns
        if re.match(r"^\s*[ivxIVX]{1,4}\s*$", line):
            return {"type": "clause", "label": line.strip(), "depth": 1}

        return None

    def _classify_paragraph_type(self, line: str) -> ParagraphType:
        """Classify paragraph type based on line content."""
        # Check for schedule
        if re.match(r"^\s*Schedule\s*", line, re.IGNORECASE):
            return ParagraphType.SCHEDULE
        # Check for table
        if re.match(r"^\s*Table\s*", line, re.IGNORECASE):
            return ParagraphType.TABLE
        # Check for explanation (allow trailing punctuation)
        if re.match(r"^\s*Explanation\s*[:.]?\s*$", line, re.IGNORECASE):
            return ParagraphType.EXPLANATION
        # Check for proviso (allow content following the marker)
        if re.match(r"^\s*(?:Proviso|Provided|BE IT FURTHER PROVIDED|Provided further|Except)", line, re.IGNORECASE):
            return ParagraphType.PROVISO
        # Check for note
        if re.match(r"^\s*Note\s*:", line, re.IGNORECASE):
            return ParagraphType.NOTE
        # Check for section
        if re.match(r"^\s*(?:Section|Sec\.|§)\s*\d+", line, re.IGNORECASE):
            return ParagraphType.SECTION
        # Check for clause (e.g., 3.1, 1.2.3, 3(1)(a))
        if re.match(r"^\s*\d+\.\d+", line):
            return ParagraphType.CLAUSE
        if re.match(r"^\s*\d+\s*\(\d+\)", line):
            return ParagraphType.CLAUSE
        # Check for subclause (e.g., (a), [a], (i))
        if re.match(r"^\s*\(\s*[a-zA-Z]\s*\)", line):
            return ParagraphType.SUBCLAUSE
        if re.match(r"^\s*\[\s*[a-zA-Z]\s*\]", line):
            return ParagraphType.SUBCLAUSE
        if re.match(r"^\s*[ivxIVX]{1,4}\s*$", line):
            return ParagraphType.SUBCLAUSE
        # Check for subsection (e.g., (1))
        if re.match(r"^\s*\(\s*\d+\s*\)", line):
            return ParagraphType.SUBSECTION

        return ParagraphType.NORMAL

    def _detect_structure_end(self, line: str) -> bool:
        """Detect if line ends a structural unit."""
        hierarchy_enders = [
            r"^\s*\d+\s*\.\s*\d+\s*\.\s*\d+\s*\.\s*$",
            r"^\s*\d+\.\d+\.\d+\.\d+\.\s*$",
            r"^\s*\d+\.\d+\.\d+\.\s*$",
            r"^\s*\d+\.\d+\.\s*$",
            r"^\s*\d+\.\d+\.\d+\.\d+\s*$",
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*\.\s*$",
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*$",
            r"^\s*\[\s*[a-zA-Z]\s*\]\s*$",
            r"^\s*[ivxIVX]{1,4}\s*$",
            r"^\s*\d+\.1\s*$",
            r"^\s*\d+\s*\.\s*$",
        ]

        for pattern in hierarchy_enders:
            if re.match(pattern, line):
                return True

        return bool(re.search(r"explanation", line, re.IGNORECASE))

    def _detect_structure_start(self, line: str) -> bool:
        """Detect if line starts a new structure."""
        for _category, patterns in self.STRUCTURAL_MARKERS.items():
            for pattern in patterns:
                if re.match(pattern, line, re.IGNORECASE):
                    return True

        return False

    def _detect_hierarchy_break(self, line: str) -> bool:
        """Detect if line represents a hierarchy break."""
        hierarchy_patterns = [
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*$",
            r"^\s*\[\s*[a-zA-Z]\s*\]\s*$",
            r"^\s*[ivxIVX]{1,4}\s*$",
            r"^\s*\d+\.\s*\d+\.\s*\d+\.",
            r"^\s*\d+\.\d+\.\d+\s*$",
            r"^\s*\d+\s*\(.*?\)",
            r"^\s*[A-Z][a-z]+\s*:?\s*$",
        ]

        for pattern in hierarchy_patterns:
            if re.match(pattern, line):
                return True

        special_patterns = [
            r"^\s*Explanation\s*$",
            r"^\s*Note\s*:",
            r"^\s*Schedule\s*[IVX0-9]*",
            r"^\s*Table\s*[IVX0-9]*",
            r"^\s*Proviso\s*$",
            r"^\s*Provided\s*$",
        ]

        return any(re.match(pattern, line, re.IGNORECASE) for pattern in special_patterns)

    def _create_paragraph_info(
        self, lines: list[str], start_line: int, end_line: int, para_index: int
    ) -> ParagraphInfo | None:
        """Create ParagraphInfo from lines."""
        text = " ".join(line.strip() for line in lines if line.strip())
        if not text:
            return None

        first_line = lines[0].strip() if lines else ""
        para_type = self._classify_paragraph_type(first_line)

        section = self._extract_section_number(first_line)
        clause = self._extract_clause_number(first_line)
        subclause = self._extract_subclause_number(first_line)
        hierarchy_depth = self._calculate_hierarchy_depth(first_line)

        word_count = len(text.split())

        return ParagraphInfo(
            id=f"para_{start_line}_{para_index}",
            text=text,
            paragraph_type=para_type,
            start_line=start_line,
            end_line=end_line,
            section=section,
            clause=clause,
            subclause=subclause,
            hierarchy_depth=hierarchy_depth,
            word_count=word_count,
            metadata={"original_lines": lines},
        )

    def _extract_section_number(self, line: str) -> str | None:
        """Extract section number from line."""
        patterns = [
            r"(?:Section|Sec\.|Sec|§)\s*(\d+)",
            r"^\s*(\d+)\s*$",
        ]

        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                return match.group(1)

        return None

    def _extract_clause_number(self, line: str) -> str | None:
        """Extract clause number from line."""
        patterns = [
            r"\b(\d+)\s*\.\s*[a-zA-Z]",
            r"\b(\d+)\s*\(\s*[a-zA-Z]\s*\)",
            r"\b(\d+)\s*\(\d+\)",
            r"^\s*\((\d+)\)\s*\(",
            r"^\s*(\d+)\.\d+",
        ]

        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                return match.group(1)

        return None

    def _extract_subclause_number(self, line: str) -> str | None:
        """Extract subclause number from line."""
        patterns = [
            r"\(\s*([a-zA-Z])\s*\)",
            r"\[\s*([a-zA-Z])\s*\]",
            r"^\s*([ivxIVX]{1,4})\s*$",
        ]

        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                return match.group(1)

        return None

    def _calculate_hierarchy_depth(self, line: str) -> int:
        """Calculate hierarchy depth based on line structure."""
        depth = 1  # Base depth

        # Count dots
        depth += line.count(".")

        # Count each opening parenthesis
        depth += line.count("(")

        # Count each opening bracket
        depth += line.count("[")

        return max(depth, 1)

    def clear_cache(self):
        """Clear the boundary detection cache."""
        with self._lock:
            self._cache.clear()


# End of paragraph.py
