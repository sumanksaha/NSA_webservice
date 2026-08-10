"""Evidence verifier — check whether extracted claims are supported by
retrieved chunks.

Uses ``rapidfuzz`` (already installed — pattern from
``app/rag/retrieval/sparse_retriever.py`` and ``app/search/indexer.py``)
to compute textual overlap between a claim and each chunk.  Section-number
matching provides a deterministic, high-confidence signal: if a claim cites
"Section 55" and any retrieved chunk carries ``section_number == "55"``,
that claim is verified.

This replaces Phase 2's heuristic claim-flagging in ``ResponseSanitizer``
with a claim-level evidence check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.rag.retrieval.result import RetrievedChunk
from app.rag.verification.claim_extractor import ExtractedClaim

logger = logging.getLogger(__name__)

#: Minimum fuzzy similarity (0–100) for a chunk to count as "evidence"
#: for a claim that has no section number to match on.
_SIMILARITY_THRESHOLD = 70

#: Confidence boost for claims whose section number matches a chunk exactly.
_SECTION_MATCH_CONFIDENCE = 0.85
#: Confidence for claims verified only by textual overlap.
_TEXT_MATCH_CONFIDENCE = 0.70
#: Default confidence when a claim is supported by general context.
_GENERAL_SUPPORT_CONFIDENCE = 0.55
#: Confidence when a claim cannot be verified at all.
_UNVERIFIED_CONFIDENCE = 0.0


@dataclass
class EvidenceVerification:
    """Result of verifying a single claim against chunks.

    Attributes:
        verified: True if at least one chunk supports the claim.
        confidence: 0.0–1.0 confidence that the claim is grounded.
        supporting_chunks: Chunk IDs that provided evidence.
        method: How the claim was verified
            (``"section"`` | ``"text"`` | ``"none"``).
        evidence_snippet: The best-matching chunk text (truncated).
    """

    verified: bool = False
    confidence: float = 0.0
    supporting_chunks: list[str] = field(default_factory=list)
    method: str = "none"
    evidence_snippet: str = ""


class EvidenceVerifier:
    """Verify extracted claims against retrieved chunks.

    Args:
        similarity_threshold: rapidfuzz ``partial_ratio`` threshold
            (0–100) below which a chunk is not considered supporting
            evidence for a claim without a section match.
    """

    def __init__(
        self, similarity_threshold: int = _SIMILARITY_THRESHOLD
    ) -> None:
        self.similarity_threshold = similarity_threshold

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def verify_claim(
        self, claim: ExtractedClaim, chunks: list[RetrievedChunk]
    ) -> EvidenceVerification:
        """Verify a single :class:`ExtractedClaim` against *chunks*."""
        if not chunks:
            return EvidenceVerification(
                verified=False, confidence=_UNVERIFIED_CONFIDENCE
            )

        # 1. Section-number match — highest confidence.
        if claim.section_numbers:
            matched = self._match_sections(claim.section_numbers, chunks)
            if matched:
                best = matched[0]
                return EvidenceVerification(
                    verified=True,
                    confidence=_SECTION_MATCH_CONFIDENCE,
                    supporting_chunks=[best.chunk_id],
                    method="section",
                    evidence_snippet=self._snippet(best.text),
                )
            # Sections cited but none match => unverified (hallucination signal).
            return EvidenceVerification(
                verified=False,
                confidence=_UNVERIFIED_CONFIDENCE,
                supporting_chunks=[],
                method="none",
                evidence_snippet="",
            )

        # 2. Textual overlap via rapidfuzz.
        best_score, best_chunk = self._best_text_match(claim.text, chunks)
        if best_score >= self.similarity_threshold:
            return EvidenceVerification(
                verified=True,
                confidence=_TEXT_MATCH_CONFIDENCE * (best_score / 100.0),
                supporting_chunks=[best_chunk.chunk_id],
                method="text",
                evidence_snippet=self._snippet(best_chunk.text),
            )

        # 3. General support — claim references an authority that appears
        #    in any chunk, even if the exact text doesn't overlap.
        if self._authority_support(claim, chunks):
            return EvidenceVerification(
                verified=True,
                confidence=_GENERAL_SUPPORT_CONFIDENCE,
                supporting_chunks=[c.chunk_id for c in chunks],
                method="general",
                evidence_snippet="",
            )

        return EvidenceVerification(
            verified=False, confidence=_UNVERIFIED_CONFIDENCE
        )

    def verify_claims(
        self, claims: list[ExtractedClaim], chunks: list[RetrievedChunk]
    ) -> list[EvidenceVerification]:
        """Verify a list of claims, returning one result per claim."""
        return [self.verify_claim(c, chunks) for c in claims]

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _match_sections(
        section_numbers: list[str], chunks: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        """Return chunks whose ``section_number`` matches any claim section."""
        section_set = set(section_numbers)
        matched = [
            c for c in chunks
            if c.section_number and c.section_number in section_set
        ]
        # Sort by retrieval score descending so the best chunk is first.
        return sorted(matched, key=lambda c: c.score, reverse=True)

    def _best_text_match(
        self, claim_text: str, chunks: list[RetrievedChunk]
    ) -> tuple[float, RetrievedChunk | None]:
        """Find the chunk with the highest ``partial_ratio`` to *claim_text*."""
        best_score = 0.0
        best_chunk: RetrievedChunk | None = None
        for chunk in chunks:
            score = fuzz.partial_ratio(claim_text, chunk.text)
            if score > best_score:
                best_score = score
                best_chunk = chunk
        return best_score, best_chunk

    @staticmethod
    def _authority_support(
        claim: ExtractedClaim, chunks: list[RetrievedChunk]
    ) -> bool:
        """Check if claim's authority entities appear in any chunk text."""
        authorities = claim.entities.get("authority", [])
        if not authorities:
            return False
        chunk_texts = " ".join(c.text for c in chunks).lower()
        return any(auth.lower() in chunk_texts for auth in authorities)

    @staticmethod
    def _snippet(text: str, limit: int = 120) -> str:
        if len(text) <= limit:
            return text
        return text[:limit].rsplit(" ", 1)[0] + "..."
