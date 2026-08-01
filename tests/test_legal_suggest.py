"""T-46b tests — legal auto-suggest for case-file generation.

Current coverage (implemented):
- Pure extraction helper ``extract_section_references`` (shared by the
  case-file and adjudication auto-suggest features).

Pending — T-46b legal auto-suggest helpers/routes not yet implemented:
- ``suggest_from_analysis`` (case-file auto-suggest) + POST
  /case_file_generator/suggest_legal route
- ``extract_adjudication_suggestions`` (adjudication auto-suggest) + POST
  /adjudication/suggest_legal route

When the helpers/routes land, restore the ``TestSuggestFromAnalysis``,
``TestExtractAdjudicationSuggestions``, ``TestSuggestLegalRoute`` and
``TestAdjudicationSuggestLegalRoute`` classes from the T-46b spec
(``LEGAL_PARAGRAPH_DETECTION_ENGINE.md`` / ``legal_paragraph_detection_engine``).
"""

from __future__ import annotations

import pytest

from app.services.legal_engine import extract_section_references

REPORT_TEXT = (
    "The sample of Taaja Jalpan Nilgiri Chanachur was analysed and found to be "
    "misbranded within the meaning of clause (zz) of sub-section (1) of section 3 "
    "of the Food Safety and Standards Act, 2006. The product contravenes the "
    "provisions of section 26(2)(ii) read with section 2(1)(a) of the said Act "
    "and is punishable under section 52 of the said Act."
)


def _analysis_with_citations(refs: list[str]) -> dict:
    """Build a minimal analyze_legal_text()-shaped result with section citations."""
    return {
        "summary": {"total_paragraphs": 1, "avg_confidence": 0.698},
        "paragraphs": [
            {
                "citations": [{"type": "section", "reference": ref} for ref in refs]
                + [{"type": "statutory", "reference": "of the said Act"}],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestExtractSectionReferences:
    def test_extracts_top_level_section_numbers(self):
        analysis = _analysis_with_citations(["section 52", "section 26(2)", "section 2(1)", "section 3"])
        assert extract_section_references(analysis) == ["2", "26", "3", "52"]

    def test_dedupes_and_sorts(self):
        analysis = _analysis_with_citations(["section 52", "section 52", "section 51"])
        assert extract_section_references(analysis) == ["51", "52"]

    def test_ignores_non_section_citations(self):
        analysis = _analysis_with_citations([])
        # Only a statutory citation — no section refs
        analysis["paragraphs"][0]["citations"] = [
            {"type": "statutory", "reference": "of the Food Safety and Standards Act"}
        ]
        assert extract_section_references(analysis) == []

    def test_empty_analysis(self):
        assert extract_section_references({"paragraphs": []}) == []
        assert extract_section_references({}) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
