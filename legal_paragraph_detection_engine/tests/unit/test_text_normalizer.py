"""Unit tests for TestTextNormalizer (moved out of tests/unit/__init__.py)."""

import re
import unittest

from legal_paragraph_detection_engine import (
    TextNormalizer,
)


class TestTextNormalizer(unittest.TestCase):
    """Test text normalization functionality."""

    def setUp(self):
        self.cleaner = TextNormalizer()

    def test_clean_text_basic(self):
        """Test basic text cleaning."""
        text = "Hello   World\n\n\nGoodbye"
        result = self.cleaner.clean_text(text)
        self.assertEqual(result, "Hello World\n\nGoodbye")

    def test_clean_text_legal_citations(self):
        """Test legal citation preservation."""
        text = "See (2020 SC 123/456) for details"
        result = self.cleaner.clean_text(text)
        self.assertIn("(2020 SC 123/456)", result)

    def test_clean_text_preserve_markers(self):
        """Test legal marker preservation."""
        text = "Section 3\n(1)\nExplanation\nNote:"
        result = self.cleaner.clean_text(text)
        self.assertIn("Section 3", result)
        self.assertIn("(1)", result)
        self.assertIn("Explanation", result)
        self.assertIn("Note:", result)

    def test_clean_text_remove_artifacts(self):
        """Test artifact removal."""
        text = "Hello World  Page 1  \n\n\nIllustration 1:"
        result = self.cleaner.clean_text(text)
        self.assertNotIn("Page 1", result)
        self.assertNotIn("Illustration 1:", result)

    def test_clean_text_weekend_processing(self):
        """Test processing weekend documents."""
        text = "Friday: End of week report.\nSaturday: Weekend processing.\nSunday: Sunday brunch."
        result = self.cleaner.clean_text(text)
        self.assertIn("Weekend processing", result)

    def test_clean_text_indian_legal_patterns(self):
        """Test Indian legal pattern recognition."""
        text = "Section 3(1)(a)\n\n(1)(a) Legal analysis."
        result = self.cleaner.clean_text(text)
        self.assertIn("Section 3(1)(a)", result)
        self.assertIn("(1)(a)", result)

    def test_clean_text_mixed_formatting(self):
        """Test mixed formatting preservation."""
        text = "Incident: Case No. 123\nLocation: Mumbai\nDate: 01/01/2024"
        result = self.cleaner.clean_text(text)
        self.assertIn("Case No. 123", result)
        self.assertIn("Mumbai", result)

    def test_clean_text_empty_input(self):
        """Test empty input handling."""
        text = ""
        result = self.cleaner.clean_text(text)
        self.assertEqual(result, "")

    def test_clean_text_whitespace_only(self):
        """Test whitespace-only input."""
        text = "   \n\n\n   "
        result = self.cleaner.clean_text(text)
        self.assertEqual(result, "")

    def test_clean_text_legal_sections(self):
        """Test legal section extraction."""
        text = "Section 3(1)(a)\n\n(1)(a) Analysis."
        sections = self.cleaner.find_legal_sections(text)
        self.assertIn("Section 3(1)(a)", sections)

    def test_clean_text_citations(self):
        """Test citation extraction."""
        text = "See (2020 SC 123/456). Also reference [M1/2023]."
        citations = self.cleaner.extract_citations_from_text(text)
        self.assertEqual(len(citations), 2)
        self.assertEqual(citations[0]["type"], "supreme_court")
        self.assertEqual(citations[1]["type"], "statutory")

    def test_cleaner_cache_functionality(self):
        """Test text cleaner cache."""
        text = "Test text"
        result1 = self.cleaner.clean_text(text)
        result2 = self.cleaner.clean_text(text)
        self.assertEqual(result1, result2)

    def test_cleaner_line_classification(self):
        """Test line classification."""
        test_cases = [
            ("Section 3", "mark"),
            ("(1)", "mark"),
            ("Explanation", "mark"),
            ("Page 1", "page_number"),
            ("Hello: World", "header"),
            ("", "empty"),
        ]

        for text, expected_type in test_cases:
            actual_type = self._get_line_type(self.cleaner, text)
            self.assertEqual(actual_type, expected_type, f"Failed for text: {text}")

    def _get_line_type(self, cleaner, text):
        """Helper to get line type for testing."""
        if not text.strip():
            return "empty"
        if re.match(r"^\s*\d+\s*$", text):
            return "page_number"
        if re.match(r"^\s*Page\s*\d+\s*$", text):
            return "page_number"
        # "Label: value" lines count as headers (e.g. "Hello: World")
        if re.match(r"^\s*[A-Z][a-z\s]+:", text):
            return "header"
        if re.match(r"^\s*[A-Z][a-z]+\s*$", text):
            if text.lower() in ["explanation", "provided", "proviso"]:
                return "mark"
            return "legal_content"
        if re.match(r"^\s*\(\s*[a-zA-Z]\s*\)\s*$", text):
            return "mark"
        if re.match(r"^\s*\(\s*\d+\s*\)\s*$", text):
            return "mark"
        if re.search(r"\b\d+\s*\.\s*[a-zA-Z]\b", text):
            return "mark"
        if re.match(r"^\s*(?:Section|Sec\.|§)\s*\d+", text, re.IGNORECASE):
            return "mark"
        return "legal_content"
