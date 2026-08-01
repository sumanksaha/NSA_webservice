"""Clause and subclause parsing module

Detects and parses hierarchical clause structures in legal documents,
including nested patterns, explanations, provisos, and their relationships.
"""

import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar


class ClauseType(Enum):
    """Types of clauses in legal documents."""

    MAIN_CLAUSE = "main_clause"
    SUBCLAUSE = "subclause"
    SUBSUBCLAUSE = "subsubclause"
    EXPLANATION = "explanation"
    PROVISO = "proviso"
    EXCEPTION = "exception"
    NOTE = "note"
    SCHEDULE = "schedule"
    TABLE = "table"
    REFERENCE = "reference"


@dataclass
class ClauseData:
    """Extracted clause data."""

    id: str
    clause_type: ClauseType
    text: str
    hierarchy_label: str
    start_line: int
    end_line: int
    section: str | None = None
    parent_id: str | None = None
    children: list["ClauseData"] = None
    citations: list[dict[str, str]] = None
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.children is None:
            self.children = []
        if self.citations is None:
            self.citations = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ClausePattern:
    """Represents a pattern for detecting clauses."""

    pattern_type: str
    regex: str
    priority: int
    description: str


class ClauseParser:
    """Parses clauses, subclauses, and explanatory text from legal documents."""

    # Comprehensive clause patterns
    CLAUSE_PATTERNS: ClassVar[list[ClausePattern]] = [
        # Main clauses (Arabic numerals)
        ClausePattern(
            pattern_type="main_arabic",
            regex=r"^\s*(\d+)\s*[\.\)]\s*([A-Z][a-z\s]*?)\s*$",
            priority=10,
            description="Main clauses with Arabic numerals (1., 2.)",
        ),
        ClausePattern(
            pattern_type="main_parentheses",
            regex=r"^\s*(\d+)\s*\(\s*[a-zA-Z]\s*\)\s*([A-Z][a-z\s]*?)\s*$",
            priority=10,
            description="Main clauses with subclause parenthetical (1(a))",
        ),
        # Subclauses (letters)
        ClausePattern(
            pattern_type="subclause_letter",
            regex=r"^\s*\(\s*([a-zA-Z])\s*\)\s*([A-Z][a-z\s]*?)\s*$",
            priority=20,
            description="Subclauses with letters ((a), (b))",
        ),
        ClausePattern(
            pattern_type="subclause_bracket",
            regex=r"^\s*\[\s*([a-zA-Z])\s*\]\s*([A-Z][a-z\s]*?)\s*$",
            priority=20,
            description="Subclauses with brackets ([a], [b])",
        ),
        # Roman numeral subclauses
        ClausePattern(
            pattern_type="subclause_roman",
            regex=r"^\s*\(([i-ivIVX]{1,4})\)\s*([A-Z][a-z\s]*?)\s*$",
            priority=20,
            description="Subclauses with Roman numerals ((i), (ii))",
        ),
        # Nested patterns
        ClausePattern(
            pattern_type="nested_complex",
            regex=r"^\s*\d+\s*\(\s*\d+\s*\)\s*\(\s*[a-zA-Z]\s*\)\s*\([i-ivIVX]{1,4}\)?\s*([A-Z][a-z\s]*?)\s*$",
            priority=30,
            description="Complex nested patterns (1(2)(a)(i))",
        ),
        # Special legal patterns
        ClausePattern(
            pattern_type="explanation",
            regex=r"^\s*(?:Explanation|Illustration|Example)\s*(?:\d*)?\s*:?\s*([A-Z][a-z\s]*?)\s*$",
            priority=40,
            description="Explanation sections",
        ),
        ClausePattern(
            pattern_type="proviso",
            regex=r"^\s*(?:PROVISO|Provided|Provided further)\s*:?\s*([A-Z][a-z\s]*?)\s*$",
            priority=40,
            description="Proviso sections",
        ),
        ClausePattern(
            pattern_type="exception",
            regex=r"^\s*Except\s*(?:that)?\s*:?\s*([A-Z][a-z\s]*?)\s*$",
            priority=40,
            description="Exception clauses",
        ),
        # Reference patterns
        ClausePattern(
            pattern_type="reference",
            regex=r"^\s*See\s+([A-Z][a-z\s,]+)\s*$",
            priority=50,
            description="Cross-references",
        ),
        ClausePattern(
            pattern_type="schedule_table",
            regex=r"^\s*(Schedule|Table)\s*([A-Z0-9IVX]*)?\s*:?\s*([A-Z][a-z\s]*?)\s*$",
            priority=60,
            description="Schedules and tables",
        ),
    ]

    # Special legal text patterns
    SPECIAL_TEXT_PATTERNS: ClassVar[dict[str, str]] = {
        "provides_that": r"\bPROVIDES\s+THAT\b",
        "subject_to": r"\bSubject\s+to\b",
        "notwithstanding": r"\bNotwithstanding\b",
        "unless": r"\bUnless\b",
        "except": r"\bExcept\b",
        "further_provided": r"\bFurther\s+provided\b",
        "be_it_further": r"\bBE\s+IT\s+FURTHER\b",
        "save_save": r"\bSave\s+and\s+save\b",
        "without_prejudice": r"\bWithout\s+prejudice\b",
    }

    # Context keywords for clause identification
    CONTEXT_KEYWORDS: ClassVar[dict[str, list[str]]] = {
        "clause_starters": [
            "unless",
            "provided",
            "subject to",
            "except that",
            "if",
            "whereas",
            "be it further",
            "save and save",
            "notwithstanding",
            "provided further",
        ],
        "explanation_markers": [
            "explanation",
            "illustration",
            "example",
            "therefore",
            "consequently",
            "thus",
            "hence",
            "because",
            "since",
            "given that",
            "considering",
        ],
        "reference_markers": [
            "see also",
            "cross-reference",
            "referring to",
            "as defined in",
            "pursuant to",
            "under",
            "section",
            "clause",
            "article",
        ],
    }

    def __init__(self):
        self._cache: dict[str, list[ClauseData]] = {}
        self._lock = threading.RLock()
        self._clause_counter = 0

    def parse_clauses(self, text: str) -> list[ClauseData]:
        """Parse clauses from legal document text.

        Args:
            text: Clean legal document text

        Returns:
            List of ClauseData objects in hierarchical order
        """
        with self._lock:
            # Check cache
            cache_key = hash(text)
            if cache_key in self._cache:
                return self._cache[cache_key]

            lines = text.split("\n")
            clauses = []

            for line_num, line in enumerate(lines):
                line_stripped = line.strip()
                if not line_stripped:
                    continue

                # Try each pattern in order of priority
                clause = self._match_clause_pattern(line_stripped, line_num)
                if clause:
                    clauses.append(clause)

            # Build hierarchy
            clauses = self._build_clause_hierarchy(clauses)

            self._cache[cache_key] = clauses
            return clauses

    def _match_clause_pattern(self, line: str, line_num: int) -> ClauseData | None:
        """Match line against clause patterns."""
        # Sort patterns by priority (higher first)
        sorted_patterns = sorted(self.CLAUSE_PATTERNS, key=lambda x: x.priority, reverse=True)

        for pattern in sorted_patterns:
            match = re.match(pattern.regex, line, re.IGNORECASE)
            if match:
                clause_type = self._determine_clause_type(line, pattern.pattern_type)
                hierarchy_label = self._extract_hierarchy_label(line, pattern.pattern_type, match)
                section = self._extract_section_reference(line)

                return ClauseData(
                    id=f"clause_{self._clause_counter}",
                    clause_type=clause_type,
                    text=line,
                    hierarchy_label=hierarchy_label,
                    start_line=line_num,
                    end_line=line_num,
                    section=section,
                    metadata={
                        "pattern_type": pattern.pattern_type,
                        "description": pattern.description,
                        "match_groups": match.groups(),
                        "pattern_priority": pattern.priority,
                    },
                )

        # Check for special text patterns
        if self._detect_special_text(line):
            clause_type = self._determine_clause_type(line, "special_text")
            return self._create_clause_from_special_text(line, line_num, clause_type)

        # Check for contextual clauses
        if self._is_contextual_clause(line):
            clause_type = self._determine_clause_type(line, "contextual")
            return self._create_clause_from_context(line, line_num, clause_type)

        return None

    def _determine_clause_type(self, line: str, pattern_type: str) -> ClauseType:
        """Determine clause type from pattern and context."""
        # Check pattern type first
        if "explanation" in pattern_type:
            return ClauseType.EXPLANATION
        elif "proviso" in pattern_type:
            return ClauseType.PROVISO
        elif "exception" in pattern_type:
            return ClauseType.EXCEPTION
        elif "reference" in pattern_type:
            return ClauseType.REFERENCE
        elif "schedule" in pattern_type or "table" in pattern_type:
            return ClauseType.SCHEDULE if "schedule" in pattern_type else ClauseType.TABLE

        # Check line content
        line_lower = line.lower()

        if any(word in line_lower for word in ["explanation", "illustration", "example"]):
            return ClauseType.EXPLANATION
        elif any(word in line_lower for word in ["provided", "proviso", "unless"]):
            return ClauseType.PROVISO
        elif any(word in line_lower for word in ["except", "unless", "save and save"]):
            return ClauseType.EXCEPTION
        elif any(word in line_lower for word in ["schedule", "table"]):
            return ClauseType.SCHEDULE if "schedule" in line_lower else ClauseType.TABLE
        elif "note" in line_lower or "important" in line_lower:
            return ClauseType.NOTE

        # Check pattern
        if re.search(r"\(\s*[a-zA-Z]\s*\)", line):
            return ClauseType.SUBCLAUSE
        elif re.search(r"\(\s*[i-ivIVX]{1,4}\s*\)", line):
            return ClauseType.SUBSUBCLAUSE
        elif re.search(r"^\s*\d+\s*\(\s*\d+\s*\)", line):
            return ClauseType.MAIN_CLAUSE

        return ClauseType.MAIN_CLAUSE

    def _extract_hierarchy_label(self, line: str, pattern_type: str, match: re.Match) -> str:
        """Extract hierarchy label from matched line."""
        if pattern_type == "main_arabic":
            return f"{match.group(1)}."
        elif pattern_type == "main_parentheses":
            return f"{match.group(1)}({match.group(2)})"
        elif pattern_type == "subclause_letter":
            return f"({match.group(1)})"
        elif pattern_type == "subclause_bracket":
            return f"[{match.group(1)}]"
        elif pattern_type == "subclause_roman":
            return f"({match.group(1)})"
        elif pattern_type == "nested_complex":
            return self._extract_nested_label(line)
        elif pattern_type in ["explanation", "proviso", "exception"]:
            return line.strip()

        return line.strip()

    def _extract_nested_label(self, line: str) -> str:
        """Extract nested label from complex patterns."""
        nested_pattern = r"(\d+\s*\(\s*\d+\s*\)\s*\(\s*[a-zA-Z]\s*\)\s*\([i-ivIVX]{1,4}\))?"
        match = re.search(nested_pattern, line)
        if match:
            return match.group(1).strip()
        return line.strip()

    def _extract_section_reference(self, line: str) -> str | None:
        """Extract section reference from clause line."""
        section_pattern = r"Section\s*(\d+)"
        match = re.search(section_pattern, line, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _detect_special_text(self, line: str) -> bool:
        """Detect if line contains special legal text."""
        line_lower = line.lower()
        return any(re.search(pattern, line_lower) for pattern in self.SPECIAL_TEXT_PATTERNS.values())

    def _create_clause_from_special_text(self, line: str, line_num: int, clause_type: ClauseType) -> ClauseData:
        """Create clause from special text pattern."""
        return ClauseData(
            id=f"clause_{self._clause_counter}",
            clause_type=clause_type,
            text=line,
            hierarchy_label=line,
            start_line=line_num,
            end_line=line_num,
            metadata={"type": "special_text"},
        )

    def _is_contextual_clause(self, line: str) -> bool:
        """Determine if line is a contextual clause based on keywords."""
        line_lower = line.lower()
        for keyword in self.CONTEXT_KEYWORDS["clause_starters"]:
            if re.search(r"\b" + re.escape(keyword) + r"\b", line_lower):
                return True
        return False

    def _create_clause_from_context(self, line: str, line_num: int, clause_type: ClauseType) -> ClauseData:
        """Create clause from contextual pattern."""
        self._clause_counter += 1
        return ClauseData(
            id=f"clause_ctx_{self._clause_counter}",
            clause_type=clause_type,
            text=line,
            hierarchy_label=line,
            start_line=line_num,
            end_line=line_num,
            metadata={"type": "contextual", "clause_type": clause_type.value},
        )

    def _build_clause_hierarchy(self, clauses: list[ClauseData]) -> list[ClauseData]:
        """Build hierarchy relationships between clauses."""
        if not clauses:
            return clauses

        # Sort by line number
        clauses.sort(key=lambda x: x.start_line)

        # Group by type and level for hierarchy building
        level_map = {}
        root_clauses = []

        for clause in clauses:
            # Calculate level based on pattern
            level = self._calculate_clause_level(clause.text, clause.clause_type)
            clause.metadata["level"] = level

            # Find parent clause at previous level
            parent = None
            if level > 1:
                # Look for clauses with same hierarchy pattern
                candidates = [
                    c
                    for c in clauses
                    if c.clause_type == clause.clause_type and self._is_parent_of(c.text, clause.text)
                ]
                if candidates:
                    parent = candidates[-1]  # Use closest parent

            clause.parent_id = parent.id if parent else None
            level_map[clause.id] = clause

            if not parent:
                root_clauses.append(clause)

        # Add children to parent
        for clause in clauses:
            if clause.parent_id and clause.parent_id in level_map:
                level_map[clause.parent_id].children.append(clause)

        # Increment counter for total clauses
        self._clause_counter = len(clauses)
        return root_clauses

    def _calculate_clause_level(self, text: str, clause_type: ClauseType) -> int:
        """Calculate hierarchy level of a clause."""
        level = 1

        # Count hierarchy indicators
        level += text.count("(") - text.count(")")  # Parentheses
        level += text.count("[") - text.count("]")  # Brackets
        level += text.count(".") - text.count("..")  # Dots

        # Special handling
        if clause_type == ClauseType.SUBCLAUSE:
            level += 1
        elif clause_type == ClauseType.SUBSUBCLAUSE:
            level += 2

        return max(level, 1)

    def _is_parent_of(self, parent_text: str, child_text: str) -> bool:
        """Determine if one clause is parent of another."""
        # Simple heuristic: parent should be prefix of child when normalized
        parent_normalized = re.sub(r"\s+", " ", parent_text.strip())
        child_normalized = re.sub(r"\s+", " ", child_text.strip())

        # Check if child's patterns appear after parent's patterns
        parent_numbers = re.findall(r"\d+", parent_normalized)
        child_numbers = re.findall(r"\d+", child_normalized)

        if parent_numbers and child_numbers:
            # First number should match (parentheses patterns need special handling)
            return parent_numbers[0] == child_numbers[0]

        return False

    def clear_cache(self):
        """Clear the clause parsing cache."""
        with self._lock:
            self._cache.clear()
            self._clause_counter = 0
