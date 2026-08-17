"""Response sanitiser — validate LLM output and flag hallucinated citations.

Follows the method-based confidence scoring pattern from
``app/metadata_extractor/confidence.py``: citation validity and
response coherence are scored, and invalid citations (those that do
not map to any retrieved chunk) are flagged.

The sanitiser also computes a basic groundedness score (0.0-1.0) as the
fraction of cited sources that are backed by retrieved evidence.
Full hallucination detection (claim extraction + evidence verification)
is Phase 3 — this module provides the Phase 2 foundation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.rag.retrieval.result import Citation, RetrievedChunk

logger = logging.getLogger(__name__)

#: Minimum groundedness score to consider the response grounded.
#: Below this, ``hallucination_detected`` is set to ``True``.
_GROUNDEDNESS_THRESHOLD = 0.50


@dataclass
class SanitizedResponse:
    """Result of sanitising an LLM response.

    Attributes:
        response_text: The (unchanged) response text.
        valid_citations: Citations that map to retrieved chunks.
        invalid_citations: Citations that could not be verified.
        groundedness_score: Fraction of cited sources backed by evidence (0-1).
        hallucination_detected: True if any invalid citation or low groundedness.
        hallucinated_claims: Heuristically flagged unverifiable claims.
        confidence: Overall confidence (0.0-1.0).
    """

    response_text: str = ""
    valid_citations: list[Citation] = field(default_factory=list)
    invalid_citations: list[Citation] = field(default_factory=list)
    groundedness_score: float = 0.0
    hallucination_detected: bool = False
    hallucinated_claims: list[str] = field(default_factory=list)
    confidence: float = 0.0


class ResponseSanitizer:
    """Validate an LLM response against retrieved evidence.

    Args:
        groundedness_threshold: Minimum groundedness score (0-1) before
            hallucination is flagged.
    """

    def __init__(self, groundedness_threshold: float = _GROUNDEDNESS_THRESHOLD) -> None:
        self.groundedness_threshold = groundedness_threshold

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def sanitize(
        self,
        response_text: str,
        citations: list[Citation],
        chunks: list[RetrievedChunk],
    ) -> SanitizedResponse:
        """Validate the response and separate valid / invalid citations.

        A citation is **valid** if its ``chunk_id`` exists among the
        retrieved ``chunks``.  Invalid citations are flagged but the
        response text is left unchanged (removing markers could corrupt
        readability).

        Args:
            response_text: The raw LLM response.
            citations: Citations extracted by :class:`CitationTracker`.
            chunks: The retrieved chunks (ground-truth for validation).

        Returns:
            A :class:`SanitizedResponse` with validated citations and
            a groundedness score.
        """
        valid_chunk_ids = {c.chunk_id for c in chunks}

        valid: list[Citation] = []
        invalid: list[Citation] = []

        for cit in citations:
            if cit.chunk_id in valid_chunk_ids:
                valid.append(cit)
            else:
                invalid.append(cit)

        # Groundedness — fraction of cited sources backed by evidence.
        total = len(citations)
        groundedness = len(valid) / total if total > 0 else 0.0

        # Hallucination flag — any invalid citation or low groundedness.
        hallucination = len(invalid) > 0 or groundedness < self.groundedness_threshold

        # Simple claim flagging for obviously unverifiable statements.
        hallucinated_claims = self._flag_unverifiable_claims(
            response_text, valid, chunks
        )

        # Overall confidence — weighted blend.
        confidence = self._compute_confidence(groundedness, valid, chunks)

        return SanitizedResponse(
            response_text=response_text,
            valid_citations=valid,
            invalid_citations=invalid,
            groundedness_score=round(groundedness, 4),
            hallucination_detected=hallucination,
            hallucinated_claims=hallucinated_claims,
            confidence=round(confidence, 4),
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _flag_unverifiable_claims(
        response_text: str,
        valid_citations: list[Citation],
        chunks: list[RetrievedChunk],
    ) -> list[str]:
        """Heuristically flag claims with no matching evidence.

        Phase 3 will replace this with LLM-based claim extraction +
        evidence verification.  For now, we flag section numbers
        mentioned in the response but not in any retrieved chunk.
        """
        claims: list[str] = []

        mentioned_sections = set(re.findall(r"\b[Ss]ection\s+(\d+)", response_text))
        retrieved_sections = {
            c.section_number for c in chunks if c.section_number
        }
        for sec in mentioned_sections - retrieved_sections:
            claims.append(
                f"Claims about Section {sec} not found in retrieved documents"
            )

        return claims

    @staticmethod
    def _compute_confidence(
        groundedness: float,
        valid_citations: list[Citation],
        chunks: list[RetrievedChunk],
    ) -> float:
        """Compute overall confidence.

        Mirrors ``score_field`` from
        ``app/metadata_extractor/confidence.py``:
        - Regex match (section match) -> 0.85
        - NER / keyword match -> 0.70
        - Heuristic -> 0.55
        - Default -> 0.30
        """
        if not valid_citations:
            return 0.30

        avg_cit_conf = sum(c.confidence for c in valid_citations) / len(valid_citations)
        avg_chunk_score = sum(c.score for c in chunks) / len(chunks) if chunks else 0.0

        # Weighted blend: grounding (0.5) + citation quality (0.3) + chunk relevance (0.2)
        confidence = (
            groundedness * 0.5 + avg_cit_conf * 0.3 + avg_chunk_score * 0.2
        )
        return min(1.0, max(0.0, confidence))
