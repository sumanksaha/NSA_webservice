"""Tests for Phase 3 RAG verification — ClaimExtractor, EvidenceVerifier,
CitationValidator, GroundednessScorer, and HallucinationDetector.

No Qdrant/network required — all verification runs against in-memory
``RetrievedChunk`` objects with rapidfuzz text matching.
"""

from __future__ import annotations

from app.rag.retrieval.result import Citation, RetrievedChunk
from app.rag.verification import (
    CitationValidator,
    ClaimExtractor,
    EvidenceVerifier,
    GroundednessScorer,
    HallucinationDetector,
)


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
        claims = ClaimExtractor().extract(
            "Section 55 requires X. Unrelated claim about unicorns."
        )
        vers = EvidenceVerifier().verify_claims(claims, chunks)
        assert len(vers) == len(claims)
        assert all(isinstance(v.verified, bool) for v in vers)


# --------------------------------------------------------------------------- #
# CitationValidator
# --------------------------------------------------------------------------- #
class TestCitationValidator:
    def test_all_valid(self):
        chunks = _make_chunks(2)
        cits = [
            Citation(chunk_id="c0", section_number="55", document_title="FSS Act",
                     document_type="act", authority="FSSAI", url=None, snippet="t", confidence=0.85),
            Citation(chunk_id="c1", section_number=None, document_title="FSS Act",
                     document_type="act", authority="FSSAI", url=None, snippet="t", confidence=0.85),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert len(result.valid) == 2
        assert len(result.invalid) == 0
        assert result.score > 0.9

    def test_invalid_citation(self):
        chunks = _make_chunks(1)
        cits = [
            Citation(chunk_id="fake", section_number="99", document_title="Fake",
                     document_type="act", authority="FSSAI", url=None, snippet="t", confidence=0.5),
        ]
        result = CitationValidator().validate(cits, chunks)
        assert len(result.invalid) == 1
        assert result.score < 0.5

    def test_section_mismatch(self):
        chunks = [RetrievedChunk(chunk_id="c0", score=0.9, text="text", section_number="55")]
        cits = [
            Citation(chunk_id="c0", section_number="56", document_title="FSS Act",
                     document_type="act", authority="FSSAI", url=None, snippet="t", confidence=0.85),
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
            Citation(chunk_id="c0", section_number="55", document_title="FSS Act",
                     document_type="act", authority="FSSAI", url=None, snippet="t", confidence=0.85),
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

    def test_no_claims_neutral(self):
        score = GroundednessScorer().score([])
        assert score.claim_support_ratio == 1.0  # no claims = neutral

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
        cits = [Citation(chunk_id="c0", section_number="55", document_title="FSS Act",
                         document_type="act", authority="FSSAI", url=None, snippet="t", confidence=0.85)]
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
