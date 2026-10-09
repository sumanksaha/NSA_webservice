"""Abstention-aware scoring (ADR-0010 §2.5) — regression lock.

``ResponseSanitizer.sanitize`` measures groundedness as a ratio over
citations, so an answer that cites nothing scores 0.0 and used to be flagged
as a hallucination.  For a *correct* refusal that is the exact inverse of the
truth, and it is how a prompt change that raised the abstention rate from
8/21 to 19/21 came to look like a groundedness collapse (-0.29).

The fix records abstention separately (``abstained``) and suppresses the
hallucination flag for a refusal that cites nothing, while leaving
``groundedness_score`` at its measured value because that is what
``GROUNDEDNESS_RETRY_BELOW`` is calibrated against.

These tests pin the behaviour, including the case that must NOT be
suppressed: a degenerate short answer is ungrounded, not an abstention.

No network, no LLM, no Qdrant.
"""
from __future__ import annotations

from app.rag.generation.abstention import MIN_ABSTENTION_CHARS, is_abstention
from app.rag.generation.citation_tracker import CitationTracker
from app.rag.generation.sanitizer import ResponseSanitizer
from app.rag.retrieval.result import RetrievedChunk


def _chunks() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk_id="c1", text="Section 31 licensing requirement.", score=1.0, section_number="31"),
        RetrievedChunk(chunk_id="c2", text="Section 16 registration.", score=1.0, section_number="16"),
    ]


def _sanitize(text: str):
    chunks = _chunks()
    tracker = CitationTracker()
    citations = tracker.extract(text, chunks, {1: chunks[0], 2: chunks[1]})
    return ResponseSanitizer().sanitize(text, citations, chunks), len(citations)


ABSTAIN_NO_CITATION = (
    "I cannot find the answer in the provided context. The documents do not "
    "address it at all in any way whatsoever here."
)
ABSTAIN_MENTIONS_SECTION = (
    "I cannot find the answer in the provided context. Section 31(2) is not "
    "present; the context only covers Section 16 and the conversion timeline."
)
SUBSTANTIVE = "Under Section 31 of the FSS Act, a licence is required before operating. [1]"
SHORT_DEGENERATE = "n/a"


class TestSanitizerIsAbstentionAware:
    def test_correct_refusal_is_not_flagged_as_hallucination(self):
        result, n_cit = _sanitize(ABSTAIN_NO_CITATION)
        assert n_cit == 0, "fixture must cite nothing for this path"
        assert result.abstained is True
        assert result.hallucination_detected is False

    def test_refusal_mentioning_a_section_is_still_not_flagged(self):
        result, _ = _sanitize(ABSTAIN_MENTIONS_SECTION)
        assert result.abstained is True
        assert result.hallucination_detected is False

    def test_substantive_answer_is_not_marked_abstained(self):
        result, n_cit = _sanitize(SUBSTANTIVE)
        assert n_cit >= 1
        assert result.abstained is False
        assert result.hallucination_detected is False

    def test_short_degenerate_answer_is_still_flagged(self):
        """Regression guard: must NOT be laundered into 'not a hallucination'."""
        result, _ = _sanitize(SHORT_DEGENERATE)
        assert result.abstained is False
        assert result.hallucination_detected is True

    def test_groundedness_score_keeps_its_measured_value(self):
        """Deliberate: routing thresholds are calibrated against this number.

        Abstention-awareness is carried by the `abstained` flag, not by
        inflating groundedness, so `GROUNDEDNESS_RETRY_BELOW` stays valid.
        """
        result, _ = _sanitize(ABSTAIN_NO_CITATION)
        assert result.groundedness_score == 0.0


class TestIsAbstention:
    def test_requires_substantive_length(self):
        assert is_abstention(SHORT_DEGENERATE) is False
        assert len(SHORT_DEGENERATE) < MIN_ABSTENTION_CHARS

    def test_empty_is_not_abstention(self):
        assert is_abstention("") is False

    def test_negation_anchored_markers_match(self):
        assert is_abstention(ABSTAIN_NO_CITATION) is True
        assert is_abstention("The corpus does not establish that any penalty applies here.") is True

    def test_answer_merely_mentioning_a_subject_is_not_abstention(self):
        # Must not match: substantive assertion with no negation anchor.
        assert is_abstention("A licence under Section 31 is required for every food business operator.") is False
