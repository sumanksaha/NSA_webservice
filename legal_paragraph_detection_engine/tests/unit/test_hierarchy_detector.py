"""Unit tests for TestHierarchyDetector (moved out of tests/unit/__init__.py)."""

import threading
import unittest

from legal_paragraph_detection_engine import (
    HierarchyDetector,
)


class TestHierarchyDetector(unittest.TestCase):
    """Test hierarchy detection functionality."""

    def setUp(self):
        self.detector = HierarchyDetector(max_depth=5)

    def test_detect_sections(self):
        """Test section detection."""
        text = "Section 3\n\n3(1)\n\n3(1)(a)\n\n3(1)(a)(i)"
        sections = self.detector.detect_hierarchy(text)

        self.assertGreater(len(sections), 0)

        # Find section nodes
        section_nodes = [n for n in sections if n.node_type == "section"]
        self.assertGreater(len(section_nodes), 0)

    def test_detect_clauses(self):
        """Test clause detection."""
        text = """
        Section 3(1)
        3(1)(a) First clause.
        3(1)(b) Second clause.
        """

        clauses = self.detector.detect_hierarchy(text)
        clause_nodes = [n for n in clauses if n.node_type in ["clause_arabic", "clause_letter", "clause_roman"]]

        self.assertGreater(len(clause_nodes), 0)

    def test_build_hierarchy(self):
        """Test hierarchy building."""
        text = """
        Section 3(1)
        3(1)(a) First clause.
        3(1)(b) Second clause.
        (a) Subclause.
        """

        nodes = self.detector.detect_hierarchy(text)
        self.detector._build_hierarchy(nodes)

        # Find root node
        root = next((n for n in nodes if n.parent_id is None), None)
        self.assertIsNotNone(root)
        self.assertEqual(root.node_type, "section")

        # Check children
        children = [n for n in nodes if n.parent_id == root.id]
        self.assertGreater(len(children), 0)

    def test_node_id_generation(self):
        """Test node ID generation."""
        text = "Section 3\n\n3(1)"
        nodes = self.detector.detect_hierarchy(text)

        for node in nodes:
            # Node IDs should be in the format: {node_type}_{depth}_{index}
            # e.g., "section_0_0", "clause_2_1", "subclause_3_2", etc.
            self.assertRegex(node.id, r"^(section_|clause_|subclause_|roman_|boundary_)\d+_\d+$")
            self.assertIn(
                node.node_type,
                ["section", "clause", "subclause", "boundary", "clause_arabic", "clause_letter", "clause_roman"],
            )

    def test_depth_calculation(self):
        """Test depth calculation.

        T-44 audit: the previous version passed the expected value directly
        into ``_create_node`` and asserted it was stored — a tautology that
        never exercised the depth logic, and it encoded 0-based expectations
        that contradict the implementation. The real contract (source
        docstring, §1.1d, RC-7/T-21) is 1-based: ``3`` → 1, ``3(1)`` → 2,
        ``3(1)(a)`` → 3, ``3(1)(a)(i)`` → 4, ``3.1.2.3`` → 4.
        """
        test_cases = [
            ("3", 1),
            ("3(1)", 2),
            ("3(1)(a)", 3),
            ("3(1)(a)(i)", 4),
            ("3.1.2.3", 4),
        ]

        for text, expected in test_cases:
            self.assertEqual(
                self.detector._calculate_depth(text),
                expected,
                f"Failed for {text}",
            )

    def test_cache_functionality(self):
        """Test hierarchy detector cache."""
        text = "Section 3\n\n3(1)"
        result1 = self.detector.detect_hierarchy(text)
        result2 = self.detector.detect_hierarchy(text)
        self.assertEqual(len(result1), len(result2))

    def test_clean_cache(self):
        """Test cache clearing."""
        text = "Section 3"
        self.detector.detect_hierarchy(text)
        self.detector.clear_cache()
        result = self.detector.detect_hierarchy(text)
        self.assertGreater(len(result), 0)

    def test_thred_safety(self):
        """Test thread safety."""

        text = "Section 3\n\n3(1)\n\n3(1)(a)"

        results = []
        errors = []

        def worker():
            try:
                result = self.detector.detect_hierarchy(text)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(3)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 3)
