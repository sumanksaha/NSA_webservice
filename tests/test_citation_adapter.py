"""Tests for the Agent A Phase 2 citation adapter (app/rag/citation_adapter.py).

Pins the CitationExtractor -> §5.1/§5.2 mapping: reference strings for the
Qdrant payload ``citations`` field, structured ``{"section", "type"}`` dicts
for the ``LegalChunk.citations`` JSON column, de-duplication, and the §2.3
regression guard (``"of the Act"`` is never emitted as a statute name).

A fake extractor (returning real :class:`LegalCitation` objects) drives the
mapping tests; real-engine checks cover actual extraction on FSS Act text.
"""

from __future__ import annotations

import json

from app.rag.citation_adapter import CitationAdapter, ExtractedCitation
from legal_paragraph_detection_engine.src import (
    CitationExtractor,
    CitationType,
    LegalCitation,
)


def _citation(
    citation_type=CitationType.SECTION,
    normalized_text="Section 55",
    details=None,
    confidence=0.85,
):
    return LegalCitation(
        citation_type=citation_type,
        normalized_text=normalized_text,
        details=details or {},
        confidence=confidence,
        context="... some context ...",
    )


class _FakeExtractor:
    def __init__(self, citations):
        self._citations = citations
        self.calls = 0

    def extract_citations(self, text):
        self.calls += 1
        return list(self._citations)


class _FakeChunk:
    def __init__(self, chunk_text, citations=None):
        self.chunk_text = chunk_text
        self.citations = citations


class TestCitationAdapterMapping:
    def test_extract_maps_citation_fields(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor(
                [
                    _citation(
                        citation_type=CitationType.SECTION,
                        normalized_text="Section 55",
                        details={"section_reference": "55"},
                        confidence=0.85,
                    )
                ]
            )
        )
        extracted = adapter.extract("Pursuant to Section 55 of the Act")
        assert len(extracted) == 1
        citation = extracted[0]
        assert isinstance(citation, ExtractedCitation)
        assert citation.citation_type == "section"  # enum name lowercased
        assert citation.reference == "Section 55"
        assert citation.details == {"section_reference": "55"}
        assert citation.confidence == 0.85

    def test_extract_dedupes_by_type_and_reference(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor(
                [
                    _citation(normalized_text="Section 55"),
                    _citation(normalized_text="Section 55"),  # duplicate
                    _citation(
                        citation_type=CitationType.STATUTORY,
                        normalized_text="The Food Safety and Standards Act",
                    ),
                ]
            )
        )
        extracted = adapter.extract("text")
        assert [c.reference for c in extracted] == [
            "Section 55",
            "The Food Safety and Standards Act",
        ]

    def test_extract_skips_empty_references(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor(
                [
                    _citation(normalized_text=""),
                    _citation(normalized_text="Section 7"),
                ]
            )
        )
        assert [c.reference for c in adapter.extract("text")] == ["Section 7"]

    def test_to_dict_is_json_serializable(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor([_citation(normalized_text="Section 55")])
        )
        citation = adapter.extract("text")[0]
        json.loads(json.dumps(citation.to_dict()))


class TestPayloadCitations:
    def test_payload_citations_returns_reference_strings(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor(
                [
                    _citation(normalized_text="Section 55"),
                    _citation(
                        citation_type=CitationType.STATUTORY,
                        normalized_text="The Food Safety and Standards Act",
                    ),
                ]
            )
        )
        assert adapter.payload_citations("text") == [
            "Section 55",
            "The Food Safety and Standards Act",
        ]


