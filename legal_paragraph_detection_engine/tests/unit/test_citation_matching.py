"""Unit tests for the engine's citation-to-paragraph matching (T-30)."""

import unittest

from legal_paragraph_detection_engine import LegalParagraphEngine
from legal_paragraph_detection_engine.src.storage.citation import CitationType, LegalCitation


def _make_citation(
    normalized_text: str,
    source_text: str | None = None,
    details: dict[str, str] | None = None,
) -> LegalCitation:
    """Build a minimal LegalCitation for matching tests."""
    return LegalCitation(
        citation_type=CitationType.SECTION,
        normalized_text=normalized_text,
        details=details or {},
        confidence=0.85,
        context="",
        source_text=source_text,
    )


class TestCitationMatching(unittest.TestCase):
    """Contract tests for the T-30 compiled-regex citation matching."""

    def setUp(self) -> None:
        self.engine = LegalParagraphEngine()

    def _find(self, paragraph_text: str, citations: list[LegalCitation]) -> list[LegalCitation]:
        return self.engine._find_citations_for_paragraph(paragraph_text, citations, None, None)

    def test_exact_normalized_match(self) -> None:
        """A citation whose normalized text appears verbatim is matched."""
        citation = _make_citation("2020 SC 123/456")
        found = self._find("See (2020 SC 123/456) for the ruling.", [citation])
        self.assertEqual(found, [citation])

    def test_case_insensitive_match(self) -> None:
        """F-14: matching must be case-insensitive (naive `in` was not)."""
        citation = _make_citation("Section 5")
        found = self._find("see section 5 of the act.", [citation])
        self.assertEqual(found, [citation])

    def test_word_boundary_rejects_partial_number(self) -> None:
        """'Section 5' must not match 'Section 50' or 'Section 512'."""
        citation = _make_citation("Section 5")
        for text in ("Section 50", "Section 512", "Section 5x"):
            with self.subTest(text=text):
                self.assertEqual(self._find(text, [citation]), [], f"matched inside {text!r}")

    def test_word_boundary_accepts_following_punctuation(self) -> None:
        """'Section 5' followed by punctuation is still a valid match."""
        citation = _make_citation("Section 5")
        found = self._find("See Section 5.", [citation])
        self.assertEqual(found, [citation])

    def test_source_text_fallback(self) -> None:
        """Normalized text differs from raw source; source must also match."""
        citation = _make_citation("2020 SC 123/456", source_text="(2020 SC 123/456)")
        # Normalized form is not present verbatim (parens stripped), source is.
        found = self._find("See (2020 SC 123/456) above.", [citation])
        self.assertEqual(found, [citation])

    def test_no_match_returns_empty(self) -> None:
        """Unrelated citations are not attached to a paragraph."""
        citation = _make_citation("Section 5")
        self.assertEqual(self._find("Nothing to do here.", [citation]), [])

    def test_section_relevance_fallback(self) -> None:
        """Falls back to section relevance via citation details."""
        citation = _make_citation("Some act name", details={"section_reference": "5"})
        found = self.engine._find_citations_for_paragraph("Unrelated prose.", [citation], "5", None)
        self.assertEqual(found, [citation])

    def test_clause_relevance_fallback_case_insensitive(self) -> None:
        """Clause fallback is case-insensitive."""
        citation = _make_citation("Some act name", details={"clause_reference": "a"})
        found = self.engine._find_citations_for_paragraph("Unrelated prose.", [citation], None, "A")
        self.assertEqual(found, [citation])

    def test_pattern_cache_is_populated_and_cleared(self) -> None:
        """Compiled patterns are cached and cleared with the engine cache."""
        citation = _make_citation("Section 5")
        self._find("See Section 5.", [citation])
        self.assertGreater(len(self.engine._citation_pattern_cache), 0)
        self.engine.clear_cache()
        self.assertEqual(len(self.engine._citation_pattern_cache), 0)

    def test_citation_attached_in_full_pipeline(self) -> None:
        """End-to-end: the citing paragraph exposes the SC citation in output."""
        text = "Section 3\n\n3(1) See (2020 SC 123/456) for authority."
        result = self.engine.process_document(text)
        cited = [p for p in result if p["citations"]]
        self.assertTrue(cited, "expected at least one paragraph with citations")
        # The paragraph actually citing the SC case must carry it.
        sc_paragraphs = [p for p in result if any("2020 SC 123/456" in c["reference"] for c in p["citations"])]
        self.assertTrue(sc_paragraphs, "SC citation not attached to any paragraph")
        self.assertIn("2020 SC 123/456", sc_paragraphs[0]["text"])

    def test_parenthesized_reference_matches(self) -> None:
        """Escaped-paren references match exactly (e.g. 'Section 5(2)')."""
        citation = _make_citation("Section 5(2)")
        found = self._find("See Section 5(2) of the act.", [citation])
        self.assertEqual(found, [citation])

    def test_parenthesized_reference_prefix_behavior(self) -> None:
        """A parent citation is relevant to its own sub-clauses (deliberate).

        ``Section 5(2)`` matches inside ``Section 5(2)(a)`` — the sub-clause
        references the parent citation, so attaching it is intentional (T-30
        word-boundary guards only apply to word-character endings).
        """
        citation = _make_citation("Section 5(2)")
        found = self._find("See Section 5(2)(a) for details.", [citation])
        self.assertEqual(found, [citation])


if __name__ == "__main__":
    unittest.main()
