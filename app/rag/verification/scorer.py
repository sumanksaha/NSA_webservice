"""Groundedness scoring — 0–1 score for how much of a response is grounded
in retrieved evidence.

Provides the :class:`GroundednessScore` dataclass and a pure-function
:class:`GroundednessScorer` that aggregates claim-verification + citation
results into a single score.  This is the numeric backbone for both
Phase 3 (hallucination detection) and Phase 4 (evaluation metrics).

Reuses the method-based scoring pattern from
``app/metadata_extractor/confidence.py`` — scores are weighted blends
with deterministic, documented defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.rag.verification.citation_validator import CitationValidationResult
from app.rag.verification.evidence_verifier import EvidenceVerification

logger = logging.getLogger(__name__)

#: Weight given to claim-level verification vs. citation-level validation.
_CLAIM_WEIGHT = 0.6
_CITATION_WEIGHT = 0.4


@dataclass
class GroundednessScore:
    """A groundedness measurement.

    Attributes:
        score: Overall groundedness 0.0–1.0.
        claim_support_ratio: Fraction of claims verified by evidence.
        citation_validity_ratio: Fraction of citations backed by chunks.
        claim_verifications: Per-claim verification results.
        citation_result: The citation validation result (or None).
        detail: Free-form breakdown for debugging.
    """

    score: float = 0.0
    claim_support_ratio: float = 0.0
    citation_validity_ratio: float = 0.0
    claim_verifications: list[EvidenceVerification] = field(default_factory=list)
    citation_result: CitationValidationResult | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "claim_support_ratio": self.claim_support_ratio,
            "citation_validity_ratio": self.citation_validity_ratio,
            "claim_count": len(self.claim_verifications),
            "verified_claim_count": sum(
                1 for v in self.claim_verifications if v.verified
            ),
            "detail": self.detail,
        }


class GroundednessScorer:
    """Compute a groundedness score from verification results.

    The score is a weighted blend:

        groundedness = 0.6 × claim_support_ratio + 0.4 × citation_validity_ratio

    where:
    - ``claim_support_ratio`` = (# claims verified by evidence) / (# claims)
    - ``citation_validity_ratio`` = the ``CitationValidationResult.score``
      (valid + partial / total), or 1.0 if there were no citations
      (no citations is neutral, not penalised).
    """

    def __init__(
        self, claim_weight: float = _CLAIM_WEIGHT, citation_weight: float = _CITATION_WEIGHT
    ) -> None:
        self.claim_weight = claim_weight
        self.citation_weight = citation_weight

    def score(
        self,
        claim_verifications: list[EvidenceVerification],
        citation_result: CitationValidationResult | None = None,
    ) -> GroundednessScore:
        """Compute a groundedness score from verification results.

        Args:
            claim_verifications: Per-claim evidence verification results.
            citation_result: Optional citation validation result.

        Returns:
            A :class:`GroundednessScore`.
        """
        # Claim support ratio.
        if claim_verifications:
            verified = sum(1 for v in claim_verifications if v.verified)
            claim_ratio = verified / len(claim_verifications)
        else:
            # No claims extracted — treat as neutral (1.0) so that
            # responses with no extractable claims aren't penalised
            # purely for being short.
            verified = 0
            claim_ratio = 1.0

        # Citation validity ratio.
        citation_ratio = citation_result.score if citation_result is not None and citation_result.detail else 1.0

        score = (
            self.claim_weight * claim_ratio
            + self.citation_weight * citation_ratio
        )
        score = round(min(1.0, max(0.0, score)), 4)

        return GroundednessScore(
            score=score,
            claim_support_ratio=round(claim_ratio, 4),
            citation_validity_ratio=round(citation_ratio, 4),
            claim_verifications=claim_verifications,
            citation_result=citation_result,
            detail={
                "verified_claims": verified,
                "total_claims": len(claim_verifications),
                "claim_weight": self.claim_weight,
                "citation_weight": self.citation_weight,
            },
        )
