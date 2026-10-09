"""Regression guard for the shadow-verifier wiring (fix 2).

``linear.generate_node`` computed the shadow bundle with
``shadow_report(answer, chunks, verifications, None)``.  ``hardened_citation_ratio``
treats a missing citation result as "no citations" and returns
``(0.50, "no_citations")``, so *every* non-empty answer was flagged
``no_citations`` and ``citation_ratio_shadow`` was a constant — the shadow
verifier logged a wrong citation score for every answer, silently.

The shadow verifier is the evidence base for routing the hardened scorer into
production (SPEC-3/SPEC-4).  A shadow log that is wrong by construction is
worse than no shadow log: it looks like a measurement.

These tests pin that the citation result is the *real* one.
"""

from __future__ import annotations

import pytest

from app.rag.retrieval.result import Citation, RetrievedChunk
from app.rag.verification.citation_validator import CitationValidator
from app.rag.verification.hardened_scorer import hardened_citation_ratio, shadow_report


def _chunks(n: int = 3) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=f"c{i}",
            score=0.9,
            text=f"Section {i} prescribes a penalty of {i + 1} rupees.",
            document_title="FSS Act",
            section_number=str(i + 1),
        )
        for i in range(n)
    ]


def _citation(chunk_id: str) -> Citation:
    return Citation(
        chunk_id=chunk_id,
        section_number=None,
        document_title="FSS Act",
        document_type="act",
        authority="FSSAI",
        url=None,
        snippet="t",
        confidence=0.7,
    )


class TestHardenedCitationRatio:
    def test_real_result_is_used_not_treated_as_missing(self):
        chunks = _chunks(3)
        real = CitationValidator().validate([_citation("c0"), _citation("c1")], chunks)
        ratio, flag = hardened_citation_ratio(real, "Answer with citations [1] [2].")
        assert flag is None
        assert ratio == pytest.approx(1.0)

    def test_genuinely_uncited_answer_is_still_flagged(self):
        """The flag must still fire — just not on every answer."""
        ratio, flag = hardened_citation_ratio(None, "A substantive answer with no markers.")
        assert ratio == 0.50
        assert flag == "no_citations"

    def test_invalid_citation_lowers_the_real_ratio(self):
        chunks = _chunks(3)
        real = CitationValidator().validate([_citation("c0"), _citation("ghost")], chunks)
        ratio, flag = hardened_citation_ratio(real, "Answer citing one real and one fake source.")
        assert flag is None
        assert ratio < 1.0


class TestShadowReportIsNotConstant:
    def test_cited_and_uncited_answers_differ(self):
        """The bug's signature: identical citation_ratio_shadow for both."""
        chunks = _chunks(3)
        cited = CitationValidator().validate([_citation("c0")], chunks)
        answer = "Section 1 prescribes a penalty of 1 rupees. [Source 1]"

        with_cites = shadow_report(answer, chunks, [], cited)
        without_cites = shadow_report("A substantive answer with no markers at all.", chunks, [], None)

        assert with_cites["citation_ratio_shadow"] == pytest.approx(1.0)
        assert "no_citations" not in with_cites["shadow_flags"]
        assert without_cites["citation_ratio_shadow"] == 0.50
        assert "no_citations" in without_cites["shadow_flags"]
        # And the composite must actually move, not just the sub-score.
        assert with_cites["groundedness_shadow"] > without_cites["groundedness_shadow"]

    def test_reports_abstention(self):
        chunks = _chunks(2)
        rep = shadow_report(
            "The corpus does not establish any penalty amount for this conduct.",
            chunks,
            [],
            None,
        )
        assert rep["abstained"] is True
