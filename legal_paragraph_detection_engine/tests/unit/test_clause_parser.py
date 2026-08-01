"""Unit tests for TestClauseParser (moved out of tests/unit/__init__.py)."""

import threading
import unittest

from legal_paragraph_detection_engine import (
    ClauseParser,
)


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

        arabic_clauses = [c for c in clauses if c.clause_type.name == "MAIN_CLAUSE"]
        self.assertGreater(len(arabic_clauses), 0)

    def test_parse_letter_clauses(self):
        """Test letter clause parsing."""
        text = """
        (a) First clause.
        (b) Second clause.
        """

        clauses = self.parser.parse_clauses(text)

        letter_clauses = [c for c in clauses if c.clause_type.name == "SUBCLAUSE"]
        self.assertGreater(len(letter_clauses), 0)

    def test_parse_roman_clauses(self):
        """Test Roman numeral clause parsing."""
        text = """
        (i) First clause.
        (ii) Second clause.
        """

        clauses = self.parser.parse_clauses(text)

        roman_clauses = [c for c in clauses if c.clause_type.name == "SUBCLAUSE"]
        self.assertGreater(len(roman_clauses), 0)

    def test_parse_explanations(self):
        """Test explanation parsing."""
        text = """
        Explanation: This is an explanation.
        Explanation of the above provisions.
        """

        clauses = self.parser.parse_clauses(text)

        explanations = [c for c in clauses if c.clause_type.name == "EXPLANATION"]
        self.assertGreater(len(explanations), 0)

    def test_parse_provios(self):
        """Test proviso parsing."""
        text = """
        Provided that exceptions may be made.
        PROVISO: Special provision.
        """

        clauses = self.parser.parse_clauses(text)

        provisos = [c for c in clauses if c.clause_type.name == "PROVISO"]
        self.assertGreater(len(provisos), 0)

    def test_parse_exceptions(self):
        """Test exception parsing."""
        text = """
        Except that this may be modified.
        Subject to the provisions.
        """

        clauses = self.parser.parse_clauses(text)

        exceptions = [c for c in clauses if c.clause_type.name == "EXCEPTION"]
        self.assertGreater(len(exceptions), 0)

    def test_parse_notes(self):
        """Test note parsing."""
        text = """
        Note: Important note.
        IMPORTANT: Critical information.
        """

        clauses = self.parser.parse_clauses(text)

        notes = [c for c in clauses if c.clause_type.name == "NOTE"]
        self.assertGreater(len(notes), 0)

    def test_parse_schedules(self):
        """Test schedule parsing."""
        text = """
        Schedule I
        Schedule II: Additional provisions.
        """

        clauses = self.parser.parse_clauses(text)

        schedules = [c for c in clauses if c.clause_type.name == "SCHEDULE"]
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
        pattern_types = set(c.metadata.get("pattern_type", "") for c in clauses)
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

    def test_ids_are_line_derived(self):
        """T-06 (F-10): clause ids must be derived from the line number."""
        text = "1. First clause.\n2. Second clause.\n3. Third clause."
        clauses = self.parser.parse_clauses(text)
        # Lines 0, 1, 2 (the text has no blank lines).
        expected_ids = ["clause_0", "clause_1", "clause_2"]
        self.assertEqual([c.id for c in clauses], expected_ids)
        self.assertEqual([c.start_line for c in clauses], [0, 1, 2])

    def test_ids_line_derived_with_blank_lines(self):
        """T-06: ids track the real line index even with blank lines."""
        text = "1. First clause.\n\n\n3(1)(a) Nested clause."
        clauses = self.parser.parse_clauses(text)
        ids = {c.id for c in clauses}
        self.assertIn("clause_0", ids)  # line 0
        self.assertIn("clause_3", ids)  # line 3 (blank lines at 1, 2)
        # No counter-style ids remain.
        self.assertFalse(any(not c.id.startswith(("clause_", "clause_ctx_")) for c in clauses))

    def test_ids_survive_clear_cache(self):
        """T-06 (F-10): ids must be identical after clear_cache() reparse."""
        text = "1. First clause.\n\n3(1)(a) Nested clause."
        first = self.parser.parse_clauses(text)
        first_ids = [c.id for c in first]
        self.parser.clear_cache()
        second = self.parser.parse_clauses(text)
        second_ids = [c.id for c in second]
        self.assertEqual(first_ids, second_ids)

    def test_ids_unique_within_document(self):
        """T-06: line-derived ids must be unique within a document."""
        text = "\n".join(["1. First clause.", "(a) Subclause.", "Explanation: note."])
        clauses = self.parser.parse_clauses(text)
        ids = [c.id for c in clauses]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ids_same_across_parser_instances(self):
        """T-06: the same document yields the same ids on a fresh parser."""
        text = "1. First clause.\n\n3(1)(a) Nested clause."
        other_parser = ClauseParser()
        first = self.parser.parse_clauses(text)
        second = other_parser.parse_clauses(text)
        self.assertEqual([c.id for c in first], [c.id for c in second])

    def test_parser_thread_safety(self):
        """Test thread safety."""

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
