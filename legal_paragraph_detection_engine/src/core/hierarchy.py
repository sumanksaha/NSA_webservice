"""Core hierarchy detection and parsing module

Contains the main engine for detecting and parsing hierarchical legal structures,
including section numbers, clauses, subclauses, and maintaining hierarchy relationships.
"""

import re
import threading
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..utils.cache import evict_if_full, stable_key


@dataclass
class LegalNode:
    """Represents a single node in the legal document hierarchy."""

    id: str
    node_type: str  # 'section', 'clause', 'subclause', 'subsubclause', 'explanation', etc.
    content: str
    hierarchy_label: str | None = None
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    depth: int = 0
    citations: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.children is None:
            self.children = []
        if self.citations is None:
            self.citations = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SectionInfo:
    """Section information extracted from legal text."""

    number: str
    full_label: str
    type: str  # 'section', 'sub_section', etc.
    start_line: int
    end_line: int
    level: int


@dataclass
class ClauseInfo:
    """Clause information extracted from legal text."""

    label: str
    type: str  # 'clause', 'subclause', 'subsubclause', 'roman', 'letter'
    parent_label: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class HierarchyDetector:
    """Detects and parses hierarchical legal structures."""

    # Comprehensive pattern definitions for legal hierarchy
    HIERARCHICAL_PATTERNS: ClassVar[dict[str, list[str]]] = {
        # Section patterns
        "section": [
            r"\b(Section|Sec\.|§)\s*\d+",
            r"\bSection\s*\d+",
            r"\bClauses?\b.*\d+",
            r"\bArticle\b\s*\d+",
            r"\bChapter\b\s*\d+",
            r"\bPart\b\s*\d+",
            r"\bDivision\b\s*\d+",
            r"\bSubsection\s*\(\d+\)",
        ],
        # Clause patterns (Arabic numerals)
        "clause": [
            r"\b\d+\s*\(.*?\)",  # 1(2), 3(4)(a)
            r"\b\d+\.\s*\[.*?\]",  # 2.1 [a]
            r"\b\(\d+\)\s*\(.*?\)",  # (1)(2)(a)
            r"\b\d+\s*\.\s*\(.*\)",  # 3(4)(i)
            r"\b\d+\.\d+\.\d+\.",  # 1.2.3.
            r"\b\d+\.\s*\d+\.\s*\d+\.",  # Nested
        ],
        # Subclause patterns (parentheses)
        "subclause_parentheses": [
            r"\(\s*[a-zA-Z]\s*\)",  # (a), (b), (c)
            r"\(\s*[ivxIVX]+\s*\)",  # (i), (ii), (iii)
            r"\([a-zA-Z]\)",  # Without spaces
            r"\([ivxIVX]\)",  # Without spaces
        ],
        # Subclause patterns (brackets)
        "subclause_brackets": [
            r"\[\s*[a-zA-Z]\s*\]",  # [a], [b]
            r"\[\s*[ivxIVX]+\s*\]",  # [i], [ii]
        ],
        # Explanatory patterns
        "explanation": [
            r"Explanation",
            r"Explanation:",
            r"explanation\.\.\.",
            r"Explanation .* \{",
            r"Explanation of",
            r"Provided that",
            r"Subject to",
            r"[Ee]xcept that",
        ],
        # Proviso patterns
        "proviso": [
            r"Provided",
            r"PROVIDED",
            r"BE IT FURTHER PROVIDED",
            r"PROVIDED THAT",
            r"Provided further",
        ],
        # Schedule patterns
        "schedule": [
            r"Schedule\s*[IVX0-9]+",
            r"Schedule\s*[a-zA-Z]+",
            r"\bSchedule\b",
        ],
        # Table patterns
        "table": [
            r"Table\s*[IVX0-9]+",
            r"Table\s*[a-zA-Z]+",
            r"\bTable\b.*\d+",
            r"TA B L E",
        ],
        # Note patterns
        "note": [
            r"Note\s*:",
            r"Notes\s*:",
            r"Note\s*:",
            r"IMPORTANT\s*N[O]*T[E]*",
            r"NOTE\s*:",
        ],
        # Subsection patterns
        "subsection": [
            r"Subsection\s*\([a-z]\)",
            r"Sub-section\s*\(\d+\)",
            r"\(.*\)\s*of\s*section",
        ],
        # Sub-subsection patterns
        "subsub_section": [
            r"\(.*\)\s*of\s*subsection",
            r"Sub-subsection",
            r"\(.*\)\s*of\s*clauses",
        ],
    }

    # Reference and citation patterns
    REFERENCE_PATTERNS: ClassVar[list[str]] = [
        r"\(\d{4}\s*SC\s*[\d\w/]+\)",
        r"\([Hh]onorable\s*[Sj]ud\w+\s*[Hh]c\s*[\d/]+\)",
        r"\bSC\s*\d+\/\d+",
        r"\bHC\s*\d+\/\d+",
        r"\bC\s*\d+\/\d+",
        r"\b^\s*\(\d{4}\s*[A-Z]\s*[\d\w/]+\)\s*$",
    ]

    # Text boundary patterns
    TEXT_BOUNDARY_PATTERNS: ClassVar[list[str]] = [
        r"^\s*\d+\.\s*$",
        r"^\s*\(\w+\)\s*$",
        r"^\s*\w+\.\.\.\s*$",
        r"^\s*[A-Z][a-z]+\s*:?\s*$",
        r"^\s*[i-ivIVX]{1,4}\s*$",
        r"^\s*[a-z]{1,3}\s*$",
    ]

    # Canonical node-id prefixes (kept stable so ids never embed raw node types)
    ID_PREFIX: ClassVar[dict[str, str]] = {
        "section": "section",
        "clause": "clause",
        "clause_arabic": "clause",
        "clause_letter": "clause",
        "clause_roman": "roman",
        "subclause": "subclause",
        "subclause_nested": "subclause",
        "boundary": "boundary",
    }

    def __init__(self, max_depth: int = 10):
        self.max_depth = max_depth
        self._cache: dict[str, list[LegalNode]] = {}
        self._lock = threading.RLock()

    def detect_hierarchy(self, text: str) -> list[LegalNode]:
        """Detect and parse the complete hierarchy from legal text.

        Args:
            text: Clean legal document text

        Returns:
            List of LegalNode objects in hierarchical order
        """
        with self._lock:
            # Check cache first
            cache_key = stable_key(text)
            if cache_key in self._cache:
                return self._cache[cache_key]

            # Process text line by line
            lines = text.split("\n")
            nodes: list[LegalNode] = []
            ancestor_stack: list[tuple[str, int]] = []  # (node_id, depth)
            section_info = None

            for line_num, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue

                # Detect section
                section = self._detect_section(line, line_num, section_info, text)
                if section:
                    section_info = section
                    node = self._create_node(
                        id=f"section_{section_info.number}_{len(nodes)}",
                        node_type="section",
                        content=line,
                        hierarchy_label=section_info.full_label,
                        depth=0,
                        metadata={"section_info": section_info},
                    )
                    nodes.append(node)
                    # A new section resets the ancestor chain
                    ancestor_stack = [(node.id, 0)]
                    continue

                # Detect hierarchy level
                hierarchy_info = self._detect_hierarchy_level(line)
                if hierarchy_info:
                    # Create node with hierarchy
                    node_type = hierarchy_info["type"]
                    label = hierarchy_info["label"]
                    depth = hierarchy_info["depth"]

                    # Boundary markers are non-hierarchical (depth 0) and must
                    # not sever the ancestor chain for later nodes.
                    is_boundary = node_type == "boundary"
                    if not is_boundary:
                        # Determine parent: nearest ancestor shallower than this node
                        while ancestor_stack and ancestor_stack[-1][1] >= depth:
                            ancestor_stack.pop()
                    parent_id = ancestor_stack[-1][0] if ancestor_stack else None

                    # Create node
                    node = self._create_node(
                        id=self._make_node_id(node_type, depth, len(nodes)),
                        node_type=node_type,
                        content=line,
                        hierarchy_label=label,
                        parent_id=parent_id,
                        depth=depth,
                        metadata={"hierarchy_info": hierarchy_info},
                    )
                    nodes.append(node)
                    if not is_boundary:
                        ancestor_stack.append((node.id, depth))

            result = self._build_hierarchy(nodes)
            evict_if_full(self._cache)
            self._cache[cache_key] = result
            return result

    def _detect_section(
        self, line: str, line_num: int, current_section: SectionInfo | None, text: str | None = None
    ) -> SectionInfo | None:
        """Detect if line is a section marker."""
        section_patterns = self.HIERARCHICAL_PATTERNS["section"]

        for pattern in section_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                section_number = self._extract_section_number(match.group(0))
                # Determine end_line by looking ahead for natural section boundaries
                end_line = self._calculate_section_end_line(line_num, text if text else "")
                return SectionInfo(
                    number=section_number,
                    full_label=match.group(0),
                    type="section",
                    start_line=line_num,
                    end_line=end_line,
                    level=0,
                )

        return None

    def _detect_hierarchy_level(self, line: str) -> dict[str, Any] | None:
        """Detect the hierarchy level of a line."""
        # Check text boundary patterns first (these are likely paragraph boundaries)
        for pattern in self.TEXT_BOUNDARY_PATTERNS:
            if re.match(pattern, line):
                return {"type": "boundary", "label": line.strip("."), "depth": 0}

        # Check for hierarchy patterns
        all_patterns = []
        for node_type, patterns in self.HIERARCHICAL_PATTERNS.items():
            if node_type in ["clause", "subclause_parentheses", "subclause_brackets"]:
                all_patterns.extend(patterns)

        for pattern in all_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                hierarchy_type = self._determine_node_type(line)
                depth = self._calculate_depth(line)
                label = self._extract_hierarchy_label(match.group(0))

                return {"type": hierarchy_type, "label": label, "depth": depth}

        return None

    def _calculate_section_end_line(self, start_line: int, text: str) -> int:
        """Calculate the end line for a section by looking ahead for natural boundaries.

        Args:
            start_line: The line number where the section starts
            text: The full document text being processed

        Returns:
            The line number where the section should end (inclusive)
        """
        lines = text.split("\n")
        if start_line >= len(lines):
            return start_line

        # Look ahead for section boundaries
        i = start_line + 1
        while i < len(lines):
            line = lines[i].strip()

            # Check if this line starts a new section
            if line and self._detect_section(line, i, None, text):
                # This looks like a new section marker
                return i - 1

            # Check for empty line that might indicate section end
            if not line:
                # Empty line could be boundary, but continue looking
                # as some documents have spacing within sections
                pass

            # Additional heuristic: if we encounter a line that appears to be
            # a new heading or major structural element, end the previous section
            if self._is_new_section_indicator(line):
                return i - 1

            i += 1

        # If we reach the end of the document, return the last line
        return len(lines) - 1

    def _is_new_section_indicator(self, line: str) -> bool:
        """Check if a line indicates the start of a new section."""
        if not line:
            return False

        # Check for section-like patterns
        section_patterns = self.HIERARCHICAL_PATTERNS["section"]
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in section_patterns):
            return True

        # Check for hierarchy level patterns
        hierarchy_patterns = []
        for node_type, patterns in self.HIERARCHICAL_PATTERNS.items():
            if node_type in ["clause", "subclause_parentheses", "subclause_brackets"]:
                hierarchy_patterns.extend(patterns)

        return any(re.search(pattern, line, re.IGNORECASE) for pattern in hierarchy_patterns)

    def _determine_node_type(self, line: str) -> str:
        """Determine the type of node based on the matched line content."""
        line_lower = line.lower()

        # Check for specific types based on content
        if any(word in line_lower for word in ["explanation", "illustration"]):
            return "explanation"
        if any(word in line_lower for word in ["proviso", "provided"]):
            return "proviso"
        if any(word in line_lower for word in ["schedule"]):
            return "schedule"
        if any(word in line_lower for word in ["table"]):
            return "table"
        if any(word in line_lower for word in ["note", "important"]):
            return "note"

        # Classify from the line content, not the regex literal
        stripped = line.strip()

        # Roman numeral subclause: (i), (ii)
        if re.match(r"^\(\s*[ivxIVX]+\s*\)", stripped):
            return "clause_roman"
        # Letter subclause: (a), (b), or bracket [a]
        if re.match(r"^\(\s*[a-zA-Z]\s*\)", stripped) or re.match(r"^\[\s*[a-zA-Z]\s*\]", stripped):
            return "clause_letter"
        # Arabic clause with parenthetical: 3(1), 3(1)(a)
        if re.match(r"^\d+\s*\(", stripped):
            return "clause_arabic"
        # Dotted clause: 1.2.3
        if re.match(r"^\d+(?:\.\d+)+", stripped):
            return "clause_arabic"
        # Standalone numbered subclause marker: (1)
        if re.match(r"^\(\s*\d+\s*\)", stripped):
            return "subclause"

        return "element"

    def _calculate_depth(self, line: str) -> int:
        """Calculate the nesting depth of the element.

        Depth counts hierarchy components: a base level of 1, plus one for
        each parenthetical group and one for each dotted level. Examples:
        ``3`` → 1, ``3(1)`` → 2, ``3(1)(a)`` → 3, ``3(1)(a)(i)`` → 4,
        ``3.1.2.3`` → 4.
        """
        paren_groups = len(re.findall(r"\([^()]*\)", line))
        dot_count = line.count(".")
        return max(1 + paren_groups + dot_count, 1)

    def _extract_section_number(self, text: str) -> str:
        """Extract section number from text."""
        # Extract digits from text
        digits = re.findall(r"\d+", text)
        if digits:
            return str(digits[0])
        return "0"

    def _extract_hierarchy_label(self, text: str) -> str:
        """Extract hierarchy label from text."""
        # Clean up the label
        label = re.sub(r"^\s*\d+\.\s*", "", text)
        label = re.sub(r"^\s*\(\w+\)\s*", "", label)
        label = re.sub(r"^\s*\[\w+\]\s*", "", label)
        label = re.sub(r"^\s*[ivxIVX]+\s*", "", label)
        label = label.strip()
        return label if label else text.strip()

    def _make_node_id(self, node_type: str, depth: int, index: int) -> str:
        """Generate a canonical node id using a stable type prefix."""
        prefix = self.ID_PREFIX.get(node_type, "clause")
        return f"{prefix}_{depth}_{index}"

    def _create_node(
        self,
        id: str,
        node_type: str,
        content: str,
        hierarchy_label: str | None = None,
        depth: int = 0,
        parent_id: str | None = None,
        **kwargs,
    ) -> LegalNode:
        """Create a LegalNode with all attributes."""
        return LegalNode(
            id=id,
            node_type=node_type,
            content=content,
            hierarchy_label=hierarchy_label,
            depth=depth,
            parent_id=parent_id,
            **kwargs,
        )

    def _build_hierarchy(self, nodes: list[LegalNode]) -> list[LegalNode]:
        """Build parent-child relationships while preserving document order."""
        if not nodes:
            return nodes

        node_by_id = {node.id: node for node in nodes}

        # Reset children so repeated builds are idempotent
        for node in nodes:
            node.children = []

        # Link children to parents (parent_id already resolved during detection)
        for node in nodes:
            if node.parent_id and node.parent_id in node_by_id:
                node_by_id[node.parent_id].children.append(node.id)

        return nodes

    def clear_cache(self):
        """Clear the parsing cache."""
        with self._lock:
            self._cache.clear()
