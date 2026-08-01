"""Core paragraph boundary detection module

Detects paragraph boundaries, explanations, notes, provisos, schedules, tables,
and provides text cleaning and normalization for legal documents.
"""

import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar


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
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


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

    # Patterns to clean
    CLEAN_PATTERNS: ClassVar[list[str]] = [
        r"^\s*Page\s*\d+\s*$",
        r"^\s*\d+\s*\*\s*$",
        r"^\s*(?:Illustration|Example|Figure)\s*\d+\s*:",
        r"^\s*(?:Table|Fig)\s*[A-Z]\s*\d+\s*:",
        r"^\s*[A-Z]\s*=[\s\S]*?\s*\n\s*[A-Z][a-z]+\s*=",
        r"^\s*(?:Said|And)\s*(?:to|that)\s*$",
        r"^\s*The\s*(?:said|above)\s*(?:as)\s*$",
    ]

    # Whitespace normalization
    WHITESPACE_PATTERNS: ClassVar[list[tuple[str, str]]] = [
        (r"\r\n", "\n"),
        (r"\t", " "),
        (r"\s{4,}", " "),  # Multiple spaces
        (r" \n", "\n"),  # Space before newline
    ]

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._lock = threading.RLock()

    def clean_text(self, text: str) -> str:
        """Clean and normalize legal text."""
        with self._lock:
            # Check cache first
            cache_key = hash(text)
            if cache_key in self._cache:
                return self._cache[cache_key]

            # Preserve important patterns first
            preserved = text
            for pattern in self.PRESERVE_PATTERNS:
                matches = re.finditer(pattern, preserved, re.IGNORECASE)
                for match in matches:
                    # Replace with preserved version
                    preserved = preserved[: match.start()] + match.group(0).strip() + preserved[match.end() :]

            # Apply cleanup patterns
            cleaned = preserved
            for pattern in self.CLEAN_PATTERNS:
                cleaned = re.sub(pattern, "", cleaned, flags=re.MULTILINE | re.IGNORECASE)

            # Normalize whitespace
            normalized = cleaned
            for old_pattern, new_pattern in self.WHITESPACE_PATTERNS:
                normalized = re.sub(old_pattern, new_pattern, normalized)

            # Remove duplicate empty lines while preserving legal structure
            normalized = re.sub(r"\n\s*\n\s*\n+", "\n\n", normalized)

            result = normalized.strip()
            self._cache[cache_key] = result
            return result

    def split_into_paragraphs(self, text: str) -> list[str]:
        """Split text into paragraphs based on legal document conventions."""
        # First, identify paragraph boundaries
        lines = text.split("\n")
        paragraphs = []
        current_para = []
        line_num = 0

        while line_num < len(lines):
            line = lines[line_num].strip()
            if not line:
                # Empty line - check if it's a paragraph boundary
                if self._is_paragraph_boundary(current_para, lines, line_num):
                    if current_para:
                        paragraphs.append("\n".join(current_para))
                    current_para = []
                line_num += 1
                continue

            # Check for hierarchy patterns that might end paragraph
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

        last_line = current_para[-1]
        next_line = lines[line_num + 1] if line_num + 1 < len(lines) else ""

        # Check for hierarchy indicators in next line
        if next_line and self._detect_hierarchy_break(next_line.strip()):
            return True

        # Check for section markers
        if re.search(r"\b(Section|Sec\.|§)\s*\d+", last_line):
            return True

        # Check for clause patterns
        if re.search(r"\b\d+\s*\(.*?\)", last_line):
            return True

        # Check for explanatory text
        if re.search(r"explanation", last_line, re.IGNORECASE):
            return True

        # Default: empty line after substantial content
        return len(" ".join(current_para).strip()) > 100

    def _detect_hierarchy_break(self, line: str) -> bool:
        """Detect if line represents a hierarchy break."""
        # Patterns that indicate new paragraph/section
        hierarchy_patterns = [
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*$",  # (a), (b)
            r"^\s*\[\s*[a-zA-Z]\s*\]\s*$",  # [a], [b]
            r"^\s*[ivxIVX]{1,4}\s*$",  # i, ii, iii
            r"^\s*\d+\.\s*\d+\.\s*\d+\.",  # 1.2.3.
            r"^\s*\d+\s*\(.*?\)",  # 1(2), 3(4)(a)
            r"^\s*[A-Z][a-z]+\s*:?\s*$",  # Word:  (label format)
        ]

        for pattern in hierarchy_patterns:
            if re.match(pattern, line):
                return True

        # Special legal document patterns
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

        Args:
            text: Clean legal document text

        Returns:
            List of ParagraphInfo objects
        """
        with self._lock:
            # Check cache first
            cache_key = hash(text)
            if cache_key in self._cache:
                return self._cache[cache_key]

            # Split into paragraphs
            lines = text.split("\n")
            paragraphs = []
            current_para_lines = []
            start_line = 0

            for line_num, line in enumerate(lines):
                line_stripped = line.strip()

                if not line_stripped:
                    if current_para_lines and self._is_paragraph_end(current_para_lines, line_num):
                        para_info = self._create_paragraph_info(current_para_lines, start_line, line_num - 1)
                        if para_info:
                            paragraphs.append(para_info)
                        current_para_lines = []
                    elif current_para_lines:
                        current_para_lines.append(line)
                    continue

                current_para_lines.append(line)

                if line_num == len(lines) - 1:
                    para_info = self._create_paragraph_info(current_para_lines, start_line, line_num)
                    if para_info:
                        paragraphs.append(para_info)

            result = sorted(paragraphs, key=lambda x: (x.start_line, x.hierarchy_depth))
            self._cache[cache_key] = result
            return result

    def _is_paragraph_end(self, current_para: list[str], next_line_num: int) -> bool:
        """Determine if paragraph should end at empty line."""
        last_line = current_para[-1].strip() if current_para else ""

        # Check for structural markers in last line
        if self._detect_structure_end(last_line):
            return True

        # Check paragraph length
        para_text = " ".join(current_para)
        if len(para_text.split()) > 50:
            return True

        # Check for incomplete sentence (potential continuation)
        if last_line and not last_line.endswith((".", ":", ";", "!")):
            # Look ahead for continuation
            if next_line_num < len(self._working_text):
                next_line = self._working_text[next_line_num].strip()
                if self._detect_structure_start(next_line):
                    return True

        return False

    def _detect_structure_end(self, line: str) -> bool:
        """Detect if line ends a structural unit."""
        # Check for hierarchy markers
        hierarchy_enders = [
            r"^\s*\d+\s*\.\s*\d+\s*\.\s*\d+\s*$",
            r"^\s*\(\s*[a-zA-Z]\s*\)\s*$",
            r"^\s*\[\s*[a-zA-Z]\s*\]\s*$",
            r"^\s*[ivxIVX]{1,4}\s*$",
            r"^\s*(?:\w+)\s*\.\s*\d+\s*$",
        ]

        for pattern in hierarchy_enders:
            if re.match(pattern, line):
                return True

        # Check for explanatory text end
        if re.search(r"explanation", line, re.IGNORECASE):
            return True

        return False

    def _detect_structure_start(self, line: str) -> bool:
        """Detect if line starts a new structure."""
        # Check for structural markers
        for category, patterns in self.STRUCTURAL_MARKERS.items():
            for pattern in patterns:
                if re.match(pattern, line, re.IGNORECASE):
                    return True

        return False

    def _create_paragraph_info(self, lines: list[str], start_line: int, end_line: int) -> ParagraphInfo | None:
        """Create ParagraphInfo from lines."""
        text = " ".join(line.strip() for line in lines if line.strip())
        if not text:
            return None

        # Determine paragraph type
        first_line = lines[0].strip() if lines else ""
        para_type = self._classify_paragraph_type(first_line)

        # Extract hierarchy information
        section = self._extract_section_number(first_line)
        clause = self._extract_clause_number(first_line)
        subclause = self._extract_subclause_number(first_line)
        hierarchy_depth = self._calculate_hierarchy_depth(first_line)

        word_count = len(text.split())

        return ParagraphInfo(
            id=f"para_{start_line}_{len(paragraphs) if 'paragraphs' in locals() else '0'}",
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

    def _classify_paragraph_type(self, line: str) -> ParagraphType:
        """Classify paragraph type based on line content."""
        line_lower = line.lower()

        # Check in order of specificity
        if re.match(self.STRUCTURAL_MARKERS["schedule"][0], line, re.IGNORECASE):
            return ParagraphType.SCHEDULE
        elif re.match(self.STRUCTURAL_MARKERS["table"][0], line, re.IGNORECASE):
            return ParagraphType.TABLE
        elif re.match(self.STRUCTURAL_MARKERS["explanation"][0], line, re.IGNORECASE):
            return ParagraphType.EXPLANATION
        elif re.match(self.STRUCTURAL_MARKERS["proviso"][0], line, re.IGNORECASE):
            return ParagraphType.PROVISO
        elif re.match(self.STRUCTURAL_MARKERS["note"][0], line, re.IGNORECASE):
            return ParagraphType.NOTE
        elif re.match(self.STRUCTURAL_MARKERS["subsection"][0], line, re.IGNORECASE):
            return ParagraphType.SUBSECTION
        elif re.match(self.STRUCTURAL_MARKERS["subsub_section"][0], line, re.IGNORECASE):
            return ParagraphType.SUBSUBSECTION
        elif re.match(self.STRUCTURAL_MARKERS["clause"][0], line, re.IGNORECASE):
            return ParagraphType.CLAUSE
        elif re.match(self.STRUCTURAL_MARKERS["subclause"][0], line, re.IGNORECASE):
            return ParagraphType.SUBCLAUSE
        elif re.match(self.STRUCTURAL_MARKERS["section"][0], line, re.IGNORECASE):
            return ParagraphType.SECTION

        return ParagraphType.NORMAL

    def _extract_section_number(self, line: str) -> str | None:
        """Extract section number from line."""
        section_patterns = [
            r"\b(?:Section|Sec\.|§)?\s*(\d+)",
            r"\b\d+\s*$",
            r"\b\d+\s*\.\s*[A-Z]",
        ]

        for pattern in section_patterns:
            match = re.search(pattern, line)
            if match:
                return match.group(1)

        return None

    def _extract_clause_number(self, line: str) -> str | None:
        """Extract clause number from line."""
        clause_patterns = [
            r"\b(\d+)\s*\.\s*[a-zA-Z]",
            r"\b(\d+)\s*\(\s*[a-zA-Z]\s*\)",
        ]

        for pattern in clause_patterns:
            match = re.search(pattern, line)
            if match:
                return match.group(1)

        return None

    def _extract_subclause_number(self, line: str) -> str | None:
        """Extract subclause number from line."""
        subclause_patterns = [
            r"\(\s*([a-zA-Z])\s*\)",
            r"\[\s*([a-zA-Z])\s*\]",
            r"^\s*([ivxIVX]{1,4})\s*$",
        ]

        for pattern in subclause_patterns:
            match = re.search(pattern, line)
            if match:
                return match.group(1)

        return None

    def _calculate_hierarchy_depth(self, line: str) -> int:
        """Calculate hierarchy depth based on line structure."""
        depth = 0

        # Count dots
        depth += line.count(".")

        # Count parentheses
        depth += line.count("(") - line.count(")")

        # Count brackets
        depth += line.count("[") - line.count("]")

        # Count Roman numerals
        if re.search(r"\([i-ivIVX]{1,4}\)", line):
            depth += 2

        return max(depth, 1)

    def clear_cache(self):
        """Clear the boundary detection cache."""
        with self._lock:
            self._cache.clear()
