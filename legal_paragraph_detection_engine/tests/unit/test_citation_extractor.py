"""Unit tests for TestCitationExtractor (moved out of tests/unit/__init__.py)."""

import threading
import unittest

from legal_paragraph_detection_engine import (
    CitationExtractor,
)


class TestCitationExtractor(unittest.TestCase):
    """Test citation extraction functionality."""

    def setUp(self):
        self.extractor = CitationExtractor()

    def test_extract_supreme_court_citations(self):
        """Test Supreme Court citation extraction."""
        text = "See (2020 SC 123/456) and (2021 SC 789/123)."
        citations = self.extractor.extract_citations(text)

        sc_citations = [c for c in citations if c.citation_type.name == "SUPREME_COURT"]
        self.assertEqual(len(sc_citations), 2)

        for citation in sc_citations:
            self.assertIn("SC", citation.normalized_text)
            self.assertGreaterEqual(len(citation.details.get("year", "")), 4)

    def test_extract_high_court_citations(self):
        """Test High Court citation extraction."""
        text = "Reference: (Honorable HC 123/456) and (HC 789/123)."
        citations = self.extractor.extract_citations(text)

        hc_citations = [c for c in citations if c.citation_type.name == "HIGH_COURT"]
        self.assertEqual(len(hc_citations), 2)

    def test_extract_statutory_citations(self):
        """Test statutory citation extraction."""
        text = "Reference: The Indian Penal Code. Section 301 of the IPC."
        citations = self.extractor.extract_citations(text)

        statute_citations = [c for c in citations if c.citation_type.name == "STATUTORY"]
        self.assertGreater(len(statute_citations), 0)

    def test_extract_section_citations(self):
        """Test section citation extraction."""
        text = "See Section 5(2) and Clause (a) of the Act."
        citations = self.extractor.extract_citations(text)

        section_citations = [c for c in citations if c.citation_type.name == "SECTION"]
        self.assertEqual(len(section_citations), 2)

    def test_extract_date_citations(self):
        """Test date citation extraction."""
        text = "Effective from 01/01/2020 to 31/12/2025."
        citations = self.extractor.extract_citations(text)

        date_citations = [c for c in citations if c.citation_type.name == "DATE_REFERENCE"]
        self.assertEqual(len(date_citations), 1)

    def test_extract_registry_citations(self):
        """Test registry citation extraction."""
        text = "Cite as: A 1234/2021 and B 5678/2022."
        citations = self.extractor.extract_citations(text)

        registry_citations = [c for c in citations if c.citation_type.name == "REGISTRY"]
        self.assertEqual(len(registry_citations), 2)

    def test_statutory_citation_captures_full_statute_name(self):
        """The full statute name is captured, not a truncated fragment.

        RAG_AGENT_A_SCOPE §2.3: previously ``"the Food Safety and Standards
        Act, 2006"`` was misidentified as fragments such as ``"the Fo"`` or
        ``"of the Act"``. The captured statute must be the full name.
        """
        text = "the Food Safety and Standards Act, 2006"
        citations = self.extractor.extract_citations(text)

        statute_citations = [c for c in citations if c.citation_type.name == "STATUTORY"]
        self.assertEqual(len(statute_citations), 1)
        self.assertEqual(statute_citations[0].normalized_text, "Food Safety and Standards Act")

    def test_of_the_act_is_not_emitted_as_statute_name(self):
        """Bare ``"of the Act"`` cross-references are not statute names.

        RAG_AGENT_A_SCOPE §2.3: ``"Section 14 of the Act."`` must only yield
        the SECTION citation — the 2-word fragment before ``Act`` fails the
        minimum 3-word statute-name requirement.
        """
        text = "Section 14 of the Act."
        citations = self.extractor.extract_citations(text)

        statute_citations = [c for c in citations if c.citation_type.name == "STATUTORY"]
        self.assertEqual(len(statute_citations), 0)
        section_citations = [c for c in citations if c.citation_type.name == "SECTION"]
        self.assertEqual(len(section_citations), 1)

    def test_statute_name_requires_minimum_three_words(self):
        """A statute name must have >= 3 words (RAG_AGENT_A_SCOPE §2.3).

        ``"Air Pollution Act"`` (2-word name) is rejected; the 4-word
        ``"Prevention of Food Adulteration Act"`` is captured.
        """
        two_word = "The authority cited Air Pollution Act."
        two_word_cits = [c for c in self.extractor.extract_citations(two_word) if c.citation_type.name == "STATUTORY"]
        self.assertEqual(len(two_word_cits), 0)

        full = "Prevention of Food Adulteration Act, 1954"
        full_cits = [c for c in self.extractor.extract_citations(full) if c.citation_type.name == "STATUTORY"]
        self.assertEqual(len(full_cits), 1)
        self.assertEqual(full_cits[0].normalized_text, "Prevention of Food Adulteration Act")

    def test_statutory_citations_deduplicated(self):
        """Overlapping statutory patterns emit one citation per statute.

        RAG_AGENT_A_SCOPE §2.3: ``"The Food Safety and Standards Act"`` is
        matched by both the ``The ...`` and the bare-name patterns; the result
        must contain exactly one STATUTORY citation.
        """
        text = "Pursuant to The Food Safety and Standards Act, 2006."
        citations = self.extractor.extract_citations(text)

        statute_citations = [c for c in citations if c.citation_type.name == "STATUTORY"]
        self.assertEqual(len(statute_citations), 1)
        self.assertEqual(statute_citations[0].normalized_text, "The Food Safety and Standards Act")

    def test_extract_special_patterns(self):
        """Test special legal document pattern extraction."""
        text = "The Constitution of India provides framework. Air Pollution Act."
        citations = self.extractor.extract_citations(text)

        special_citations = [c for c in citations if "Constitution" in c.normalized_text]
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
