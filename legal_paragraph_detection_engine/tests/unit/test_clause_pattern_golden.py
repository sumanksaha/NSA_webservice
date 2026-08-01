"""T-05: Golden tests for every ClauseParser pattern type (F-04 hardening).

Each entry in ``GOLDEN_CASES`` documents a representative line, the
``pattern_type`` it must resolve to, the resulting ``ClauseType`` and the
``hierarchy_label``. Values were captured from the live parser so the suite
locks in current correct behavior.

The ``test_every_pattern_type_has_a_golden_case`` guard fails if a new
pattern is added to ``ClauseParser.CLAUSE_PATTERNS`` without a golden entry —
keeping the suite complete as the parser evolves.
"""

import unittest

from legal_paragraph_detection_engine import ClauseParser

# (input_line, expected_pattern_type, expected_clause_type, expected_hierarchy_label)
GOLDEN_CASES = [
    # --- Arabic main clauses ---
    ("1. First clause.", "main_arabic", "MAIN_CLAUSE", "1."),
    ("2. Second clause.", "main_arabic", "MAIN_CLAUSE", "2."),
    ("10. Tenth clause.", "main_arabic", "MAIN_CLAUSE", "10."),
    # --- Parenthesised main clauses ---
    ("1(a) Subclause content.", "main_parentheses", "SUBCLAUSE", "1(a)"),
    # --- Letter subclauses ---
    ("(a) Subclause.", "subclause_letter", "SUBCLAUSE", "(a)"),
    ("(b) Another subclause.", "subclause_letter", "SUBCLAUSE", "(b)"),
    # --- Bracket subclauses ---
    ("[a] Bracket subclause.", "subclause_bracket", "MAIN_CLAUSE", "[a]"),
    # --- Roman-numeral subclauses ---
    ("(ii) Roman subclause.", "subclause_roman", "SUBSUBCLAUSE", "(ii)"),
    ("(iii) Third roman.", "subclause_roman", "SUBSUBCLAUSE", "(iii)"),
    # --- Complex nested patterns ---
    ("1(2)(a) Nested clause.", "nested_complex", "SUBCLAUSE", "1(2)(a)"),
    ("3(1)(a) First clause.", "nested_complex", "SUBCLAUSE", "3(1)(a)"),
    # --- Special legal patterns ---
    ("Explanation: This explains.", "explanation", "EXPLANATION", "Explanation: This explains."),
    ("Explanation 2: More.", "explanation", "EXPLANATION", "Explanation 2: More."),
    ("Illustration: Eg.", "explanation", "EXPLANATION", "Illustration: Eg."),
    ("Provided that this applies.", "proviso", "PROVISO", "Provided that this applies."),
    ("Provided further that x.", "proviso", "PROVISO", "Provided further that x."),
    ("PROVISO: Special provision.", "proviso", "PROVISO", "PROVISO: Special provision."),
    ("Except that this is excluded.", "exception", "EXCEPTION", "Except that this is excluded."),
    ("Note: Important.", "note", "NOTE", "Note: Important."),
    ("IMPORTANT: Critical information.", "note", "NOTE", "IMPORTANT: Critical information."),
    # --- References ---
    ("See Section 3 above.", "reference", "REFERENCE", "See Section 3 above."),
    ("See also Section 4.", "reference", "REFERENCE", "See also Section 4."),
    # --- Schedules and tables ---
    ("Schedule I", "schedule_table", "SCHEDULE", "Schedule I"),
    ("Schedule II: Additional.", "schedule_table", "SCHEDULE", "Schedule II: Additional."),
    ("Table 1: Data.", "schedule_table", "SCHEDULE", "Table 1: Data."),
]


