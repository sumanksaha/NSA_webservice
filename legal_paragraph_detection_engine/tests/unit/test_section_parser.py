"""Unit tests for TestSectionParser (moved out of tests/unit/__init__.py)."""

import threading
import unittest

from legal_paragraph_detection_engine import (
    SectionParser,
)
from legal_paragraph_detection_engine.src.parsers.section_parser import SectionType


class TestSectionParser(unittest.TestCase):
    """Test section parsing functionality."""

    def setUp(self):
        self.parser = SectionParser()

    def test_parse_sections_basic(self):
        """Test basic section parsing."""
        text = """
        Section 3(1)

        3(1)(a) First clause.
        3(1)(b) Second clause.
        """

        sections = self.parser.parse_sections(text)

        self.assertGreater(len(sections), 0)

        # Find main sections
        main_sections = [s for s in sections if s.section_type.name == "MAIN_SECTION"]
        self.assertGreater(len(main_sections), 0)

    def test_parse_subsections(self):
        """Test subsection parsing."""
        text = """
        Section 3(1)
        (1) First subsection.
        (2) Second subsection.
        """

        sections = self.parser.parse_sections(text)

        # Find subsections
        subsections = [s for s in sections if s.section_type.name == "SUBSECTION"]
        self.assertGreater(len(subsections), 0)

    def test_parse_subsubsections(self):
        """Test sub-subsection parsing."""
        text = """
        Section 3(1)
        (a) First sub-subsection.
        (b) Second sub-subsection.
        """

        sections = self.parser.parse_sections(text)

        # Find sub-subsections
        subsubsections = [s for s in sections if s.section_type.name == "SUBSUBSECTION"]
        self.assertGreater(len(subsubsections), 0)

    def test_parse_paragraphs(self):
        """Test paragraph parsing."""
        text = """
        Paragraph 1
        First paragraph.
        """

        sections = self.parser.parse_sections(text)

        # Find paragraphs
        paragraphs = [s for s in sections if s.section_type.name == "PARAGRAPH"]
        self.assertGreater(len(paragraphs), 0)

    def test_parse_subparagraphs(self):
        """Test subparagraph parsing."""
        text = """
        1.1 First subparagraph.
        1.2 Second subparagraph.
        """

        sections = self.parser.parse_sections(text)

        # Find subparagraphs
        subparagraphs = [s for s in sections if s.section_type.name == "SUBPARAGRAPH"]
        self.assertGreater(len(subparagraphs), 0)

    def test_parse_roman_sections(self):
        """Test Roman numeral section parsing."""
        text = """
        i First section.
        ii Second section.
        iii Third section.
        """

        sections = self.parser.parse_sections(text)

        # Find Roman sections
        roman_sections = [s for s in sections if s.section_type.name == "ROMAN_SECTION"]
        self.assertGreater(len(roman_sections), 0)

    def test_build_hierarchy(self):
        """Test hierarchy building."""
        text = """
        Section 3(1)
        3(1)(a) First clause.
        3(1)(b) Second clause.
        """

        sections = self.parser.parse_sections(text)
        hierarchy = self.parser._build_hierarchy(sections)

        # Find root sections (Sections without parents)
        roots = [s for s in hierarchy if s.parent_id is None]
        self.assertGreater(len(roots), 0)

    def test_section_number_extraction(self):
        """Test section number extraction."""
        text_cases = [
            ("Section 3", "3"),
            ("Sec 5", "5"),
            ("§ 10", "10"),
            ("3", "3"),
            ("(1)", None),
        ]

        for text, expected in text_cases:
            section = self.parser._extract_section_info(text, 1)
            if expected:
                self.assertEqual(section.section_number, expected)
            else:
                self.assertIsNone(section.section_number)

    def test_section_title_extraction(self):
        """Test section title extraction."""
        text = "Section 3: Legal provisions and requirements."
        section = self.parser._extract_section_info(text, 1)

        self.assertIsNotNone(section.title)
        self.assertIn("provisions", section.title)

    def test_level_calculation(self):
        """Test level calculation."""
        text_cases = [
            ("Section 3", 1),
            ("(1)", 1),
            ("(a)", 1),
            ("1.2.3", 3),
            ("1(2)(a)", 3),
        ]

        for text, expected in text_cases:
            section = self.parser._extract_section_info(text, 1)
            self.assertEqual(section.level, expected, f"Failed for {text}, got {section.level}")

    def test_marker_chain_recognised_not_dropped(self):
        """Subsection-marker chains are parsed, not silently dropped.

        RAG_AGENT_A_SCOPE §2.3: ``(1)(a)`` previously returned ``None``. It is
        a SUBSUBSECTION marker with no section number and no title of its own.
        """
        section = self.parser._extract_section_info("(1)(a)", 1)
        self.assertIsNotNone(section)
        self.assertEqual(section.section_type, SectionType.SUBSUBSECTION)
        self.assertIsNone(section.section_number)
        self.assertIsNone(section.title)

    def test_marker_chain_with_content(self):
        """A marker chain followed by prose yields the prose as the title.

        RAG_AGENT_A_SCOPE §2.3: the ``(1)(a)`` prefix must not leak into the
        title; ``"First clause."`` is the title and no section number is
        inherited from the markers.
        """
        section = self.parser._extract_section_info("(1)(a) First clause.", 1)
        self.assertIsNotNone(section)
        self.assertEqual(section.section_type, SectionType.SUBSUBSECTION)
        self.assertIsNone(section.section_number)
        self.assertEqual(section.title, "First clause.")

    def test_subsection_markers_never_section_title(self):
        """Markers on a ``Section N`` line are not the section title.

        RAG_AGENT_A_SCOPE §2.3: ``Section 3(1)(a)`` previously reported
        ``"(1)(a)"`` as its title. Marker-only sections have no title; a real
        title that follows the markers is preserved.
        """
        bare = self.parser._extract_section_info("Section 3(1)(a)", 1)
        self.assertEqual(bare.section_number, "3")
        self.assertIsNone(bare.title)

        titled = self.parser._extract_section_info("Section 3(1)(a) Powers of the Food Authority", 1)
        self.assertEqual(titled.section_number, "3")
        self.assertEqual(titled.title, "Powers of the Food Authority")

    def test_marker_chain_level_assignment(self):
        """Subsection markers push the level up to 4+ (RAG_AGENT_A_SCOPE §2.3).

        ``Section 3(1)(a)`` (number + two marker groups) is level 4, matching
        the audit's ``SectionInfo(num=3, level=4)``; deep chains reach 4+.
        """
        text_cases = [
            ("Section 3(1)(a)", 4),
            ("3(1)(a)(i)", 4),
            ("(1)(a)", 2),
        ]
        for text, expected in text_cases:
            section = self.parser._extract_section_info(text, 1)
            self.assertEqual(section.level, expected, f"Failed for {text}, got {section.level}")

    def test_cache_functionality(self):
        """Test section parser cache."""
        text = "Section 3\n\n3(1)"
        result1 = self.parser.parse_sections(text)
        result2 = self.parser.parse_sections(text)
        self.assertEqual(len(result1), len(result2))

    def test_clean_cache(self):
        """Test cache clearing."""
        text = "Section 3"
        self.parser.parse_sections(text)
        self.parser.clear_cache()
        result = self.parser.parse_sections(text)
        self.assertGreater(len(result), 0)

    def test_parser_thread_safety(self):
        """Test thread safety."""

        text = "Section 3\n\n3(1)\n\n3(1)(a)"

        results = []
        errors = []

        def worker():
            try:
                result = self.parser.parse_sections(text)
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
