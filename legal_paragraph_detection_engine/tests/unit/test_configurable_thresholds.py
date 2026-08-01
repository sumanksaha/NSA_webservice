"""Unit tests for configurable heuristic thresholds (T-34)."""

import unittest

from legal_paragraph_detection_engine import (
    LegalParagraphEngine,
    ParagraphInfo,
    ProcessingConfig,
    TextCleaner,
    TextNormalizer,
)
from legal_paragraph_detection_engine.src.core.paragraph import ParagraphType


class TestConfigurableThresholds(unittest.TestCase):
    """Configurable thresholds replace magic numbers (T-34, F-12)."""

    def test_paragraph_boundary_chars_default(self) -> None:
        """Default TextNormalizer keeps the historical 100-char boundary."""
        cleaner = TextNormalizer()
        self.assertEqual(cleaner._paragraph_boundary_chars, 100)

    def test_paragraph_boundary_chars_custom(self) -> None:
        """A lower boundary splits long paragraphs earlier."""
        # ~59 chars after strip: > 50 (tight) but <= 100 (default).
        medium_line = "word " * 12
        text = f"{medium_line}\n\nsecond paragraph"
        default = TextNormalizer()
        tight = TextNormalizer(paragraph_boundary_chars=50)

        default_paras = default.split_into_paragraphs(text)
        tight_paras = tight.split_into_paragraphs(text)

        # Default: ~59 chars <= 100 -> no boundary, single paragraph.
        # Tight: ~59 chars > 50 -> boundary, two paragraphs.
        self.assertEqual(len(default_paras), 1)
        self.assertGreater(len(tight_paras), len(default_paras))

    def test_continuation_max_words_default(self) -> None:
        """Default TextCleaner keeps the historical 3-word continuation rule."""
        cleaner = TextCleaner()
        self.assertEqual(cleaner._continuation_max_words, 3)

    def test_continuation_max_words_custom(self) -> None:
        """A higher word threshold keeps more short lines joined."""
        # Previous line does not end with punctuation; current line has 4 words.
        default = TextCleaner()
        lenient = TextCleaner(continuation_max_words=5)

        prev = "some earlier line"
        current = "a b c d"
        self.assertFalse(default._continues_previous_line(prev, current))
        self.assertTrue(lenient._continues_previous_line(prev, current))

    def test_engine_wires_paragraph_boundary_chars(self) -> None:
        """The engine passes its configured threshold into TextNormalizer."""
        engine = LegalParagraphEngine(ProcessingConfig(paragraph_boundary_chars=40))
        self.assertEqual(engine.text_normalizer._paragraph_boundary_chars, 40)

    def test_confidence_curve_uses_configured_divisor(self) -> None:
        """content_quality reaches 1.0 at the configured word curve."""
        # 75 words on the default 150 curve -> 0.25 + 75/150 = 0.75.
        engine = LegalParagraphEngine()
        para = ParagraphInfo(
            id="p1",
            text="x",
            paragraph_type=ParagraphType.NORMAL,
            start_line=0,
            end_line=0,
            word_count=75,
        )
        scores = engine._calculate_confidence_scores(para, [])
        self.assertAlmostEqual(scores["content_quality"], 0.75, places=6)

        # 75 words on a 100-word curve -> 0.25 + 75/100 = 1.0 (capped).
        engine2 = LegalParagraphEngine(ProcessingConfig(content_quality_word_curve=100.0))
        scores2 = engine2._calculate_confidence_scores(para, [])
        self.assertAlmostEqual(scores2["content_quality"], 1.0, places=6)

    def test_heuristic_thresholds_emitted_in_output(self) -> None:
        """The active thresholds are exported per paragraph (JSON-safe)."""
        engine = LegalParagraphEngine()
        result = engine.process_document("Section 3\n\n3(1) Some content here.")
        thresholds = result[0]["heuristic_thresholds"]
        self.assertEqual(thresholds["paragraph_boundary_chars"], 100)
        self.assertEqual(thresholds["content_quality_word_curve"], 150.0)


if __name__ == "__main__":
    unittest.main()
