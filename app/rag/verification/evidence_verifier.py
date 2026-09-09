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
import re
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.rag.retrieval.result import RetrievedChunk
from app.rag.verification.claim_extractor import (
    _AMOUNT_RE,
    _PERCENT_RE,
    ExtractedClaim,
)

logger = logging.getLogger(__name__)

#: Prohibition language ("no person shall sell", "shall not be added").
_PROHIBITION_RE = re.compile(
    r"\b(?:no person shall|shall not(?:\s+\w+){0,3}\b(?:be\s+)?(?:added|sold|used|manufactured|imported)|prohibited)\b",
    re.IGNORECASE,
)
#: Permission language ("may be sold/used/…", "permitted", "allowed").
_PERMISSION_RE = re.compile(
    r"\b(?:may\s+(?:be\s+)?(?:added|sold|used|manufactured|imported|permitted)|shall be permitted|is permitted|are permitted|allowed)\b",
    re.IGNORECASE,
)

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
class Contradiction:
    """One detected conflict between two retrieved chunks.

    Attributes:
        a, b: The conflicting chunks.
        kind: ``"numeric"`` (different amounts for the same provision)
            or ``"prohibition"`` (conflicting prohibition/permission).
        values: The conflicting values (numeric kind).
    """

    a: RetrievedChunk
    b: RetrievedChunk
    kind: str
    values: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_a": self.a.chunk_id,
            "chunk_b": self.b.chunk_id,
            "kind": self.kind,
            "values": list(self.values),
        }


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
            # No chunk carries the cited section stamp — but the evidence may
            # still *contain* the cited provision text.  Fall through to the
            # textual-overlap check before declaring the claim unverified
            # (a bare "sections cited but no stamp => hallucinated" verdict
            # flagged claims whose evidence simply lacked metadata).
            best_score, best_chunk = self._best_text_match(claim.text, chunks)
            if best_chunk is not None and best_score >= self.similarity_threshold:
                return EvidenceVerification(
                    verified=True,
                    confidence=_TEXT_MATCH_CONFIDENCE * (best_score / 100.0),
                    supporting_chunks=[best_chunk.chunk_id],
                    method="text",
                    evidence_snippet=self._snippet(best_chunk.text),
                )
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

    # ------------------------------------------------------------------ #
    # Contradiction detection (V2 plan item 16)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _numeric_values(text: str) -> list[str]:
        """Extract monetary/percentage/amount values from *text*."""
        return [
            f"{m.group(1)}{m.group(2)}"
            for m in _AMOUNT_RE.finditer(text)
        ] + [m.group(1) for m in _PERCENT_RE.finditer(text)]

    def find_contradictions(
        self,
        chunks: list[RetrievedChunk],
    ) -> list[Contradiction]:
        """Find conflicting evidence among *chunks* (live item-16 signal).

        Deterministic heuristics, no LLM:

        1. **numeric** — two chunks assert different monetary/percentage
           amounts for the same provision (same section stamp, or same
           query-relevant topic).  This is the "Penalty = Rs. X vs Rs. Y"
           case the V2 proposal calls out.
        2. **prohibition** — one chunk prohibits ("shall not"/"no person
           shall") while another permits ("may"/"shall be permitted")
           the same thing on the same section stamp.

        Chunks on different sections/acts never conflict — differing
        amounts across *different* provisions are expected, not
        contradictions.
        """
        if len(chunks) < 2:
            return []
        conflicts: list[Contradiction] = []
        seen_pairs: set[tuple[str, str]] = set()
        for i in range(len(chunks)):
            for j in range(i + 1, len(chunks)):
                a, b = chunks[i], chunks[j]
                pair = tuple(sorted((a.chunk_id, b.chunk_id)))
                if pair in seen_pairs or a.chunk_id == b.chunk_id:
                    continue
                seen_pairs.add(pair)
                # Numeric conflicts only count within the same provision.
                same_provision = bool(
                    a.section_number
                    and b.section_number
                    and a.section_number == b.section_number
                )
                a_vals = self._numeric_values(a.text)
                b_vals = self._numeric_values(b.text)
                if same_provision and a_vals and b_vals and set(a_vals).isdisjoint(b_vals):
                    conflicts.append(
                        Contradiction(
                            a=a, b=b, kind="numeric", values=sorted(set(a_vals) | set(b_vals))[:4]
                        )
                    )
                    continue
                if not same_provision:
                    # Prohibition conflicts also require the same provision.
                    continue
                a_prohibits = _PROHIBITION_RE.search(a.text)
                b_permits = _PERMISSION_RE.search(b.text)
                if a_prohibits and b_permits:
                    conflicts.append(Contradiction(a=a, b=b, kind="prohibition", values=[]))
        return conflicts

    @staticmethod
    def _snippet(text: str, limit: int = 120) -> str:
        if len(text) <= limit:
            return text
        return text[:limit].rsplit(" ", 1)[0] + "..."
