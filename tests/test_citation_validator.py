"""Tests for Phase 3 CitationValidator (standalone + integration)."""

from __future__ import annotations

from app.rag.retrieval.result import Citation, RetrievedChunk
from app.rag.verification import CitationValidationResult, CitationValidator


def _chunks(n=3):
    return [
        RetrievedChunk(chunk_id=f"c{i}", score=0.9, text=f"chunk text {i}",
                       section_number=str(i + 1))
        for i in range(n)
    ]


class TestCitationValidatorStandalone:
    def test_returns_result_type(self):
        result = CitationValidator().validate([], _chunks(2))
        assert isinstance(result, CitationValidationResult)

    def test_all_valid_score(self):
        chunks = _chunks(3)
        cits = [
            Citation(chunk_id="c0", section_number="1", document_title="D",
                     document_type="act", authority="FS", url=None, snippet="", confidence=0.85),
            Citation(chunk_id="c1", section_number="2", document_title="D",
                     document_type="act", authority="FS", url=None, snippet="", confidence=0.85),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert len(result.valid) == 2
        assert result.score == 1.0

    def test_empty_citations_neutral(self):
        result = CitationValidator().validate([], _chunks(2))
        assert result.score == 0.0
        assert len(result.valid) == 0

    def test_section_mismatch_partial(self):
        chunks = _chunks(1)
        cits = [
            Citation(chunk_id="c0", section_number="99", document_title="D",
                     document_type="act", authority="FS", url=None, snippet="", confidence=0.85),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert len(result.section_mismatches) == 1
        assert result.score == 0.55

    def test_invalid_citation(self):
        chunks = _chunks(1)
        cits = [
            Citation(chunk_id="ghost", section_number=None, document_title="D",
                     document_type="act", authority="FS", url=None, snippet="", confidence=0.5),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert len(result.invalid) == 1
        assert result.score == 0.0

    def test_mixed_citations(self):
        chunks = _chunks(3)
        cits = [
            Citation(chunk_id="c0", section_number="1", document_title="D",
                     document_type="act", authority="FS", url=None, snippet="", confidence=0.85),
            Citation(chunk_id="ghost", section_number=None, document_title="D",
                     document_type="act", authority="FS", url=None, snippet="", confidence=0.5),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert len(result.valid) == 1
        assert len(result.invalid) == 1
