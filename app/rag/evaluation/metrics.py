"""RAG evaluation metrics — pure functions computing RAGAS-style scores.

Each metric is a stateless class with a ``compute(...)`` method returning
an :class:`EvalScore` (score 0–1, explanation, detail).  Metrics reuse
``rapidfuzz`` (installed — pattern from sparse retriever) and Phase 3's
``EvidenceVerifier`` for claim-level faithfulness.

No DB or network access required — all metrics operate on in-memory data
(query, answer, retrieved chunks, expected ground truth).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.rag.retrieval.result import RetrievedChunk
from app.rag.verification.claim_extractor import ClaimExtractor
from app.rag.verification.evidence_verifier import EvidenceVerifier

logger = logging.getLogger(__name__)

#: rapidfuzz threshold (0-100) for a chunk to count as "relevant" to a query.
_RELEVANCE_SIMILARITY = 50


@dataclass
class EvalScore:
    """A single metric score.

    Attributes:
        name: Metric name (e.g. ``"faithfulness"``).
        score: 0.0–1.0.
        explanation: Human-readable summary.
        detail: Optional extra dict.
    """

    name: str
    score: float
    explanation: str
    detail: dict[str, Any] = field(default_factory=dict)


class FaithfulnessMetric:
    """Does the answer align with the retrieved context?

    Computed as the average evidence-verification confidence across all
    claims extracted from the answer.  Reuses :class:`EvidenceVerifier`
    (Phase 3) for claim-to-chunk matching.
    """

    def __init__(self, claim_extractor: ClaimExtractor | None = None,
                 evidence_verifier: EvidenceVerifier | None = None) -> None:
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.evidence_verifier = evidence_verifier or EvidenceVerifier()

    def compute(
        self,
        answer: str,
        chunks: list[RetrievedChunk],
        query: str = "",
    ) -> EvalScore:
        if not chunks:
            return EvalScore(
                "faithfulness", 0.0,
                "No chunks retrieved — cannot verify faithfulness.",
            )
        claims = self.claim_extractor.extract(answer)
        if not claims:
            # No claims to verify — treat as fully faithful (nothing asserted).
            return EvalScore(
                "faithfulness", 1.0,
                "No claims extracted — trivially faithful.",
                detail={"claim_count": 0},
            )
        verifications = self.evidence_verifier.verify_claims(claims, chunks)
        confidences = [v.confidence for v in verifications]
        score = sum(confidences) / len(confidences) if confidences else 0.0
        score = round(min(1.0, max(0.0, score)), 4)
        return EvalScore(
            "faithfulness",
            score,
            f"{sum(1 for v in verifications if v.verified)}/{len(verifications)} "
            f"claims verified by evidence.",
            detail={
                "claim_count": len(claims),
                "verified_count": sum(1 for v in verifications if v.verified),
                "avg_confidence": score,
            },
        )


class AnswerRelevanceMetric:
    """Is the answer relevant to the query?

    Computed via rapidfuzz ``partial_ratio`` between the answer and the
    query (or expected answer if provided).  Higher textual overlap
    between the question and the response => more relevant.
    """

    def compute(
        self,
        answer: str,
        query: str,
        expected_answer: str | None = None,
    ) -> EvalScore:
        if not answer or not query:
            return EvalScore(
                "answer_relevance", 0.0,
                "Empty answer or query.",
            )
        # If we have ground-truth, score against that; otherwise query.
        reference = expected_answer or query
        raw = fuzz.WRatio(answer.lower(), reference.lower())
        score = round(raw / 100.0, 4)
        return EvalScore(
            "answer_relevance",
            score,
            f"Textual overlap with {reference[:50]!r}...",
            detail={"similarity_raw": raw, "expected": bool(expected_answer)},
        )


class ContextPrecisionMetric:
    """Are the retrieved chunks relevant to the query?

    Computed as the fraction of retrieved chunks whose text shares a
    query term or exceeds the rapidfuzz similarity threshold.
    """

    def __init__(self, threshold: int = _RELEVANCE_SIMILARITY) -> None:
        self.threshold = threshold

    def compute(
        self,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> EvalScore:
        if not chunks:
            return EvalScore(
                "context_precision", 0.0,
                "No chunks retrieved.",
                detail={"relevant": 0, "total": 0},
            )
        relevant = 0
        for chunk in chunks:
            sim = fuzz.partial_ratio(query.lower(), chunk.text.lower())
            # Also check if query terms appear in the chunk (token overlap).
            query_tokens = set(query.lower().split())
            chunk_tokens = set(chunk.text.lower().split())
            overlap = bool(query_tokens & chunk_tokens)
            if sim >= self.threshold or overlap:
                relevant += 1
        score = round(relevant / len(chunks), 4)
        return EvalScore(
            "context_precision",
            score,
            f"{relevant}/{len(chunks)} retrieved chunks are relevant to the query.",
            detail={"relevant": relevant, "total": len(chunks)},
        )


class ContextRecallMetric:
    """Did retrieval miss relevant chunks?

    Given a set of *relevant* chunk IDs (from ground-truth expected
    citations) and the *retrieved* chunk IDs, recall =
    |relevant ∩ retrieved| / |relevant|.
    """

    def compute(
        self,
        relevant_chunk_ids: list[str],
        retrieved_chunks: list[RetrievedChunk],
    ) -> EvalScore:
        if not relevant_chunk_ids:
            return EvalScore(
                "context_recall", 1.0,
                "No expected relevant chunks — trivially satisfied.",
                detail={"relevant": 0, "retrieved": 0},
            )
        relevant_set = set(relevant_chunk_ids)
        retrieved_set = {c.chunk_id for c in retrieved_chunks}
        hit = len(relevant_set & retrieved_set)
        score = round(hit / len(relevant_set), 4)
        return EvalScore(
            "context_recall",
            score,
            f"{hit}/{len(relevant_set)} expected chunks were retrieved.",
            detail={"hit": hit, "expected": len(relevant_set), "retrieved": len(retrieved_set)},
        )


class CitationRecallMetric:
    """Are all cited chunks actually supported by retrieval?

    Of the chunks cited in the response, what fraction are present in the
    retrieved set (i.e. were available as evidence)?
    """

    def compute(
        self,
        cited_chunk_ids: list[str],
        retrieved_chunks: list[RetrievedChunk],
    ) -> EvalScore:
        if not cited_chunk_ids:
            return EvalScore(
                "citation_recall", 1.0,
                "No citations in response — trivially satisfied.",
                detail={"cited": 0, "retrieved": 0},
            )
        cited_set = set(cited_chunk_ids)
        retrieved_set = {c.chunk_id for c in retrieved_chunks}
        hit = len(cited_set & retrieved_set)
        score = round(hit / len(cited_set), 4)
        return EvalScore(
            "citation_recall",
            score,
            f"{hit}/{len(cited_set)} cited chunks were in the retrieved set.",
            detail={"hit": hit, "cited": len(cited_set), "retrieved": len(retrieved_set)},
        )


class GroundednessMetric:
    """Is the response grounded in its sources?

    Delegates to the :class:`EvidenceVerifier` + :class:`ClaimExtractor`
    to compute the fraction of claims supported by retrieved evidence.
    """

    def __init__(
        self,
        claim_extractor: ClaimExtractor | None = None,
        evidence_verifier: EvidenceVerifier | None = None,
    ) -> None:
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.evidence_verifier = evidence_verifier or EvidenceVerifier()

    def compute(
        self,
        answer: str,
        chunks: list[RetrievedChunk],
    ) -> EvalScore:
        if not chunks:
            return EvalScore(
                "groundedness", 0.0,
                "No chunks retrieved — response is ungrounded.",
            )
        claims = self.claim_extractor.extract(answer)
        if not claims:
            return EvalScore(
                "groundedness", 1.0,
                "No claims to verify — trivially grounded.",
                detail={"claim_count": 0},
            )
        verifications = self.evidence_verifier.verify_claims(claims, chunks)
        verified = sum(1 for v in verifications if v.verified)
        score = round(verified / len(verifications), 4) if verifications else 0.0
        return EvalScore(
            "groundedness",
            score,
            f"{verified}/{len(verifications)} claims are grounded in evidence.",
            detail={"claim_count": len(claims), "verified_count": verified},
        )
