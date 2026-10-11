"""Tests for Phase 3 RAG verification — ClaimExtractor, EvidenceVerifier,
CitationValidator, GroundednessScorer, and HallucinationDetector.

No Qdrant/network required — all verification runs against in-memory
``RetrievedChunk`` objects with rapidfuzz text matching.
"""

from __future__ import annotations

import pytest
from rapidfuzz import fuzz

from app.rag.retrieval.result import Citation, RetrievedChunk
from app.rag.verification import (
    CitationValidator,
    ClaimExtractor,
    EvidenceVerifier,
    GroundednessScorer,
    HallucinationDetector,
)
from app.rag.verification.evidence_verifier import _PROHIBITION_RE


def _make_chunks(n=3, section_base="55"):
    return [
        RetrievedChunk(
            chunk_id=f"c{i}",
            score=0.9 - i * 0.1,
            text=f"Section {section_base} of the FSS Act, 2006 states that "
            f"food businesses must obtain a license. This is chunk {i}.",
            section_number=f"{section_base}" if i == 0 else None,
            document_title="FSS Act 2006",
            document_type="act",
            authority="FSSAI",
        )
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# ClaimExtractor
# --------------------------------------------------------------------------- #
class TestClaimExtractor:
    def test_extracts_section_claims(self):
        text = "Section 55 requires a license. Section 3(1)(a) imposes penalties."
        claims = ClaimExtractor().extract(text)
        assert len(claims) == 2
        assert "55" in claims[0].section_numbers
        assert "3(1)(a)" in claims[1].section_numbers

    def test_extracts_percent_and_amount(self):
        text = "A penalty of 100% of turnover or Rs. 10,000 may apply."
        claims = ClaimExtractor().extract(text)
        assert len(claims) >= 1
        entities = claims[0].entities
        assert "100" in entities.get("percent", [])
        assert any("Rs" in a for a in entities.get("amount", []))

    def test_extracts_authority(self):
        text = "The FSS Act and FSSAI regulations govern this."
        claims = ClaimExtractor().extract(text)
        assert len(claims) >= 1
        assert "authority" in claims[0].entities

    def test_empty_response(self):
        assert ClaimExtractor().extract("") == []

    def test_filters_short_sentences(self):
        text = "Yes. No."
        assert ClaimExtractor().extract(text) == []

    def test_preserves_index(self):
        text = "Section 55 says X. Section 56 says Y."
        claims = ClaimExtractor().extract(text)
        assert claims[0].index == 0
        assert claims[1].index == 1

    def test_to_dict(self):
        claims = ClaimExtractor().extract("Section 55 says X.")
        d = claims[0].to_dict()
        assert d["text"] == "Section 55 says X."
        assert d["section_numbers"] == ["55"]
        assert "index" in d


# --------------------------------------------------------------------------- #
# EvidenceVerifier
# --------------------------------------------------------------------------- #
class TestEvidenceVerifier:
    def test_section_match_verifies(self):
        chunks = _make_chunks(3, section_base="55")
        claim = ClaimExtractor().extract("Section 55 requires a license.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert ver.confidence == 0.85
        assert ver.method == "section"
        assert "c0" in ver.supporting_chunks

    def test_text_match_verifies(self):
        chunks = _make_chunks(2, section_base="99")
        # chunk has no section_number, but high textual overlap
        claim = ClaimExtractor().extract("food businesses must obtain a license.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert ver.method == "text"
        assert ver.confidence > 0

    def test_no_match_unverified(self):
        chunks = _make_chunks(2, section_base="99")
        claim = ClaimExtractor().extract("Section 999 is unverifiable law.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert not ver.verified
        assert ver.confidence == 0.0

    def test_empty_chunks(self):
        claim = ClaimExtractor().extract("Section 55 says X.")[0]
        ver = EvidenceVerifier().verify_claim(claim, [])
        assert not ver.verified
        assert ver.confidence == 0.0

    def test_verify_claims_returns_list(self):
        chunks = _make_chunks(3)
        claims = ClaimExtractor().extract("Section 55 requires X. Unrelated claim about unicorns.")
        vers = EvidenceVerifier().verify_claims(claims, chunks)
        assert len(vers) == len(claims)
        assert all(isinstance(v.verified, bool) for v in vers)


# --------------------------------------------------------------------------- #
# EvidenceVerifier — claim-level entailment gates (P1 item 5.5)
# --------------------------------------------------------------------------- #
class TestClaimEntailmentGates:
    """Fuzzy similarity (rapidfuzz >= 70) alone must not verify a claim.

    ``partial_ratio`` scores "sale is banned" highly against "sale may be
    permitted", and "Rs. 10,000" against "Rs. 50,000" — so ``verify_claim``
    now applies a polarity gate and a numeric-entailment gate before a
    textual match can count as evidence.
    """

    @staticmethod
    def _chunk(text, section_number=None, chunk_id="c0", score=0.9):
        return RetrievedChunk(
            chunk_id=chunk_id,
            score=score,
            text=text,
            section_number=section_number,
            document_title="FSS Act 2006",
            document_type="act",
            authority="FSSAI",
        )

    # -- Polarity gate ---------------------------------------------------- #
    def test_inverted_polarity_not_verified(self):
        """Claim prohibits, evidence permits -> not supported.

        The two sentences score 85 on ``partial_ratio`` (differing only in
        "shall not" vs "may"), so this is exactly the case raw fuzzy
        matching used to wave through.
        """
        chunks = [self._chunk("A licence may be sold to unqualified applicants.")]
        claim = ClaimExtractor().extract("A licence shall not be sold to unqualified applicants.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert not ver.verified
        assert ver.reason == "polarity_mismatch"

    def test_inverted_polarity_other_direction_not_verified(self):
        """Claim permits, evidence prohibits -> not supported."""
        chunks = [self._chunk("A licence shall not be sold to unqualified applicants.")]
        claim = ClaimExtractor().extract("A licence may be sold to unqualified applicants.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert not ver.verified
        assert ver.reason == "polarity_mismatch"

    def test_matching_polarity_still_verified(self):
        chunks = [self._chunk("A licence shall not be sold to unqualified applicants.")]
        claim = ClaimExtractor().extract("A licence shall not be sold to unqualified applicants.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert ver.reason == ""
        assert ver.method == "text"

    def test_mixed_polarity_chunk_supports_claim(self):
        """A chunk stating the rule *and* its exception is ambiguous, not contradictory."""
        chunks = [
            self._chunk(
                "A licence shall not be sold to unqualified applicants, but sale may be permitted with a waiver."
            )
        ]
        claim = ClaimExtractor().extract("A licence shall not be sold to unqualified applicants.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert ver.reason == ""

    def test_polarity_gate_ignores_neutral_evidence(self):
        """A chunk with no polarity language cannot contradict the claim."""
        chunks = [self._chunk("Section 55 of the FSS Act deals with licensing of food businesses.")]
        claim = ClaimExtractor().extract("A licence shall not be sold to unqualified applicants.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.reason != "polarity_mismatch"

    def test_polarity_mismatch_applies_on_cited_section_fallthrough(self):
        """Section cited but no stamp on any chunk -> fuzzy path, gate still applies."""
        chunks = [self._chunk("Under Section 55, the item may be sold freely to applicants.")]
        claim = ClaimExtractor().extract("Under Section 55, the item shall not be sold to applicants.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert not ver.verified
        assert ver.reason == "polarity_mismatch"

    def test_cited_section_fallthrough_scans_past_a_failing_best_match(self):
        """Both fuzzy paths walk the candidate list, not just the top hit.

        This branch (a claim citing a section no chunk is stamped with) used to
        gate only the single best fuzzy match, so a claim whose top match
        failed a gate was rejected even when a lower-ranked chunk stated the
        asserted figure.

        The decoy must outrank the gold on ``partial_ratio`` or the test
        proves nothing: with both chunks near-verbatim the gold wins the
        ranking outright and the old best-only code reaches the same verdict.
        Here the decoy is a near-verbatim mismatch (Rs. 10,000 -> fails the
        numeric gate) while the gold is reworded enough to rank second but
        still states the asserted Rs. 50,000.
        """
        chunks = [
            self._chunk(
                "Under Section 55, a penalty of Rs. 10,000 may be imposed on the operator.",
                chunk_id="decoy",
            ),
            self._chunk(
                "Where an offence under Section 55 is committed, a monetary penalty "
                "of Rs. 50,000 may be imposed.",
                chunk_id="gold",
            ),
        ]
        claim = ClaimExtractor().extract(
            "Under Section 55, a penalty of Rs. 50,000 may be imposed on the operator.",
        )[0]
        assert claim.section_numbers, "fixture must exercise the cited-section branch"

        # The decoy must be the best fuzzy match, else this does not test the
        # scan. Assert it rather than trusting the wording above.
        ranked = sorted(
            chunks,
            key=lambda c: fuzz.partial_ratio(claim.text, c.text),
            reverse=True,
        )
        assert ranked[0].chunk_id == "decoy", "fixture no longer exercises the fallthrough"

        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert ver.method == "text"
        assert ver.supporting_chunks == ["gold"]
        assert ver.reason == ""

    # -- Polarity regex coverage ------------------------------------------ #
    @pytest.mark.parametrize(
        "text",
        [
            "A licence shall not be sold to unqualified applicants.",
            "The substance shall not be used in food.",
            "The item shall not be imported without approval.",
            "No person shall sell the item under Section 55.",
            "Sale of the item is prohibited under Section 55.",
        ],
    )
    def test_prohibition_regex_recognises_statutory_phrasing(self, text):
        """``shall not be <verb>`` is the dominant statutory prohibition form.

        The previous pattern could never match it, which would have made
        the polarity gate silently blind to most real prohibitions.
        """
        assert _PROHIBITION_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "A licence may be sold to unqualified applicants.",
            "The item may be used in food.",
            "Sale is permitted under Section 55.",
        ],
    )
    def test_prohibition_regex_does_not_match_permissions(self, text):
        assert not _PROHIBITION_RE.search(text)

    # -- Numeric entailment gate ------------------------------------------ #
    def test_hallucinated_amount_not_verified(self):
        """Claim asserts Rs. 50,000, evidence says Rs. 10,000 -> not supported."""
        chunks = [self._chunk("A penalty of Rs. 10,000 may be imposed on unlicensed operators.")]
        claim = ClaimExtractor().extract("A penalty of Rs. 50,000 may be imposed on unlicensed operators.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert not ver.verified
        assert ver.reason == "numeric_mismatch"

    def test_hallucinated_percent_not_verified(self):
        chunks = [self._chunk("The inspector may reject up to 5% of the consignment.")]
        claim = ClaimExtractor().extract("The inspector may reject up to 95% of the consignment.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert not ver.verified
        assert ver.reason == "numeric_mismatch"

    def test_matching_amount_still_verified(self):
        chunks = [self._chunk("A penalty of Rs. 10,000 may be imposed on unlicensed operators.")]
        claim = ClaimExtractor().extract("A penalty of Rs. 10,000 may be imposed on unlicensed operators.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert ver.reason == ""

    def test_amount_currency_spelling_does_not_matter(self):
        """Rs./INR spelling is presentation; the figure is what must match."""
        chunks = [self._chunk("A penalty of INR 10,000 may be imposed on unlicensed operators.")]
        claim = ClaimExtractor().extract("A penalty of Rs. 10,000 may be imposed on unlicensed operators.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert ver.reason == ""

    def test_second_chunk_supplies_missing_figure(self):
        """A gate failure on the best chunk does not veto a later, valid one."""
        chunks = [
            self._chunk("A penalty of Rs. 10,000 may be imposed on unlicensed operators.", chunk_id="c0"),
            self._chunk("A penalty of Rs. 50,000 may be imposed on unlicensed operators.", chunk_id="c1"),
        ]
        claim = ClaimExtractor().extract("A penalty of Rs. 50,000 may be imposed on unlicensed operators.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert "c1" in ver.supporting_chunks

    def test_numeric_gate_skipped_when_claim_has_no_figures(self):
        chunks = [self._chunk("Food businesses must obtain a licence before operating.")]
        claim = ClaimExtractor().extract("Food businesses must obtain a licence before operating.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert ver.reason == ""

    def test_multi_figure_claim_requires_all_figures(self):
        """One grounded and one invented figure is still a hallucination."""
        chunks = [self._chunk("A penalty of Rs. 10,000 or 100% of turnover may be imposed.")]
        claim = ClaimExtractor().extract("A penalty of Rs. 10,000 or 500% of turnover may be imposed.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert not ver.verified
        assert ver.reason == "numeric_mismatch"

    # -- Regression: existing paths unchanged ----------------------------- #
    def test_section_match_unaffected_by_gates(self):
        """An exact section stamp is not fuzzy-matched; the gates do not apply."""
        chunks = [self._chunk("Section 55 text.", section_number="55")]
        claim = ClaimExtractor().extract("Section 55 requires a license.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        assert ver.verified
        assert ver.method == "section"
        assert ver.confidence == 0.85
        assert ver.reason == ""

    def test_asserted_numerics_helper(self):
        assert EvidenceVerifier._asserted_numerics("Rs. 1,000 and 50% and INR 200") == {"1000", "50", "200"}
        assert EvidenceVerifier._asserted_numerics("no figures here") == set()


# --------------------------------------------------------------------------- #
# CitationValidator
# --------------------------------------------------------------------------- #
class TestCitationValidator:
    def test_all_valid(self):
        chunks = _make_chunks(2)
        cits = [
            Citation(
                chunk_id="c0",
                section_number="55",
                document_title="FSS Act",
                document_type="act",
                authority="FSSAI",
                url=None,
                snippet="t",
                confidence=0.85,
            ),
            Citation(
                chunk_id="c1",
                section_number=None,
                document_title="FSS Act",
                document_type="act",
                authority="FSSAI",
                url=None,
                snippet="t",
                confidence=0.85,
            ),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert len(result.valid) == 2
        assert len(result.invalid) == 0
        assert result.score > 0.9

    def test_invalid_citation(self):
        chunks = _make_chunks(1)
        cits = [
            Citation(
                chunk_id="fake",
                section_number="99",
                document_title="Fake",
                document_type="act",
                authority="FSSAI",
                url=None,
                snippet="t",
                confidence=0.5,
            ),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert len(result.invalid) == 1
        assert result.score < 0.5

    def test_section_mismatch(self):
        chunks = [RetrievedChunk(chunk_id="c0", score=0.9, text="text", section_number="55")]
        cits = [
            Citation(
                chunk_id="c0",
                section_number="56",
                document_title="FSS Act",
                document_type="act",
                authority="FSSAI",
                url=None,
                snippet="t",
                confidence=0.85,
            ),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert len(result.section_mismatches) == 1
        assert len(result.valid) == 0

    def test_no_citations(self):
        result = CitationValidator().validate([], [])
        assert result.score == 0.0
        assert len(result.valid) == 0

    def test_detail_recorded(self):
        chunks = _make_chunks(1)
        cits = [
            Citation(
                chunk_id="c0",
                section_number="55",
                document_title="FSS Act",
                document_type="act",
                authority="FSSAI",
                url=None,
                snippet="t",
                confidence=0.85,
            ),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert result.detail[0]["status"] == "valid"


# --------------------------------------------------------------------------- #
# GroundednessScorer
# --------------------------------------------------------------------------- #
class TestGroundednessScorer:
    def test_full_grounding(self):
        chunks = _make_chunks(2, section_base="55")
        claim = ClaimExtractor().extract("Section 55 requires a license.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        score = GroundednessScorer().score([ver])
        assert score.score > 0.3  # claim weight * section confidence
        assert score.claim_support_ratio == 1.0

    def test_no_claims_flagged(self):
        """No claims extracted from a non-empty response is a red flag.

        P1-5 fix: the empty-claims loophole closed — claim_ratio is now
        0.50 (was 1.0) so a vacuous answer cannot score maximally on the
        claim axis.  The combined score with neutral citations is 0.70,
        which stays above the 0.50 hallucination threshold (a response
        that says nothing extractable is not a hallucination).
        """
        score = GroundednessScorer().score([])
        assert score.claim_support_ratio == 0.50  # no claims = flagged, not neutral
        assert score.score == 0.70  # 0.6*0.50 + 0.4*1.0
        assert score.detail.get("empty_claims") is True

    def test_all_unverified(self):
        claim = ClaimExtractor().extract("Section 999 is law.")[0]
        chunks = _make_chunks(2, section_base="55")
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        score = GroundednessScorer().score([ver])
        assert score.score < 0.5  # 0.6*0 + 0.4*1.0 (neutral citations) = 0.4
        assert score.claim_support_ratio == 0.0

    def test_with_citation_result(self):
        from app.rag.verification.citation_validator import CitationValidationResult

        chunks = _make_chunks(1)
        claim = ClaimExtractor().extract("Section 55 requires a license.")[0]
        ver = EvidenceVerifier().verify_claim(claim, chunks)
        cit_result = CitationValidationResult()
        cit_result.detail = [{"score": 0.85}]
        cit_result.score = 0.85
        score = GroundednessScorer().score([ver], citation_result=cit_result)
        assert score.citation_validity_ratio == 0.85
        assert score.score > 0.3


# --------------------------------------------------------------------------- #
# HallucinationDetector
# --------------------------------------------------------------------------- #
class TestHallucinationDetector:
    def test_grounded_response_no_hallucination(self):
        chunks = _make_chunks(2, section_base="55")
        response = "Section 55 requires a food business license. [1]"
        cits = [
            Citation(
                chunk_id="c0",
                section_number="55",
                document_title="FSS Act",
                document_type="act",
                authority="FSSAI",
                url=None,
                snippet="t",
                confidence=0.85,
            ),
        ]
        report = HallucinationDetector().detect(response, chunks, citations=cits)
        assert not report.detected
        assert report.groundedness_score > 0.3
        assert len(report.verified_claims) >= 1

    def test_hallucinated_response_detected(self):
        chunks = _make_chunks(2, section_base="55")
        response = "Section 999 imposes a penalty of 10000 gold coins. [1]"
        report = HallucinationDetector().detect(response, chunks)
        assert report.detected
        assert report.groundedness_score < 0.5
        assert len(report.hallucinated_claims) >= 1

    def test_empty_response_flags(self):
        report = HallucinationDetector().detect("", [])
        assert report.detected
        assert report.groundedness_score == 0.0

    def test_empty_chunks_flags(self):
        report = HallucinationDetector().detect("Some text", [])
        assert report.detected

    def test_no_citations_still_detects_via_claims(self):
        chunks = _make_chunks(3, section_base="55")
        response = "Section 55 requires a license. Section 56 governs penalties."
        report = HallucinationDetector().detect(response, chunks)
        assert len(report.claims) >= 1
        assert len(report.verified_claims) >= 1

    def test_report_detail(self):
        chunks = _make_chunks(2, section_base="55")
        response = "Section 55 requires a license."
        report = HallucinationDetector().detect(response, chunks)
        assert "claim_count" in report.detail
        assert report.detail["claim_count"] >= 1

    def test_stub_llm_not_used_by_default(self):
        """No API key => LLM double-check skipped, pure evidence-based."""
        chunks = _make_chunks(2, section_base="55")
        response = "Section 55 requires a license."
        report = HallucinationDetector().detect(response, chunks)
        # GroundedLLMClient defaults to stub mode => use_llm is False
        assert report.llm_verified is False