class TestStructuredCitations:
    def test_section_citation_uses_section_reference(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor(
                [_citation(normalized_text="Section 55", details={"section_reference": "55"})]
            )
        )
        structured = adapter.structured_citations("text")
        assert structured == [
            {"section": "55", "type": "section", "confidence": 0.85}
        ]

    def test_statute_citation_uses_statute_name(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor(
                [
                    _citation(
                        citation_type=CitationType.STATUTORY,
                        normalized_text="The Food Safety and Standards Act",
                        details={"statute_name": "The Food Safety and Standards Act"},
                        confidence=0.8,
                    )
                ]
            )
        )
        structured = adapter.structured_citations("text")
        assert structured[0]["section"] == "The Food Safety and Standards Act"
        assert structured[0]["type"] == "statutory"

    def test_case_citation_uses_case_number(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor(
                [
                    _citation(
                        citation_type=CitationType.SUPREME_COURT,
                        normalized_text="2020 SC 123/456",
                        details={"year": "2020", "case_number": "123"},
                        confidence=0.95,
                    )
                ]
            )
        )
        structured = adapter.structured_citations("text")
        assert structured[0]["section"] == "123"
        assert structured[0]["type"] == "supreme_court"

    def test_reference_fallback_for_other_citation_types(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor(
                [
                    _citation(
                        citation_type=CitationType.DATE_REFERENCE,
                        normalized_text="01/01/2020 to 31/12/2025",
                        details={"year": "2025", "date": "01/01/2020 to 31/12/2025"},
                        confidence=0.75,
                    ),
                    _citation(
                        citation_type=CitationType.CONSTITUTION,
                        normalized_text="Constitution of India",
                        details={"jurisdiction": "India"},
                        confidence=0.85,
                    ),
                    _citation(
                        citation_type=CitationType.REGISTRY,
                        normalized_text="A 1234/2021",
                        details={"year": "2021", "registry_reference": "A 1234/2021"},
                        confidence=0.85,
                    ),
                ]
            )
        )
        structured = adapter.structured_citations("text")
        by_type = {s["type"]: s["section"] for s in structured}
        assert by_type["date_reference"] == "01/01/2020 to 31/12/2025"  # reference fallback
        assert by_type["constitution"] == "Constitution of India"  # reference fallback
        assert by_type["registry"] == "A 1234/2021"  # reference fallback


class TestEnrichChunk:
    def test_enrich_chunk_sets_citations_from_text(self):
        adapter = CitationAdapter(
            extractor=_FakeExtractor(
                [_citation(normalized_text="Section 55", details={"section_reference": "55"})]
            )
        )
        chunk = _FakeChunk(chunk_text="Section 55 applies here")
        adapter.enrich_chunk(chunk)
        assert chunk.citations == ["Section 55"]

    def test_enrich_chunk_skips_empty_text(self):
        adapter = CitationAdapter(extractor=_FakeExtractor([]))
        chunk = _FakeChunk(chunk_text="", citations=None)
        adapter.enrich_chunk(chunk)
        assert chunk.citations is None

    def test_enrich_chunk_returns_chunk(self):
        adapter = CitationAdapter(extractor=_FakeExtractor([]))
        chunk = _FakeChunk(chunk_text="plain text")
        assert adapter.enrich_chunk(chunk) is chunk


class TestLazyEngine:
    def test_real_engine_built_when_not_injected(self):
        adapter = CitationAdapter()  # no injector
        assert adapter._get_extractor() is not None
        assert isinstance(adapter._get_extractor(), CitationExtractor)


class TestRealCitationEngine:
    """Real CitationExtractor behaviour — including the §2.3 regression guard."""

    def test_extracts_section_and_statute(self):
        adapter = CitationAdapter()  # real engine
        text = (
            "Pursuant to Section 55 of the Food Safety and Standards Act, 2006, "
            "every food business operator shall comply."
        )
        references = adapter.payload_citations(text)
        assert any(ref.startswith("Section 55") for ref in references)
        assert any("Food Safety and Standards Act" in ref for ref in references)

    def test_never_emits_of_the_act(self):
        """§2.3 regression: bare cross-references are not statute names."""
        adapter = CitationAdapter()
        references = adapter.payload_citations(
            "Pursuant to Section 3 of the Act, the FBO must register."
        )
        assert references == ["Section 3"]  # no statutory "of the Act" citation

    def test_extracts_supreme_court_citation(self):
        adapter = CitationAdapter()
        text = "The Supreme Court held in (2020 SC 123/456) that the matter is barred."
        references = adapter.payload_citations(text)
        assert "2020 SC 123/456" in references

    def test_extracts_constitution_of_india(self):
        adapter = CitationAdapter()
        text = "Article 21 of the Constitution of India guarantees life and liberty."
        references = adapter.payload_citations(text)
        assert any("Constitution of India" in ref for ref in references)

    def test_structured_citations_with_real_engine(self):
        adapter = CitationAdapter()
        structured = adapter.structured_citations("Section 55 of the Act")
        assert structured and structured[0]["type"] == "section"
        assert structured[0]["section"] == "55"
