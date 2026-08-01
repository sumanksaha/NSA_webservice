"""Unit tests for TestParagraphBoundaryDetector (moved out of tests/unit/__init__.py)."""

import threading
import unittest

from legal_paragraph_detection_engine import (
    ParagraphBoundaryDetector,
)


class TestParagraphBoundaryDetector(unittest.TestCase):
    """Test paragraph boundary detection functionality."""

    def setUp(self):
        self.detector = ParagraphBoundaryDetector()

    def test_detect_hierarchy_levels(self):
        """Test hierarchy level detection."""
        test_cases = [
            ("3", 1),
            ("3.1", 2),
            ("3(1)", 2),
            ("3(1)(a)", 3),
            ("3(1)(a)(i)", 4),
            ("3.1.2.3", 4),
        ]

        for text, expected in test_cases:
            result = self.detector._detect_hierarchy_level(text)
            if result:
                self.assertEqual(result["depth"], expected, f"Failed for {text}, got {result['depth']}")

    def test_classify_paragraph_types(self):
        """Test paragraph type classification."""
        test_cases = [
            ("Section 3", "section"),
            ("3.1", "clause"),
            ("(a)", "subclause"),
            ("Explanation", "explanation"),
            ("Proviso", "proviso"),
            ("Note:", "note"),
            ("Schedule I", "schedule"),
            ("Table 1", "table"),
        ]

        for text, expected in test_cases:
            result = self.detector._classify_paragraph_type(text)
            self.assertEqual(result.value, expected, f"Failed for {text}, got {result.value}")

    def test_hierarchy_break_detection(self):
        """Test hierarchy break detection."""
        test_cases = [
            ("(a)", True),
            ("[a]", True),
            ("i", True),
            ("1.2.3", True),
            ("Explanation", True),
            ("Note:", True),
            ("Hello World", False),
            ("This is a test paragraph.", False),
        ]

        for text, expected in test_cases:
            result = self.detector._detect_hierarchy_break(text)
            self.assertEqual(result, expected, f"Failed for {text}, got {result}")

    def test_structure_end_detection(self):
        """Test structure end detection."""
        test_cases = [
            ("3.1.", True),
            ("(a).", True),
            ("3.1.2.3.", True),
            ("Explanation.", True),
            ("Hello World", False),
            ("This is test text", False),
        ]

        for text, expected in test_cases:
            result = self.detector._detect_structure_end(text)
            self.assertEqual(result, expected, f"Failed for {text}, got {result}")

    def test_structure_start_detection(self):
        """Test structure start detection."""
        test_cases = [
            ("Section 3", True),
            ("Clause 1.2", True),
            ("Article 5", True),
            ("Chapter 1", True),
            ("Provided that", True),
            ("Explanation", True),
            ("Hello World", False),
        ]

        for text, expected in test_cases:
            result = self.detector._detect_structure_start(text)
            self.assertEqual(result, expected, f"Failed for {text}, got {result}")

    def test_section_number_extraction(self):
        """Test section number extraction."""
        test_cases = [
            ("Section 3", "3"),
            ("Sec 5", "5"),
            ("§ 10", "10"),
            ("3", "3"),
            ("(1)", None),
            ("Hello", None),
        ]

        for text, expected in test_cases:
            result = self.detector._extract_section_number(text)
            self.assertEqual(result, expected, f"Failed for {text}, got {result}")

    def test_clause_number_extraction(self):
        """Test clause number extraction."""
        test_cases = [
            ("1.a", "1"),
            ("(1)(a)", "1"),
            ("1.2", "1"),
            ("Hello", None),
        ]

        for text, expected in test_cases:
            result = self.detector._extract_clause_number(text)
            self.assertEqual(result, expected, f"Failed for {text}, got {result}")

    def test_subclause_number_extraction(self):
        """Test subclause number extraction."""
        test_cases = [
            ("(a)", "a"),
            ("[b]", "b"),
            ("i", "i"),
            ("1.a", None),
            ("Hello", None),
        ]

        for text, expected in test_cases:
            result = self.detector._extract_subclause_number(text)
            self.assertEqual(result, expected, f"Failed for {text}, got {result}")

    def test_hierarchy_depth_calculation(self):
        """Test hierarchy depth calculation."""
        test_cases = [
            ("3", 1),
            ("3.1", 2),
            ("3(1)", 2),
            ("3(1)(a)", 3),
            ("3(1)(a)(i)", 4),
            ("3.1.2.3", 4),
            ("(a)", 2),
            ("[b]", 2),
            ("(i)", 2),
        ]

        for text, expected in test_cases:
            result = self.detector._calculate_hierarchy_depth(text)
            self.assertEqual(result, expected, f"Failed for {text}, got {result}")

    def test_paragraph_boundary_detection(self):
        """Test full paragraph boundary detection."""
        text = """
        Section 3(1)

        3(1)(a) Some text here.
        (a) Subclause text.
        Explanation.

        Note: Important note here.

        Provided that this is a proviso.
        """

        paragraphs = self.detector.detect_paragraph_boundaries(text)
        self.assertGreater(len(paragraphs), 0)

        # Check that we have different paragraph types
        types = [p.paragraph_type for p in paragraphs]
        self.assertIn("section", [t.value for t in types])
        self.assertIn("clause", [t.value for t in types])
        self.assertIn("subclause", [t.value for t in types])
        self.assertIn("explanation", [t.value for t in types])
        self.assertIn("note", [t.value for t in types])
        self.assertIn("proviso", [t.value for t in types])

    def test_detector_cache(self):
        """Test paragraph detector cache."""
        text = "Section 3\n\n3(1)\n\n3(1)(a)"
        result1 = self.detector.detect_paragraph_boundaries(text)
        result2 = self.detector.detect_paragraph_boundaries(text)
        self.assertEqual(len(result1), len(result2))

    def test_detector_locking(self):
        """Test thread safety."""

        text = "Section 3\n\n3(1)"

        results = []
        errors = []

        def worker():
            try:
                result = self.detector.detect_paragraph_boundaries(text)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 5)
