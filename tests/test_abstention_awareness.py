"""Abstention-awareness for groundedness / hallucination (ADR-0010 §2.5).

The bug: ``groundedness_score`` is a ratio over citations, so an answer that
correctly refuses scores 0.0 and was flagged ``hallucination_detected=True``.
ADR-0010 measured 8/21 -> 19/21 abstentions being scored as hallucinations,
which read as a groundedness collapse (-0.29) that was pure metric artifact.

These tests pin the fixed behaviour and, just as importantly, the boundary:
a *fabricated citation* is still a fabrication, and a short degenerate answer
is not laundered into "not a hallucination".
"""

from __future__ import annotations

import pytest

from app.rag.generation.abstention import (
    ABSTENT_MARKERS_RE,
    MIN_ABSTENTION_CHARS,
    is_abstention,
)
from app.rag.generation.citation_tracker import CitationTracker
from app.rag.generation.sanitizer import ResponseSanitizer
from app.rag.retrieval.result import Citation, RetrievedChunk
from app.rag.verification.hallucination_detector import HallucinationDetector

#: A real refusal: substantive, negation-anchored, cites nothing.
ABSTENTION = (
    "The retrieved provisions do not specify any penalty amount for this "
    "conduct, so the corpus does not establish a monetary limit."
)


def _chunks(n: int = 2) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=f"c{i}",
            score=0.9,
            text=f"Section {i} text about penalties and registration.",
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


# --------------------------------------------------------------------------- #
# Lexicon
# --------------------------------------------------------------------------- #


class TestLexicon:
    @pytest.mark.parametrize(
        "text",
        [
            "I cannot determine the penalty from the retrieved provisions.",
            "No provision in the corpus establishes a monetary limit here.",
            "The corpus does not prescribe a specific threshold for this.",
            "The fee is not ascertainable from the materials provided.",
        ],
    )
    def test_recognises_refusals(self, text):
        assert is_abstention(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Section 31 imposes a penalty of ten thousand rupees.",
            "The FSO must issue a notice to the business operator.",
        ],
    )
    def test_does_not_match_assertions(self, text):
        assert not is_abstention(text)

    def test_short_answer_is_not_an_abstention(self):
        """A 3-word answer asserts nothing, but it is not a *refusal*.

        Laundering it into "not a hallucination" would be a safety
        regression, so the length floor is load-bearing.
        """
        assert not is_abstention("no cites")
        assert not is_abstention("cannot find")
        assert MIN_ABSTENTION_CHARS == 20

    def test_empty_is_not_an_abstention(self):
        assert not is_abstention("")
        assert not is_abstention("   ")

    def test_banned_markers_stripped(self):
        text = "I cannot find the answer to this question anywhere."
        assert is_abstention(text)
        assert not is_abstention(text, banned={"cannot find"})


# --------------------------------------------------------------------------- #
# ResponseSanitizer
# --------------------------------------------------------------------------- #


class TestSanitizerAbstentionAware:
    def test_correct_refusal_is_not_a_hallucination(self):
        chunks = _chunks(2)
        san = ResponseSanitizer().sanitize(ABSTENTION, [], chunks)
        assert san.abstained is True
        assert san.groundedness_score == 0.0  # unchanged: it really cites nothing
        assert san.hallucination_detected is False

    def test_short_uncited_answer_still_flagged(self):
        """The pre-existing behaviour this fix must not erode."""
        chunks = _chunks(2)
        san = ResponseSanitizer().sanitize("no cites", [], chunks)
        assert san.abstained is False
        assert san.groundedness_score == 0.0
        assert san.hallucination_detected is True

    def test_refusal_with_fabricated_citation_still_flagged(self):
        """Suppression applies only when the abstention cites nothing."""
        chunks = _chunks(2)
        san = ResponseSanitizer().sanitize(ABSTENTION, [_citation("ghost")], chunks)
        assert san.abstained is True
        assert san.hallucination_detected is True

    def test_asserting_answer_with_valid_citations_unaffected(self):
        chunks = _chunks(2)
        cits = CitationTracker().extract("See [1] and [2]", chunks)
        san = ResponseSanitizer().sanitize("The penalty is set by the Act. [1] [2]", cits, chunks)
        assert san.abstained is False
        assert san.groundedness_score == 1.0
        assert san.hallucination_detected is False


# --------------------------------------------------------------------------- #
# HallucinationDetector
# --------------------------------------------------------------------------- #


class TestDetectorAbstentionAware:
    def test_refusal_not_detected(self):
        report = HallucinationDetector().detect(ABSTENTION, _chunks(2))
        assert report.abstained is True
        assert report.detected is False
        assert report.detail["abstained"] is True

    def test_hallucinating_answer_still_detected(self):
        report = HallucinationDetector().detect(
            "Section 999 imposes a penalty of 10000 gold coins. [1]",
            _chunks(2),
        )
        assert report.abstained is False
        assert report.detected is True

    def test_empty_response_still_detected(self):
        report = HallucinationDetector().detect("", [])
        assert report.detected is True
        assert report.abstained is False

    def test_abstained_flag_defaults_false_for_back_compat(self):
        """Reports built by hand / older callers must keep working."""
        report = HallucinationDetector().detect("Section 1 is fine.", _chunks(1))
        assert report.abstained is False


# --------------------------------------------------------------------------- #
# Eval side: one lexicon, two length policies
# --------------------------------------------------------------------------- #


class TestEvalSideSharesTheLexicon:
    def test_same_regex_object(self):
        from evaluation import abstention_rule

        assert abstention_rule.ABSTAIN_CREDIT_RE is ABSTENT_MARKERS_RE

    def test_frozen_length_heuristic_preserved(self):
        """The eval scorer still treats <20 chars as an abstention.

        That is a frozen v1/v2 scoring convention; the production detector is
        deliberately stricter. Both policies are intentional.
        """
        from evaluation.abstention_rule import abstain_match

        assert abstain_match("no cites") is True
        assert is_abstention("no cites") is False

    def test_summarize_splits_by_abstention(self):
        from evaluation.abstention_rule import summarize_groundedness

        rows = {
            "Q1": {"answer": "Section 31 imposes a penalty. [1]", "groundedness_score": 1.0},
            "Q2": {"answer": "Section 31 imposes a penalty. [1]", "groundedness_score": 0.8},
            "Q3": {"answer": ABSTENTION, "groundedness_score": 0.0},
            "Q4": {"answer": ABSTENTION, "groundedness_score": 0.0},
        }
        got = summarize_groundedness(rows)
        assert got["n"] == 4
        assert got["n_answering"] == 2
        assert got["abstention_rate"] == 0.5
        assert got["groundedness_all"] == pytest.approx(0.45)
        # The point of the split: raw mean says 0.45, the answering
        # population is actually 0.90. That gap is the ADR-0010 §2.5 artifact.
        assert got["groundedness_answering"] == pytest.approx(0.9)

    def test_summarize_all_abstained_has_no_answering_mean(self):
        from evaluation.abstention_rule import summarize_groundedness

        got = summarize_groundedness({"Q1": {"answer": ABSTENTION, "groundedness_score": 0.0}})
        assert got["groundedness_answering"] is None
        assert got["abstention_rate"] == 1.0

    def test_summarize_empty_input(self):
        from evaluation.abstention_rule import summarize_groundedness

        assert summarize_groundedness({})["n"] == 0
