"""Core hierarchy detection and parsing module

Contains the main engine for detecting and parsing hierarchical legal structures,
including section numbers, clauses, subclauses, and maintaining hierarchy relationships.
"""

import re
import threading
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class LegalNode:
    """Represents a single node in the legal document hierarchy."""

    id: str
    node_type: str  # 'section', 'clause', 'subclause', 'subsubclause', 'explanation', etc.
    content: str
    hierarchy_label: str | None = None
    parent_id: str | None = None
    children: list[str] = None
    depth: int = 0
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

    def __init__(self, max_depth: int = 10):
        self.max_depth = max_depth
        self._cache: dict[str, Any] = {}
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
            cache_key = hash(text)
            if cache_key in self._cache:
                return self._cache[cache_key]

            # Process text line by line
            lines = text.split("\n")
            nodes = []
            indent_stack = []
            section_info = None

            for line_num, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue

                # Detect section
                section = self._detect_section(line, line_num, section_info)
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
                    continue

                # Detect hierarchy level
                hierarchy_info = self._detect_hierarchy_level(line)
                if hierarchy_info:
                    # Create node with hierarchy
                    node_type = hierarchy_info["type"]
                    label = hierarchy_info["label"]
                    depth = hierarchy_info["depth"]

                    # Determine parent
                    parent_id = None
                    if indent_stack and depth <= len(indent_stack):
                        parent_id = indent_stack[depth - 1]

                    # Update indent stack
                    while len(indent_stack) > depth:
                        indent_stack.pop()
                    if depth > len(indent_stack):
                        indent_stack.append("placeholder")

                    # Create node
                    node = self._create_node(
                        id=f"{node_type}_{depth}_{len(nodes)}",
                        node_type=node_type,
                        content=line,
                        hierarchy_label=label,
                        parent_id=parent_id,
                        depth=depth,
                        metadata={"hierarchy_info": hierarchy_info},
                    )
                    nodes.append(node)
                    # Update indent stack with new node
                    indent_stack[-1] = node.id

            result = self._build_hierarchy(nodes)
            self._cache[cache_key] = result
            return result

    def _detect_section(self, line: str, line_num: int, current_section: SectionInfo | None) -> SectionInfo | None:
        """Detect if line is a section marker."""
        section_patterns = self.HIERARCHICAL_PATTERNS["section"]

        for pattern in section_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                section_number = self._extract_section_number(match.group(0))
                return SectionInfo(
                    number=section_number,
                    full_label=match.group(0),
                    type="section",
                    start_line=line_num,
                    end_line=line_num + 10,  # Estimate
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
                hierarchy_type = self._determine_node_type(line, pattern)
                depth = self._calculate_depth(line, pattern)
                label = self._extract_hierarchy_label(match.group(0))

                return {"type": hierarchy_type, "label": label, "depth": depth}

        return None

    def _determine_node_type(self, line: str, pattern: str) -> str:
        """Determine the type of node based on line and pattern."""
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

        # Determine based on pattern type
        if "clause" in pattern.lower() and "(" in pattern:
            if re.search(r"\(\s*[ivxIVX]", pattern):
                return "clause_roman"
            elif re.search(r"\(\s*[a-zA-Z]", pattern):
                return "clause_letter"
            else:
                return "clause_arabic"
        elif "(" in pattern and pattern.count("(") > 1:
            return "subclause_nested"

        return "element"

    def _calculate_depth(self, line: str, pattern: str) -> int:
        """Calculate the nesting depth of the element."""
        depth = 0

        # Count opening parentheses
        open_parens = line.count("(")
        # Subtract closing parentheses
        close_parens = line.count(")")
        depth += open_parens - close_parens

        # Check for nested dots
        dot_count = line.count(".")
        depth += dot_count

        # Special handling for Roman numerals
        if re.search(r"\([i-ivIVX]{1,4}\)", line):
            depth += 2

        return max(depth, 1)

    def _extract_section_number(self, text: str) -> str:
        """Extract section number from text."""
        # Extract digits from text
        digits = re.findall(r"\d+", text)
        if digits:
            return digits[0]
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

    def _create_node(self, id: str, node_type: str, content: str, **kwargs) -> LegalNode:
        """Create a LegalNode with all attributes."""
        return LegalNode(id=id, node_type=node_type, content=content, **kwargs)

    def _build_hierarchy(self, nodes: list[LegalNode]) -> list[LegalNode]:
        """Build and organize hierarchy from nodes."""
        # Sort nodes by depth and order
        nodes.sort(key=lambda x: (x.depth, x.id))

        # Build parent-child relationships
        for node in nodes:
            if node.parent_id:
                parent = next((n for n in nodes if n.id == node.parent_id), None)
                if parent:
                    parent.children.append(node.id)

        return nodes

    def clear_cache(self):
        """Clear the parsing cache."""
        with self._lock:
            self._cache.clear()
