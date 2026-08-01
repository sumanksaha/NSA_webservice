"""T-01b: Regression tests for TextCleaner on date-bearing lines (T-01 fix).

T-01 fixed the root cause: ``TextCleaner.clean_text()`` raised ``AttributeError``
on date-bearing lines because ``TextType.DATES_AND_NUMBERS`` was missing from the
enum. These tests guard that fix and document the expected preservation of
common Indian legal-document date formats (full month, ordinal, abbreviated).
"""

import unittest

from legal_paragraph_detection_engine import TextCleaner
from legal_paragraph_detection_engine.src.utils.text_cleaner import TextType


class TestTextCleanerDates(unittest.TestCase):
    """Regression suite for date-bearing lines in TextCleaner."""

    def setUp(self):
        self.cleaner = TextCleaner()

    def test_clean_text_full_month_date(self):
        """A bare '12 January 2020' date line must survive cleaning unchanged."""
        result = self.cleaner.clean_text("12 January 2020")
        self.assertEqual(result, "12 January 2020")

    def test_clean_text_date_in_sentence(self):
        """A date embedded in a sentence must be preserved."""
        result = self.cleaner.clean_text("The Act was notified on 12 January 2020.")
        self.assertIn("12 January 2020", result)

    def test_clean_text_ordinal_date(self):
        """Ordinal dates like '1st of January 2020' must be preserved."""
        result = self.cleaner.clean_text("1st of January 2020")
        self.assertIn("1st of January 2020", result)

    def test_clean_text_abbreviated_month(self):
        """Abbreviated-month dates like '31 Dec 2025' must be preserved."""
        result = self.cleaner.clean_text("31 Dec 2025")
        self.assertIn("31 Dec 2025", result)

    def test_clean_text_multiple_dates(self):
        """Multiple date-bearing lines in one document all survive."""
        text = "12 January 2020\n\nSection 3 content.\n\n31 Dec 2025"
        result = self.cleaner.clean_text(text)
        self.assertIn("12 January 2020", result)
        self.assertIn("31 Dec 2025", result)
        self.assertIn("Section 3 content", result)

    def test_abbreviated_month_date_classified_as_dates_and_numbers(self):
        """A line matching the dates_and_numbers regex must classify as such.

        The ``dates_and_numbers`` patterns only cover abbreviated 3-letter
        months (``31 Dec 2025``), not full month names (``12 January 2020`` —
        the alternation would consume ``Jan`` and then fail on ``uary 2020``).
        Full-month lines fall through to LEGAL_CONTENT and are still preserved.
        """
        line_type = self.cleaner._classify_line_type("31 Dec 2025")
        self.assertEqual(line_type, TextType.DATES_AND_NUMBERS)

    def test_full_month_date_classified_as_legal_content(self):
        """Full-month dates don't match the abbreviated-month regex — they are
        treated as plain legal content but still preserved verbatim."""
        line_type = self.cleaner._classify_line_type("12 January 2020")
        self.assertEqual(line_type, TextType.LEGAL_CONTENT)

    def test_date_line_never_crashes_with_blank_context(self):
        """Dates mixed with blank lines and headers must not raise."""
        text = "12 January 2020\n\nSubject: Notified\n\n5 May 2021"
        result = self.cleaner.clean_text(text)  # must not raise AttributeError
        self.assertIn("12 January 2020", result)
        self.assertIn("5 May 2021", result)


if __name__ == "__main__":
    unittest.main()