class TestClausePatternGolden(unittest.TestCase):
    """Golden tests for ClauseParser pattern resolution."""

    def setUp(self):
        self.parser = ClauseParser()

    def _resolve(self, line):
        """Parse a single line and return the resolved ClauseData (or None)."""
        clauses = self.parser.parse_clauses(line)
        return clauses[0] if clauses else None

    def test_every_pattern_type_has_a_golden_case(self):
        """Every pattern in CLAUSE_PATTERNS must have at least one golden entry."""
        declared = {p.pattern_type for p in ClauseParser.CLAUSE_PATTERNS}
        covered = {pattern_type for _, pattern_type, _, _ in GOLDEN_CASES}
        missing = declared - covered
        self.assertFalse(missing, f"Pattern types without a golden case: {sorted(missing)}")

    def test_every_golden_case_resolves(self):
        """Every golden line must resolve to exactly one clause."""
        for line, *_ in GOLDEN_CASES:
            clause = self._resolve(line)
            self.assertIsNotNone(clause, f"No clause resolved for: {line!r}")

    def test_golden_pattern_types(self):
        """Each golden line must resolve to the expected pattern_type."""
        for line, pattern_type, _, _ in GOLDEN_CASES:
            clause = self._resolve(line)
            self.assertEqual(
                clause.metadata.get("pattern_type"),
                pattern_type,
                f"pattern_type mismatch for: {line!r}",
            )

    def test_golden_clause_types(self):
        """Each golden line must resolve to the expected ClauseType name."""
        for line, _, clause_type, _ in GOLDEN_CASES:
            clause = self._resolve(line)
            self.assertEqual(
                clause.clause_type.name,
                clause_type,
                f"clause_type mismatch for: {line!r}",
            )

    def test_golden_hierarchy_labels(self):
        """Each golden line must produce the expected hierarchy_label."""
        for line, _, _, label in GOLDEN_CASES:
            clause = self._resolve(line)
            self.assertEqual(clause.hierarchy_label, label, f"hierarchy_label mismatch for: {line!r}")

    def test_priority_overlaps_resolve_stably(self):
        """Lines that could match several patterns must resolve deterministically.

        Cases where the regex alphabet overlaps:
        - ``(i)`` matches the single-letter pattern BEFORE the roman pattern
          (both priority 20; ``subclause_letter`` appears first in the list).
        - ``(ii)``/``(iii)`` are two+ characters, so only the roman pattern matches.
        - ``3(1)(a)`` is nested (priority 30), not ``main_parentheses`` (10).
        """
        cases = [
            ("(i) Single roman.", "subclause_letter"),
            ("(ii) Roman subclause.", "subclause_roman"),
            ("3(1)(a) First clause.", "nested_complex"),
            ("1(a) Subclause content.", "main_parentheses"),
            ("2. Second clause.", "main_arabic"),
            ("Schedule I", "schedule_table"),
            ("See Section 3 above.", "reference"),
        ]
        for line, expected in cases:
            clause = self._resolve(line)
            self.assertEqual(
                clause.metadata.get("pattern_type"),
                expected,
                f"Overlap resolution mismatch for: {line!r}",
            )

    def test_plain_sentence_is_not_a_clause(self):
        """Prose without a clause marker must not resolve to a clause."""
        line = "Section 3 content without number."
        self.assertIsNone(self._resolve(line), f"Expected NO clause for: {line!r}")

    def test_full_document_resolves_expected_patterns(self):
        """A multi-clause document yields the expected pattern sequence."""
        text = (
            "1. First clause.\n"
            "3(1)(a) Nested clause.\n"
            "(a) Subclause.\n"
            "Explanation: This explains.\n"
            "Provided that this applies.\n"
            "See Section 3 above."
        )
        clauses = self.parser.parse_clauses(text)
        resolved = [c.metadata.get("pattern_type") for c in clauses]
        expected = [
            "main_arabic",
            "nested_complex",
            "subclause_letter",
            "explanation",
            "proviso",
            "reference",
        ]
        self.assertEqual(resolved, expected)


if __name__ == "__main__":
    unittest.main()
