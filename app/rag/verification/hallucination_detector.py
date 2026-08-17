"""Hallucination detector — orchestrates claim extraction, evidence
verification, and citation validation to produce a full hallucination
report for an LLM response.

Pipeline:
    1. Extract claims            — :class:`ClaimExtractor`
    2. Verify each claim          — :class:`EvidenceVerifier` (rapidfuzz)
    3. Validate citations         — :class:`CitationValidator`
    4. Score groundedness         — :class:`GroundednessScorer`
    5. (Optional) LLM-based double-check — :class:`GroundedLLMClient` stub fallback

The LLM-based check is a lightweight "factuality prompt" that asks a stub/local
LLM whether each unverified claim is supported by the provided context.  When
no API key is configured (the default in tests and dev), the LLM step is
skipped and the detector falls back to pure claim + citation evidence — so
hallucination detection degrades gracefully without network access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.rag.generation.llm_client import GroundedLLMClient
from app.rag.retrieval.result import Citation, RetrievedChunk
from app.rag.verification.citation_validator import CitationValidator
from app.rag.verification.claim_extractor import ClaimExtractor, ExtractedClaim
from app.rag.verification.evidence_verifier import EvidenceVerifier
from app.rag.verification.scorer import GroundednessScore, GroundednessScorer

logger = logging.getLogger(__name__)

#: Groundedness at or above this score is considered "not hallucinated".
_HALLUCINATION_THRESHOLD = 0.50

#: Minimum chunk-text length for the LLM factuality prompt context.
_LLM_CONTEXT_LIMIT = 2000


@dataclass
class HallucinationReport:
    """Full hallucination-detection report for one response.

    Attributes:
        detected: True if hallucination is likely (groundedness < threshold).
        groundedness_score: 0.0–1.0 (see :class:`GroundednessScore`).
        claims: All extracted claims.
        verified_claims: Claims backed by evidence.
        unverified_claims: Claims with no supporting evidence (candidates).
        hallucinated_claims: Text of claims deemed hallucinated.
        citation_result: Citation validation result.
        llm_verified: Whether the LLM-based double-check ran.
        confidence: Overall 0.0–1.0 confidence in the detection result.
    """

    detected: bool = False
    groundedness_score: float = 0.0
    claims: list[ExtractedClaim] = field(default_factory=list)
    verified_claims: list[ExtractedClaim] = field(default_factory=list)
    unverified_claims: list[ExtractedClaim] = field(default_factory=list)
    hallucinated_claims: list[str] = field(default_factory=list)
    citation_result: Any = None
    llm_verified: bool = False
    confidence: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


class HallucinationDetector:
    """Detect hallucinations in an LLM response against retrieved evidence.

    Args:
        claim_extractor: Claim extractor instance.
        evidence_verifier: Evidence verifier instance.
        citation_validator: Citation validator instance.
        scorer: Groundedness scorer instance.
        llm_client: Optional LLM client for claim-level factuality checking
            (stub mode by default — no API key required).
        groundedness_threshold: Minimum groundedness score to avoid
            flagging a hallucination.
        use_llm: Whether to attempt the LLM-based double-check.  Auto-disabled
            when no API key is available.
    """

    def __init__(
        self,
        claim_extractor: ClaimExtractor | None = None,
        evidence_verifier: EvidenceVerifier | None = None,
        citation_validator: CitationValidator | None = None,
        scorer: GroundednessScorer | None = None,
        llm_client: GroundedLLMClient | None = None,
        groundedness_threshold: float = _HALLUCINATION_THRESHOLD,
        use_llm: bool = True,
    ) -> None:
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.evidence_verifier = evidence_verifier or EvidenceVerifier()
        self.citation_validator = citation_validator or CitationValidator()
        self.scorer = scorer or GroundednessScorer()
        self.llm_client = llm_client or GroundedLLMClient()
        self.groundedness_threshold = groundedness_threshold
        # Only use the LLM double-check if we're not in stub mode.  In
        # stub mode the LLM returns canned text that can't actually verify
        # anything, so we skip it and rely on claim + citation evidence.
        self.use_llm = use_llm and self._llm_is_real()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def detect(
        self,
        response_text: str,
        chunks: list[RetrievedChunk],
        citations: list[Citation] | None = None,
    ) -> HallucinationReport:
        """Run hallucination detection on *response_text*.

        Args:
            response_text: The LLM's generated response.
            chunks: Retrieved chunks (ground-truth evidence).
            citations: Optional pre-extracted citations.  If ``None``,
                citation validation is skipped (only claim-level evidence
                is used).

        Returns:
            A :class:`HallucinationReport`.
        """
        if not response_text or not chunks:
            GroundednessScore(score=0.0)
            return HallucinationReport(
                detected=True,
                groundedness_score=0.0,
                claims=[],
                hallucinated_claims=(
                    [response_text] if response_text else []
                ),
                detail={"reason": "no_response_or_chunks"},
            )

        # 1. Extract claims.
        claims = self.claim_extractor.extract(response_text)

        # 2. Verify each claim.
        verifications = self.evidence_verifier.verify_claims(claims, chunks)

        # 3. Validate citations (if provided).
        citation_result = None
        if citations:
            citation_result = self.citation_validator.validate(citations, chunks)

        # 4. Score groundedness.
        grounding = self.scorer.score(verifications, citation_result)

        # 5. Partition verified / unverified claims.
        verified, unverified = self._partition(claims, verifications)

        # 6. LLM double-check on unverified claims (best-effort, stub-safe).
        llm_claims = self._llm_verify_claims(unverified, chunks) if self.use_llm else []

        hallucinated = [
            c.text for i, c in enumerate(unverified)
            if i not in {v for v in llm_claims}
        ] if self.use_llm else [c.text for c in unverified]

        report = HallucinationReport(
            detected=grounding.score < self.groundedness_threshold,
            groundedness_score=grounding.score,
            claims=claims,
            verified_claims=verified,
            unverified_claims=unverified,
            hallucinated_claims=hallucinated,
            citation_result=citation_result,
            llm_verified=self.use_llm,
            confidence=self._confidence(grounding, claims),
            detail={
                "claim_count": len(claims),
                "verified_claim_count": len(verified),
                "unverified_claim_count": len(unverified),
                "citation_validity_ratio": (
                    grounding.citation_validity_ratio
                    if citation_result is not None else None
                ),
                "claim_support_ratio": grounding.claim_support_ratio,
                "threshold": self.groundedness_threshold,
            },
        )
        return report

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _llm_is_real(self) -> bool:
        """True if the LLM client is not in stub mode."""
        return not getattr(self.llm_client, "_use_stub", True)

    def _partition(
        self,
        claims: list[ExtractedClaim],
        verifications: list,
    ) -> tuple[list[ExtractedClaim], list[ExtractedClaim]]:
        """Split claims into (verified, unverified) based on verifications."""
        verified: list[ExtractedClaim] = []
        unverified: list[ExtractedClaim] = []
        for claim, ver in zip(claims, verifications, strict=False):
            if ver.verified:
                verified.append(claim)
            else:
                unverified.append(claim)
        return verified, unverified

    def _llm_verify_claims(
        self,
        unverified: list[ExtractedClaim],
        chunks: list[RetrievedChunk],
    ) -> set[int]:
        """Ask the LLM which unverified claims are actually supported.

        Returns a set of indices (into *unverified*) that the LLM
        vouches for.  On any failure, returns an empty set so the
        detector falls back to pure evidence-based verification.
        """
        if not unverified:
            return set()

        context = " ".join(c.text for c in chunks)[:_LLM_CONTEXT_LIMIT]
        claims_text = "\n".join(
            f"{i+1}. {c.text}" for i, c in enumerate(unverified)
        )

        system_prompt = (
            "You are a legal factuality checker. For each numbered claim "
            "below, reply ONLY with the comma-separated list of numbers that "
            "are supported by the provided context. If none are supported, "
            "reply with 'none'."
        )
        user_prompt = (
            f"Context:\n{context}\n\nClaims to verify:\n{claims_text}\n\n"
            "Supported claim numbers:"
        )

        try:
            import re

            resp = self.llm_client.call(system_prompt, user_prompt)
            if not resp.success:
                logger.warning("LLM verification failed: %s", resp.error)
                return set()

            numbers = re.findall(r"\b(\d+)\b", resp.text)
            # Indices are 0-based; LLM returns 1-based.
            return {int(n) - 1 for n in numbers if int(n) - 1 < len(unverified)}
        except Exception as exc:
            logger.warning("LLM claim verification error: %s", exc)
            return set()

    @staticmethod
    def _confidence(
        grounding: GroundednessScore, claims: list[ExtractedClaim]
    ) -> float:
        """Overall confidence in the detection result.

        Higher when we have more claims to check (more signal) and a
        clear groundedness score (not hovering at the threshold).
        """
        if not claims:
            return 0.3  # low confidence — nothing to verify
        # Confidence scales with claim count (more claims = more signal),
        # capped at 0.4 + 0.6 for a clear separation from the threshold.
        signal = min(len(claims) / 5.0, 1.0)
        clarity = abs(grounding.score - _HALLUCINATION_THRESHOLD) * 2  # 0..1
        return round(0.4 * signal + 0.6 * clarity, 4)
