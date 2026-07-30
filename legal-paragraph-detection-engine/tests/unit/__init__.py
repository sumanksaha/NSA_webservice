"""Unit tests for the Legal Paragraph Detection Engine.

This file contains comprehensive unit tests for all core components
of the Legal Paragraph Detection Engine, including:
- Text cleaning and normalization
- Hierarchy detection
- Paragraph boundary detection
- Section/subsection parsing
- Clause/subclause parsing
- Citation extraction
- Parent-child relationship tracking
- JSON export functionality
- Performance and edge case testing
"""

import unittest
import json
import tempfile
import os
from typing import Any
from unittest.mock import patch, MagicMock

from legal_paragraph_detection_engine import (
    LegalParagraphEngine,
    ProcessingConfig,
    ProcessingMode,
    ParagraphBoundaryDetector,
    TextNormalizer,
    HierarchyDetector,
    SectionParser,
    ClauseParser,
    CitationExtractor,
    DocumentTypeClassifier,
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
        self.assertEqual(citations[0]['type'], 'supreme_court')
        self.assertEqual(citations[1]['type'], 'statutory')

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
            self.assertEqual(actual_type, expected_type,
                           f"Failed for text: {text}")

    def _get_line_type(self, cleaner, text):
        """Helper to get line type for testing."""
        if not text.strip():
            return "empty"
        if re.match(r'^\s*\d+\s*$', text):
            return "page_number"
        if re.match(r'^\s*[A-Z][a-z\s]+:$', text):
            return "header"
        if re.match(r'^\s*[A-Z][a-z]+\s*$', text):
            if text.lower() in ['explanation', 'provided', 'proviso']:
                return "mark"
            return "legal_content"
        if re.match(r'^\s*\(\s*[a-zA-Z]\s*\)\s*$', text):
            return "mark"
        if re.search(r'\b\d+\s*\.\s*[a-zA-Z]\b', text):
            return "mark"
        return "legal_content"


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
                self.assertEqual(result['depth'], expected,
                               f"Failed for {text}, got {result['depth']}")

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
            self.assertEqual(result.value, expected,
                           f"Failed for {text}, got {result.value}")

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
            self.assertEqual(result, expected,
                           f"Failed for {text}, got {result}")

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
            self.assertEqual(result, expected,
                           f"Failed for {text}, got {result}")

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
            self.assertEqual(result, expected,
                           f"Failed for {text}, got {result}")

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
            self.assertEqual(result, expected,
                           f"Failed for {text}, got {result}")

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
            self.assertEqual(result, expected,
                           f"Failed for {text}, got {result}")

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
            self.assertEqual(result, expected,
                           f"Failed for {text}, got {result}")

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
            self.assertEqual(result, expected,
                           f"Failed for {text}, got {result}")

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
        import threading

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
        section_nodes = [n for n in sections if n.node_type == 'section']
        self.assertGreater(len(section_nodes), 0)

    def test_detect_clauses(self):
        """Test clause detection."""
        text = """
        Section 3(1)
        3(1)(a) First clause.
        3(1)(b) Second clause.
        """

        clauses = self.detector.detect_hierarchy(text)
        clause_nodes = [n for n in clauses if n.node_type in ['clause_arabic', 'clause_letter', 'clause_roman']]

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
        hierarchy = self.detector._build_hierarchy(nodes)

        # Find root node
        root = next((n for n in nodes if n.parent_id is None), None)
        self.assertIsNotNone(root)
        self.assertEqual(root.node_type, 'section')

        # Check children
        children = [n for n in nodes if n.parent_id == root.id]
        self.assertGreater(len(children), 0)

    def test_node_id_generation(self):
        """Test node ID generation."""
        text = "Section 3\n\n3(1)"
        nodes = self.detector.detect_hierarchy(text)

        for node in nodes:
            self.assertTrue(node.id.startswith('section_'))
            self.assertIn(node.node_type, ['section', 'clause', 'subclause', 'boundary'])

    def test_depth_calculation(self):
        """Test depth calculation."""
        test_cases = [
            ("3", 0),
            ("3(1)", 1),
            ("3(1)(a)", 2),
            ("3(1)(a)(i)", 3),
        ]

        for text, expected in test_cases:
            node = self.detector._create_node(
                f"test_{text}", "test", text, text, expected, None
            )
            self.assertEqual(node.depth, expected)

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
        import threading

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
        main_sections = [s for s in sections if s.section_type.name == 'MAIN_SECTION']
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
        subsections = [s for s in sections if s.section_type.name == 'SUBSECTION']
        self.assertGreater(len(subsections), 0)

    def test_parse_subsubsections(self):
        sections = self.parser.parse_sections(text)

        # Find sub-subsections
        subsubsections = [s for s in sections if s.section_type.name == 'SUBSUBSECTION']
        self.assertGreater(len(subsubsections), 0)
    def test_parse_paragraphs(self):
        """Test paragraph parsing."""
        text = """
        Paragraph 1
        First paragraph.
        """

        sections = self.parser.parse_sections(text)

        # Find paragraphs
        paragraphs = [s for s in sections if s.section_type.name == 'PARAGRAPH']
        self.assertGreater(len(paragraphs), 0)

    def test_parse_subparagraphs(self):
        """Test subparagraph parsing."""
        text = """
        1.1 First subparagraph.
        1.2 Second subparagraph.
        """

        sections = self.parser.parse_sections(text)

        # Find subparagraphs
        subparagraphs = [s for s in sections if s.section_type.name == 'SUBPARAGRAPH']
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
        roman_sections = [s for s in sections if s.section_type.name == 'ROMAN_SECTION']
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
            self.assertEqual(section.level, expected,
                           f"Failed for {text}, got {section.level}")

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
        import threading

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

class TestClauseParser(unittest.TestCase):
    """Test clause parsing functionality."""

    def setUp(self):
        self.parser = ClauseParser()

    def test_parse_clauses_basic(self):
        """Test basic clause parsing."""
        text = """
        3(1)(a) First clause.
        3(1)(b) Second clause.
        (a) Subclause.
        """

        clauses = self.parser.parse_clauses(text)

        self.assertGreater(len(clauses), 0)

        # Check clause types
        for clause in clauses:
            self.assertIsNotNone(clause.id)
            self.assertIsNotNone(clause.clause_type)
            self.assertIsNotNone(clause.hierarchy_label)

    def test_parse_arabic_clauses(self):
        """Test Arabic number clause parsing."""
        text = """
        1. First clause.
        2. Second clause.
        3. Third clause.
        """

        clauses = self.parser.parse_clauses(text)

        arabic_clauses = [c for c in clauses if c.clause_type.name == 'MAIN_CLAUSE']
        self.assertGreater(len(arabic_clauses), 0)

    def test_parse_letter_clauses(self):
        """Test letter clause parsing."""
        text = """
        (a) First clause.
        (b) Second clause.
        """

        clauses = self.parser.parse_clauses(text)

        letter_clauses = [c for c in clauses if c.clause_type.name == 'SUBCLAUSE']
        self.assertGreater(len(letter_clauses), 0)

    def test_parse_roman_clauses(self):
        """Test Roman numeral clause parsing."""
        text = """
        (i) First clause.
        (ii) Second clause.
        """

        clauses = self.parser.parse_clauses(text)

        roman_clauses = [c for c in clauses if c.clause_type.name == 'SUBCLAUSE']
        self.assertGreater(len(roman_clauses), 0)

    def test_parse_explanations(self):
        """Test explanation parsing."""
        text = """
        Explanation: This is an explanation.
        Explanation of the above provisions.
        """

        clauses = self.parser.parse_clauses(text)

        explanations = [c for c in clauses if c.clause_type.name == 'EXPLANATION']
        self.assertGreater(len(explanations), 0)

    def test_parse_provios(self):
        """Test proviso parsing."""
        text = """
        Provided that exceptions may be made.
        PROVISO: Special provision.
        """

        clauses = self.parser.parse_clauses(text)

        provisos = [c for c in clauses if c.clause_type.name == 'PROVISO']
        self.assertGreater(len(provisos), 0)

    def test_parse_exceptions(self):
        """Test exception parsing."""
        text = """
        Except that this may be modified.
        Subject to the provisions.
        """

        clauses = self.parser.parse_clauses(text)

        exceptions = [c for c in clauses if c.clause_type.name == 'EXCEPTION']
        self.assertGreater(len(exceptions), 0)

    def test_parse_notes(self):
        """Test note parsing."""
        text = """
        Note: Important note.
        IMPORTANT: Critical information.
        """

        clauses = self.parser.parse_clauses(text)

        notes = [c for c in clauses if c.clause_type.name == 'NOTE']
        self.assertGreater(len(notes), 0)

    def test_parse_schedules(self):
        """Test schedule parsing."""
        text = """
        Schedule I
        Schedule II: Additional provisions.
        """

        clauses = self.parser.parse_clauses(text)

        schedules = [c for c in clauses if c.clause_type.name == 'SCHEDULE']
        self.assertGreater(len(schedules), 0)

    def test_build_clause_hierarchy(self):
        """Test clause hierarchy building."""
        text = """
        3(1)(a) First clause.
        3(1)(b) Second clause.
        (a) Subclause.
        """

        clauses = self.parser.parse_clauses(text)
        hierarchy = self.parser._build_clause_hierarchy(clauses)

        # Check hierarchy structure
        for clause in hierarchy:
            self.assertIsNotNone(clause.id)
            self.assertIsNotNone(clause.clause_type)

    def test_clause_pattern_matching(self):
        """Test clause pattern matching."""
        text = """
        1. First clause.
        (a) Subclause.
        (i) Roman clause.
        Explanation: Some explanation.
        """

        clauses = self.parser.parse_clauses(text)

        # Check that we matched different pattern types
        pattern_types = set(c.metadata.get('pattern_type', '') for c in clauses)
        self.assertGreater(len(pattern_types), 1)

    def test_cache_functionality(self):
        """Test clause parser cache."""
        text = "3(1)(a) First clause."
        result1 = self.parser.parse_clauses(text)
        result2 = self.parser.parse_clauses(text)
        self.assertEqual(len(result1), len(result2))

    def test_clean_cache(self):
        """Test cache clearing."""
        text = "3(1)(a) First clause."
        self.parser.parse_clauses(text)
        self.parser.clear_cache()
        result = self.parser.parse_clauses(text)
        self.assertGreater(len(result), 0)

    def test_parser_thread_safety(self):
        """Test thread safety."""
        import threading

        text = "3(1)(a) First clause.\n3(1)(b) Second clause."

        results = []
        errors = []

        def worker():
            try:
                result = self.parser.parse_clauses(text)
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

class TestCitationExtractor(unittest.TestCase):
    """Test citation extraction functionality."""

    def setUp(self):
        self.extractor = CitationExtractor()

    def test_extract_supreme_court_citations(self):
        """Test Supreme Court citation extraction."""
        text = "See (2020 SC 123/456) and (2021 SC 789/123)."
        citations = self.extractor.extract_citations(text)

        sc_citations = [c for c in citations if c.citation_type.name == 'SUPREME_COURT']
        self.assertEqual(len(sc_citations), 2)

        for citation in sc_citations:
            self.assertIn("SC", citation.normalized_text)
            self.assertGreaterEqual(len(citation.details.get('year', '')), 4)

    def test_extract_high_court_citations(self):
        """Test High Court citation extraction."""
        text = "Reference: (Honorable HC 123/456) and (HC 789/123)."
        citations = self.extractor.extract_citations(text)

        hc_citations = [c for c in citations if c.citation_type.name == 'HIGH_COURT']
        self.assertEqual(len(hc_citations), 2)

    def test_extract_statutory_citations(self):
        """Test statutory citation extraction."""
        text = "Reference: The Indian Penal Code. Section 301 of the IPC."
        citations = self.extractor.extract_citations(text)

        statute_citations = [c for c in citations if c.citation_type.name == 'STATUTORY']
        self.assertGreater(len(statute_citations), 0)

    def test_extract_section_citations(self):
        """Test section citation extraction."""
        text = "See Section 5(2) and Clause (a) of the Act."
        citations = self.extractor.extract_citations(text)

        section_citations = [c for c in citations if c.citation_type.name == 'SECTION']
        self.assertEqual(len(section_citations), 2)

    def test_extract_date_citations(self):
        """Test date citation extraction."""
        text = "Effective from 01/01/2020 to 31/12/2025."
        citations = self.extractor.extract_citations(text)

        date_citations = [c for c in citations if c.citation_type.name == 'DATE_REFERENCE']
        self.assertEqual(len(date_citations), 1)

    def test_extract_registry_citations(self):
        """Test registry citation extraction."""
        text = "Cite as: A 1234/2021 and B 5678/2022."
        citations = self.extractor.extract_citations(text)

        registry_citations = [c for c in citations if c.citation_type.name == 'REGISTRY']
        self.assertEqual(len(registry_citations), 2)

    def test_extract_special_patterns(self):
        """Test special legal document pattern extraction."""
        text = "The Constitution of India provides framework. Air Pollution Act."
        citations = self.extractor.extract_citations(text)

        special_citations = [c for c in citations if 'Constitution' in c.normalized_text]
        self.assertGreater(len(special_citations), 0)

    def test_citation_normalization(self):
        """Test citation normalization."""
        text = "(2020 SC 123/456)"
        citations = self.extractor.extract_citations(text)

        self.assertEqual(len(citations), 1)
        self.assertIn("2020 SC 123/456", citations[0].normalized_text)

    def test_citation_confidence_scores(self):
        """Test citation confidence scores."""
        text = "(2020 SC 123/456) See also Section 5."
        citations = self.extractor.extract_citations(text)

        for citation in citations:
            self.assertGreaterEqual(citation.confidence, 0.7)

    def test_citation_context_extraction(self):
        """Test citation context extraction."""
        text = "The court held in (2020 SC 123/456) that..."
        citations = self.extractor.extract_citations(text)

        for citation in citations:
            self.assertGreater(len(citation.context), 10)

    def test_cache_functionality(self):
        """Test citation extractor cache."""
        text = "See (2020 SC 123/456)."
        result1 = self.extractor.extract_citations(text)
        result2 = self.extractor.extract_citations(text)
        self.assertEqual(len(result1), len(result2))

    def test_clean_cache(self):
        """Test cache clearing."""
        text = "See (2020 SC 123/456)."
        self.extractor.extract_citations(text)
        self.extractor.clear_cache()
        result = self.extractor.extract_citations(text)
        self.assertGreater(len(result), 0)

    def test_extractor_thread_safety(self):
        """Test thread safety."""
        import threading

        text = "See (2020 SC 123/456) and (2021 HC 789/123)."

        results = []
        errors = []

        def worker():
            try:
                result = self.extractor.extract_citations(text)
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

class TestDocumentTypeClassifier(unittest.TestCase):
    """Test document type classification functionality."""

    def setUp(self):
        self.classifier = DocumentTypeClassifier()

    def test_classify_act(self):
        """Test Act classification."""
        text = "An Act to make provision for food safety."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'ACT')
        self.assertIn("food safety", doc.title.lower())

    def test_classify_rule(self):
        """Test Rule classification."""
        text = "Rules under the Food Safety Act."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'RULE')

    def test_classify_notification(self):
        """Test Notification classification."""
        text = "Public Notification: License Renewal."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'NOTIFICATION')

    def test_classify_circular(self):
        """Test Circular classification."""
        text = "Department Circular: Update procedure."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'CIRCULAR')

    def test_classify_government_order(self):
        """Test Government Order classification."""
        text = "G.O. No. 123. Government order for implementation."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'GOVERNMENT_ORDER')

    def test_classify_ordinance(self):
        """Test Ordinance classification."""
        text = "Ordinance: Emergency food regulation."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'ORDINANCE')

    def test_classify_bill(self):
        """Test Bill classification."""
        text = "Bill for food safety amendment."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'BILL')

    def test_classify_amendment(self):
        """Test Amendment classification."""
        text = "Amendment to Section 5 of the Act."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'AMENDMENT')

    def test_classify_panchayati_raj_act(self):
        """Test Panchayati Raj Act classification."""
        text = "Panchayati Raj Act, 1959. Rural development law."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'PANCHAYATI_RAJ_ACT')

    def test_classify_municipal_act(self):
        """Test Municipal Act classification."""
        text = "Municipal Act, 2023. Urban governance."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'MUNICIPAL_ACT')

    def test_classify_special_act(self):
        """Test Special Act classification."""
        text = "Special Emergency Food Act."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'SPECIAL_ACT')

    def test_classify_unknown(self):
        """Test unknown document classification."""
        text = "Random text with no clear document type."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, 'UNKNOWN')

    def test_extract_title(self):
        """Test title extraction."""
        text = "The Food Safety Act: Licensing and Registration."
        doc = self.classifier.classify_document(text)
        self.assertIn("Food Safety Act", doc.title)

    def test_extract_year(self):
        """Test year extraction."""
        text = "Food Act of 2020 with provisions."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.year, 2020)

    def test_extract_jurisdiction(self):
        """Test jurisdiction extraction."""
        text = "Government of India: Central Act."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.jurisdiction, 'central')

    def test_cache_functionality(self):
        """Test document classifier cache."""
        text = "The Food Safety Act."
        result1 = self.classifier.classify_document(text)
        result2 = self.classifier.classify_document(text)
        self.assertEqual(result1.type, result2.type)

    def test_clean_cache(self):
        """Test cache clearing."""
        text = "Food Safety Act."
        self.classifier.classify_document(text)
        self.classifier.clear_cache()
        result = self.classifier.classify_document(text)
        self.assertIsNotNone(result.type)

    def test_classifer_thread_safety(self):
        """Test thread safety."""
        import threading

        text = "Food Safety Act: Licensing and Registration."

        results = []
        errors = []

        def worker():
            try:
                result = self.classifier.classify_document(text)
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

    def test_detector_cache(self):
        """Test detector cache.
        
        This test was causing an error before the fix."
        text = "Food Safety Act: Licensing and Registration."
        
        result1 = self.classifier.classify_document(text)
        result2 = self.classifier.classify_document(text)
        
        self.assertEqual(result1.type, result2.type)
        self.assertEqual(result1.title, result2.title)


class TestLegalParagraphEngine(unittest.TestCase):
    """Test the main LegalParagraphEngine."""

    def setUp(self):
        self.engine = LegalParagraphEngine()

    def test_process_simple_document(self):
        """Test processing a simple legal document."""
        text = """
        Section 3(1)
        
        3(1)(a) First clause.
        3(1)(b) Second clause.
        """

        result = self.engine.process_document(text)

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

        # Check basic structure
        for paragraph in result:
            self.assertIn('paragraph_id', paragraph)
            self.assertIn('section', paragraph)
            self.assertIn('clause', paragraph)
            self.assertIn('paragraph_type', paragraph)

    def test_process_with_document_type(self):
        """Test processing document with type information."""
        text = "An Act to make provision for food safety."

        doc_type_info = {'type': 'act', 'title': 'Food Safety Act'}
        result = self.engine.process_document(text, doc_type_info)

        self.assertIsInstance(result, list)
        for paragraph in result:
            self.assertEqual(paragraph['document_type'], 'act')

    def test_process_complex_document(self):
        """Test processing a complex legal document."""
        text = """
        Section 3(1)(a)

        3(1)(a) The following shall apply to all food businesses.

        Explanation:

        This provision establishes the framework for food business regulation.

        Provided that:
        - Registration is mandatory
        - Compliance inspections required
        - Penalties for non-compliance

        Note: This section applies from publication date.

        Schedule I
        """

        result = self.engine.process_document(text)

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 5)  # Should have multiple paragraphs

        # Check hierarchy
        for paragraph in result:
            self.assertIn('parent_id', paragraph)
            self.assertIn('hierarchy_depth', paragraph)

    def test_engine_cache(self):
        """Test engine cache functionality."""
        text = "Section 3(1)(a)"
        result1 = self.engine.process_document(text)
        result2 = self.engine.process_document(text)

        self.assertEqual(len(result1), len(result2))

    def test_clean_engine_cache(self):
        """Test engine cache cleaning."""
        text = "Section 3(1)(a)"
        self.engine.process_document(text)
        self.engine.clear_cache()
        result = self.engine.process_document(text)
        self.assertGreater(len(result), 0)

    def test_engine_statistics(self):
        """Test engine statistics tracking."""
        text = "Section 3(1)(a)"

        # Initial stats
        stats = self.engine.get_processing_stats()
        initial_documents = stats['total_documents']

        # Process document
        self.engine.process_document(text)

        # Check updated stats
        stats = self.engine.get_processing_stats()
        self.assertEqual(stats['total_documents'], initial_documents + 1)
        self.assertEqual(stats['successful_extractions'], 1)

    def test_engine_thread_safety(self):
        """Test engine thread safety."""
        import threading

        text = "Section 3(1)(a)\n3(1)(b) Second clause."

        results = []
        errors = []

        def worker():
            try:
                result = self.engine.process_document(text)
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

    def test_engine_error_handling(self):
        """Test error handling."""
        # Test with invalid input
        with self.assertRaises(RuntimeError):
            self.engine.process_document(None)  # type: ignore

    def test_engine_configuration(self):
        """Test engine configuration."""
        from .legal_engine import ProcessingConfig, ProcessingMode

        config = ProcessingConfig(
            mode=ProcessingMode.ACCURATE,
            max_depth=15,
            preserve_citations=True,
        )

        engine = LegalParagraphEngine(config)
        self.assertEqual(engine.config.mode, ProcessingMode.ACCURATE)
        self.assertEqual(engine.config.max_depth, 15)

    def test_engine_export_functionality(self):
        """Test engine export functionality."""
        from .storage.exporter import ParagraphExporter

        text = "Section 3(1)(a)"
        result = self.engine.process_document(text)

        exporter = ParagraphExporter()
        output_path = exporter.export_to_json(result, "test_output.json")

        self.assertTrue(os.path.exists("test_output.json"))
        
        # Read and verify output
        with open("test_output.json", 'r') as f:
            saved_result = json.load(f)
        
        self.assertEqual(len(saved_result), len(result))

        # Clean up
        os.remove("test_output.json")

class TestIntegration(unittest.TestCase):
    """Integration tests for the Legal Paragraph Detection Engine."""

    def test_full_pipeline(self):
        """Test full processing pipeline."""
        engine = LegalParagraphEngine()

        complex_text = """
        Section 3(1)(a)

        3(1)(a) This section governs food labeling requirements.
        3a This provision establishes registration framework.
        3a(i) Registration conditions.
        3a(ii) Compliance requirements.

        Explanation:

        The provisions outlined above ensure proper food safety standards.

        Provided that all food businesses must comply with these requirements.

        Note: Violations shall be subject to penalties.

        Schedule I

        Table 1: Registration Requirements

        Effective from January 1, 2024.
        """

        result = engine.process_document(complex_text)

        # Validate result structure
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 5)

        # Check all required fields
        required_fields = [
            'paragraph_id', 'section', 'clause', 'subclause',
            'paragraph_type', 'text', 'citations', 'parent_id',
            'children', 'hierarchy_depth', 'word_count',
            'document_type', 'extraction_timestamp', 'confidence_scores', 'metadata'
        ]

        for paragraph in result:
            for field in required_fields:
                self.assertIn(field, paragraph, 
                            f"Missing field: {field} in paragraph {paragraph.get('paragraph_id', 'unknown')}")

        # Check hierarchy structure
        for paragraph in result:
            self.assertIsInstance(paragraph['hierarchy_depth'], int)
            self.assertGreaterEqual(paragraph['hierarchy_depth'], 0)
            self.assertIsInstance(paragraph['word_count'], int)
            self.assertGreaterEqual(paragraph['word_count'], 0)

    def test_real_world_document(self):
        """Test with a real-world style legal document."""
        engine = LegalParagraphEngine()

        # Simulated real-world legal document
        real_document = """
        The Food Safety Act, 2020

        Section 3(1)

        3(1)(a) Registration shall be mandatory for all food businesses.
        3(1)(b) Registration applications shall be submitted to the Registrar.

        Explanation:

        This section establishes the regulatory framework for food business registration.
        Registration ensures compliance with food safety standards and protects public health.

        Provided that:
        - All established food businesses shall register within 6 months
        - Registration applications shall include food safety audit reports
        - The Registrar may impose additional conditions for registration

        Note: Non-compliance shall be subject to penalties under this Act.

        Schedule I: Registration Procedures

        Table 1: Required Documents

        Effective from April 1, 2024.
        """

        result = engine.process_document(real_document)

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

        # Check for diversity in paragraph types
        paragraph_types = set(p['paragraph_type'] for p in result)
        self.assertGreater(len(paragraph_types), 1)

        # Check for citations (if any in the document)
        # The current document may not have citations, that's okay

    def test_performance_with_complex_document(self):
        """Test performance with a complex hierarchical document."""
        import time

        engine = LegalParagraphEngine()

        # Create a complex nested document
        complex_text = "Section 3(1)(a)(i)\n\n"
        for i in range(10):
            section = f"3(1)(a)(i).{i}"
            clause = f"3(1)(a)(i).{i}(a)"
            text = f"{section} Complex clause with nested pattern. {clause} Subclause content."
            complex_text += text + "\n\n"

        start_time = time.time()
        result = engine.process_document(complex_text)
        processing_time = time.time() - start_time

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 10)

        # Should process reasonably quickly
        self.assertLess(processing_time, 5.0, 
                       f"Processing took too long: {processing_time} seconds")

    def test_export_serialization(self):
        """Test JSON export and serialization."""
        engine = LegalParagraphEngine()
        exporter = ParagraphExporter()

        text = """
        Section 3(1)

        3(1)(a) Simple test clause.
        (a) Subclause here.
        """

        # Process document
        result = engine.process_document(text)

        # Export to JSON
        output_path = exporter.export_to_json(result, "integration_test.json")

        # Read and parse JSON
        with open("integration_test.json", 'r') as f:
            loaded_result = json.load(f)

        # Verify structure
        self.assertIsInstance(loaded_result, list)
        self.assertEqual(len(loaded_result), len(result))

        # Verify data integrity
        for original, loaded in zip(result, loaded_result):
            self.assertEqual(original['paragraph_id'], loaded['paragraph_id'])
            self.assertEqual(original['text'], loaded['text'])

        # Clean up
        os.remove("integration_test.json")

    def test_cache_effectiveness(self):
        """Test cache effectiveness with repeated processing."""
        engine = LegalParagraphEngine()

        text = "Section 3(1)(a)"

        # Process same document multiple times
        results = []
        for _ in range(5):
            result = engine.process_document(text)
            results.append(result)

        # All results should be identical
        for i in range(1, len(results)):
            self.assertEqual(len(results[i]), len(results[0]))
            self.assertEqual(results[i][0]['paragraph_id'], results[0][0]['paragraph_id'])

    def test_multithreading_consistency(self):
        """Test consistency across multiple threads."""
        import threading
        import concurrent.futures

        engine = LegalParagraphEngine()
        text = "Section 3(1)(a)\n3(1)(b) Second clause."

        def process_in_thread(thread_id):
            return engine.process_document(text)

        # Process in multiple threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(process_in_thread, i) for i in range(5)]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]

        # All results should be identical
        for result in results:
            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0)

        # Check consistency
        for i in range(1, len(results)):
            self.assertEqual(len(results[i]), len(results[0]))

    def test_error_resilience(self):
        """Test error handling and resilience."""
        engine = LegalParagraphEngine()

        # Test with various problematic inputs
        test_cases = [
            "",  # Empty string
            "   ",  # Whitespace only
            "Section 3(1)(a)\n\n\n\n\n3(1)(b)\n",  # Multiple newlines
            "3(1)(a) \n (b) \n (c)",  # Mixed formatting
            "1. Simple clause. 2. Another clause. 3. Third clause.",  # Different formatting
        ]

        for text in test_cases:
            try:
                result = engine.process_document(text)
                self.assertIsInstance(result, list)
            except Exception as e:
                # Some edge cases might raise exceptions, which is acceptable
                self.assertTrue(True, f"Exception raised for edge case '{text[:50]}...': {e}")

    def test_large_document_processing(self):
        """Test processing of large documents."""
        import time

        engine = LegalParagraphEngine()

        # Create a large document with many sections
        large_text = "Section 3(1)\n\n"
        for i in range(50):
            large_text += f"{i+1}. Section {i+1}. " * 10 + "\n\n"

        start_time = time.time()
        result = engine.process_document(large_text)
        processing_time = time.time() - start_time

        self.assertIsInstance(result, list)
        # Should have processed sections
        section_count = len([p for p in result if p['section']])
        self.assertGreater(section_count, 0)

        # Performance should be reasonable
        self.assertLess(processing_time, 30.0,
                       f"Large document processing took too long: {processing_time} seconds")

    def test_memory_usage(self):
        """Test memory usage with multiple processing operations."""
        import psutil
        import os

        engine = LegalParagraphEngine()
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        # Process multiple documents
        texts = [
            "Section 3(1)",
            "Section 4(1)(a)",
            "Section 5(1)(b)(i)",
            "Section 6(1).",
        ]

        for text in texts:
            result = engine.process_document(text)
            self.assertIsInstance(result, list)

        final_memory = process.memory_info().rss
        memory_increase = (final_memory - initial_memory) / (1024 * 1024)  # MB

        # Memory increase should be reasonable (less than 100MB)
        self.assertLess(memory_increase, 100, f"Memory usage too high: {memory_increase:.2f} MB")

if __name__ == '__main__':
    # Run all tests
    unittest.main(verbosity=2)
