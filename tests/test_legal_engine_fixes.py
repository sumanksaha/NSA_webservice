"""§2.3 regression tests — legal engine fixes through the Flask service layer.

``RAG_AGENT_A_SCOPE.md`` §2.3 documented two bugs in the legal paragraph
detection engine that were fixed on 2026-08-08:

1. ``CitationExtractor`` — ``"of the Act"`` was misidentified as the statute
   name instead of ``"the Food Safety and Standards Act, 2006"``.
2. ``SectionParser`` — subsection-marker chains (``(1)(a)``) were silently
   dropped and misclassified as section titles; marker-bearing ``Section``
   headers now reach level 4+.

These tests pin the fixed behaviour through the same entry points the Flask
app uses: ``app.services.legal_engine.analyze_legal_text`` (full pipeline) and
``app.services.legal_engine.get_legal_engine`` (engine accessor).
"""

from __future__ import annotations

import pytest

from app.services.legal_engine import analyze_legal_text, extract_section_references, get_legal_engine
from legal_paragraph_detection_engine.src.parsers.section_parser import SectionType


def _citations_of_type(analysis: dict, citation_type: str) -> list[str]:
    """Collect every citation reference of a given type across all paragraphs."""
    return [
        c["reference"]
        for para in analysis["paragraphs"]
        for c in para["citations"]
        if c["type"] == citation_type
    ]


def _find_section(engine, text: str, content: str):
    """Return the SectionData whose raw content equals ``content``.

    Searches the whole parsed tree (roots and descendants) so the test stays
    correct even if a marker chain is later nested under its parent section.
    """
    sections = engine.section_parser.parse_sections(text)

    def _walk(entries):
        for entry in entries:
            if entry.content == content:
                return entry
            found = _walk(entry.children or [])
            if found is not None:
                return found
        return None

    section = _walk(sections)
    assert section is not None, f"no parsed section with content {content!r} in {text!r}"
    return section


class TestCitationExtractorFixesViaService:
    """§2.3 CitationExtractor fixes observable through ``analyze_legal_text``."""

    def test_full_statute_name_captured(self):
        analysis = analyze_legal_text(
            "Pursuant to the Food Safety and Standards Act, 2006, the Food Authority shall act."
        )
        statutory = _citations_of_type(analysis, "statutory")
        assert set(statutory) == {"Food Safety and Standards Act"}

    def test_no_of_the_act_or_fragment_statutes(self):
        analysis = analyze_legal_text(
            "Pursuant to the provisions of the Food Safety and Standards Act, 2006, "
            "the Food Authority shall act."
        )
        statutory = _citations_of_type(analysis, "statutory")
        # The full statute name is present and no bare "of the Act" reference
        # or lead-in fragment is emitted as a statute name.
        assert "Food Safety and Standards Act" in statutory
        assert not any("of the Act" in ref for ref in statutory)

    def test_of_the_act_yields_only_section_citation(self):
        analysis = analyze_legal_text("Liability under Section 14 of the Act.")
        assert _citations_of_type(analysis, "statutory") == []
        assert set(_citations_of_type(analysis, "section")) == {"Section 14"}
        # The app's auto-suggest helper still resolves the section reference.
        assert extract_section_references(analysis) == ["14"]

    def test_statutory_citations_deduplicated(self):
        analysis = analyze_legal_text(
            "Pursuant to The Food Safety and Standards Act, 2006 and the provisions of "
            "the Food Safety and Standards Act, 2006."
        )
        statutory = _citations_of_type(analysis, "statutory")
        assert set(statutory) == {"The Food Safety and Standards Act"}


class TestSectionParserFixesViaService:
    """§2.3 SectionParser fixes exercised via the app's engine accessor."""

    @pytest.fixture()
    def engine(self):
        return get_legal_engine()()

    def test_marker_chain_recognised_not_dropped(self, engine):
        section = _find_section(engine, "(1)(a)\n\n(1)(a) First clause.", "(1)(a)")
        assert section.section_type is SectionType.SUBSUBSECTION
        assert section.section_number is None
        assert section.title is None

    def test_marker_chain_with_content_title(self, engine):
        section = _find_section(engine, "(1)(a) First clause.", "(1)(a) First clause.")
        assert section.section_type is SectionType.SUBSUBSECTION
        assert section.section_number is None
        assert section.title == "First clause."

    def test_subsection_markers_never_section_title(self, engine):
        sections = engine.section_parser.parse_sections(
            "Section 3(1)(a)\n\nSection 3(1)(a) Powers of the Food Authority"
        )
        by_content = {s.content: s for s in sections}
        assert by_content["Section 3(1)(a)"].title is None
        assert by_content["Section 3(1)(a) Powers of the Food Authority"].title == "Powers of the Food Authority"

    def test_marker_chain_level_assignment(self, engine):
        sections = engine.section_parser.parse_sections("Section 3(1)(a)\n\n3(1)(a)(i)\n\nSection 3")
        levels = {s.content: s.level for s in sections}
        assert levels["Section 3(1)(a)"] == 4
        assert levels["3(1)(a)(i)"] == 4
        assert levels["Section 3"] == 1

    def test_pipeline_processes_marker_chain_text(self):
        """End-to-end sanity: marker-chain text still yields clean paragraphs."""
        analysis = analyze_legal_text(
            "Section 3(1)\n\n(1)(a) The Food Authority shall ensure food safety.\n\n"
            "Section 14 of the Act."
        )
        assert analysis["paragraphs"]
        assert _citations_of_type(analysis, "statutory") == []
        section = _citations_of_type(analysis, "section")
        assert "Section 3(1)" in section  # may attach to header + clause paragraphs
        assert "Section 14" in section


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
