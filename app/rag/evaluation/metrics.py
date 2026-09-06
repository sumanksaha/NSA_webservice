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
import re
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

from rapidfuzz import fuzz

from app.rag.retrieval.result import RetrievedChunk
from app.rag.verification.claim_extractor import ClaimExtractor
from app.rag.verification.evidence_verifier import EvidenceVerifier

logger = logging.getLogger(__name__)

#: rapidfuzz threshold (0-100) for a chunk to count as "relevant" to a query.
_RELEVANCE_SIMILARITY = 50

#: Standard English stop words + single-letter noise tokens.
_STOP_WORDS: frozenset[str] = frozenset({
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "need",
    "dare",
    "ought",
    "used",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "among",
    "over",
    "under",
    "again",
    "further",
    "then",
    "once",
    "here",
    "there",
    "when",
    "where",
    "why",
    "how",
    "all",
    "any",
    "both",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "nor",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "just",
    "and",
    "but",
    "if",
    "or",
    "because",
    "until",
    "while",
    "this",
    "these",
    "those",
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "his",
    "its",
    "our",
    "their",
    "what",
    "which",
    "who",
    "whom",
    "am",
    "s",
    "t",
    "re",
    "ll",
    "ve",
    "m",
    "y",
    "d",
    "w",
    "h",
    "o",
    "u",
    "j",
    "n",
    "v",
    "e",
    "l",
    "r",
    "c",
    "f",
    "g",
    "k",
    "p",
    "q",
    "x",
    "z",
    "b",
})

#: Minimum length for a token to be considered a meaningful key term.
_MIN_TERM_LENGTH = 3

#: Default weight for query coverage in AnswerRelevanceMetric (0-1).
_DEFAULT_COVERAGE_WEIGHT = 0.3


def _tokenize(text: str) -> list[str]:
    """Extract lowercase alphanumeric tokens from text.

    Ponytail: stdlib regex over re.split. Skips digits-only tokens.
    """
    return [t for t in re.findall(r"\b[a-zA-Z][a-zA-Z0-9]*\b", text.lower()) if t]


def _extract_key_terms(query: str) -> list[str]:
    """Extract meaningful terms from query: drop stop words, min length 3.

    Returns the list in first-seen order for stable test assertions.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for tok in _tokenize(query):
        if tok in _STOP_WORDS or len(tok) < _MIN_TERM_LENGTH or tok in seen:
            continue
        seen.add(tok)
        terms.append(tok)
    return terms


def _compute_term_coverage(query: str, answer: str) -> tuple[float, dict[str, Any]]:
    """Fraction of key query terms found in answer (exact OR substring match).

    Returns ``(coverage_ratio, detail_dict)``. Coverage is 1.0 when the query
    has no key terms (degenerate but well-defined).
    """
    key_terms = _extract_key_terms(query)
    if not key_terms:
        return 1.0, {"covered": 0, "total": 0, "terms": [], "covered_terms": []}

    answer_tokens = _tokenize(answer)
    answer_token_set = set(answer_tokens)
    covered_terms: list[str] = []
    for term in key_terms:
        if term in answer_token_set:
            covered_terms.append(term)
        elif any(term in at for at in answer_token_set):
            # ponytail: substring containment (e.g. query "fssai" matches answer "fssai-related")
            covered_terms.append(term)

    coverage = len(covered_terms) / len(key_terms)
    return coverage, {
        "covered": len(covered_terms),
        "total": len(key_terms),
        "terms": key_terms,
        "covered_terms": covered_terms,
    }


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

    def __init__(
        self, claim_extractor: ClaimExtractor | None = None, evidence_verifier: EvidenceVerifier | None = None
    ) -> None:
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
                "faithfulness",
                0.0,
                "No chunks retrieved — cannot verify faithfulness.",
            )
        claims = self.claim_extractor.extract(answer)
        if not claims:
            # No claims to verify — treat as fully faithful (nothing asserted).
            return EvalScore(
                "faithfulness",
                1.0,
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
            f"{sum(1 for v in verifications if v.verified)}/{len(verifications)} claims verified by evidence.",
            detail={
                "claim_count": len(claims),
                "verified_count": sum(1 for v in verifications if v.verified),
                "avg_confidence": score,
            },
        )


class AnswerRelevanceMetric:
    """Is the answer relevant to the query?

    Combines two signals:
      1. **Text similarity** — rapidfuzz ``WRatio`` between answer and
         the reference (expected answer if given, else the query).
         Catches fuzzy paraphrase matches.
      2. **Query coverage** — fraction of key query terms (stop words
         + sub-3-char tokens removed) found in the answer via exact or
         substring match.  Catches topical drift where the answer talks
         about something different.

    The combined score is ``coverage_weight * coverage
    + (1 - coverage_weight) * text_similarity`` (both 0-1).
    Use ``query_coverage_weight=0.0`` to restore the original
    text-only behavior.
    """

    def __init__(self, query_coverage_weight: float = _DEFAULT_COVERAGE_WEIGHT) -> None:
        if not 0.0 <= query_coverage_weight <= 1.0:
            raise ValueError(f"query_coverage_weight must be in [0, 1], got {query_coverage_weight!r}")
        self.query_coverage_weight = query_coverage_weight
        self.text_similarity_weight = 1.0 - query_coverage_weight

    def compute(
        self,
        answer: str,
        query: str,
        expected_answer: str | None = None,
    ) -> EvalScore:
        if not answer or not query:
            return EvalScore(
                "answer_relevance",
                0.0,
                "Empty answer or query.",
            )
        # Text similarity (existing behavior, unchanged)
        reference = expected_answer or query
        raw = fuzz.WRatio(answer.lower(), reference.lower())
        text_score = round(raw / 100.0, 4)

        # Query coverage: always measured against the query (not the
        # expected answer) — coverage is about answering WHAT was asked.
        coverage, cov_detail = _compute_term_coverage(query, answer)

        combined = self.text_similarity_weight * text_score + self.query_coverage_weight * coverage
        score = round(min(1.0, max(0.0, combined)), 4)

        return EvalScore(
            "answer_relevance",
            score,
            f"Text overlap {text_score:.2f}, query coverage {coverage:.2f}.",
            detail={
                "text_similarity": text_score,
                "query_coverage": coverage,
                "similarity_raw": raw,
                "expected": bool(expected_answer),
                "coverage_detail": cov_detail,
                "query_coverage_weight": self.query_coverage_weight,
            },
        )


class ContextPrecisionMetric:
    """Are the retrieved chunks relevant to the query?

    Uses **cosine similarity** between the query embedding and each
    chunk's embedding to determine relevance.  This catches semantic
    matches that rapidfuzz string overlap misses (e.g., a query about
    "food safety violations" matching a chunk titled "FSS Act
    contraventions").

    Falls back to the original rapidfuzz ``partial_ratio`` + token
    overlap when the embedding service is unavailable (e.g., no
    network during unit tests).

    Attributes:
        similarity_threshold: Cosine similarity score (0–1) above
            which a chunk is counted as relevant.  Default 0.65.
        use_embedding: When ``True`` (default), use the embedding
            service.  When ``False``, fall back to rapidfuzz only.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.65,
        use_embedding: bool = True,
    ) -> None:
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError(f"similarity_threshold must be in [0, 1], got {similarity_threshold!r}")
        self.similarity_threshold = similarity_threshold
        self.use_embedding = use_embedding

    @staticmethod
    def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = sum(a * a for a in v1) ** 0.5
        norm2 = sum(a * a for a in v2) ** 0.5
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        return dot / (norm1 * norm2)

    def _embed_and_score(self, query: str, chunks: list[RetrievedChunk]) -> tuple[int, int]:
        """Return ``(relevant_count, total_count)`` using embeddings.

        Early-exits the similarity loop once the running sum of
        similarities drops below ``threshold * remaining_chunks``
        (i.e. even perfect matches for all remaining chunks cannot
        lift the score above the threshold).  This avoids computing
        embeddings for every chunk when the answer is already clear.
        """
        from app.rag.embedding_service import EmbeddingService

        embedder = EmbeddingService()
        query_vec = embedder.embed(query)
        if not query_vec:
            return 0, len(chunks)
        threshold = self.similarity_threshold
        remaining = len(chunks)
        relevant = 0
        for chunk in chunks:
            chunk_vec = embedder.embed(chunk.text)
            sim = self._cosine_similarity(query_vec, chunk_vec)
            if sim >= threshold:
                relevant += 1
            remaining -= 1
            # Early-exit: even if ALL remaining chunks scored 1.0,
            # the mean could not reach the threshold.
            if remaining > 0 and (relevant + remaining) / len(chunks) < threshold:
                break
        return relevant, len(chunks)

    def _fuzz_and_score(self, query: str, chunks: list[RetrievedChunk]) -> tuple[int, int]:
        """Fallback: rapidfuzz partial_ratio + token overlap."""
        relevant = 0
        for chunk in chunks:
            sim = fuzz.partial_ratio(query.lower(), chunk.text.lower())
            query_tokens = set(query.lower().split())
            chunk_tokens = set(chunk.text.lower().split())
            overlap = bool(query_tokens & chunk_tokens)
            if sim >= self.similarity_threshold * 100 or overlap:
                relevant += 1
        return relevant, len(chunks)

    def compute(
        self,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> EvalScore:
        if not chunks:
            return EvalScore(
                "context_precision",
                0.0,
                "No chunks retrieved.",
                detail={"relevant": 0, "total": 0},
            )
        try:
            if self.use_embedding:
                relevant, total = self._embed_and_score(query, chunks)
                method = "embedding"
                details = {
                    "relevant": relevant,
                    "total": total,
                    "method": "embedding",
                    "similarity_threshold": self.similarity_threshold,
                }
            else:
                relevant, total = self._fuzz_and_score(query, chunks)
                method = "rapidfuzz"
                details = {
                    "relevant": relevant,
                    "total": total,
                    "method": "rapidfuzz",
                    "similarity_threshold": self.similarity_threshold * 100,
                }
        except Exception as exc:
            # Fallback to rapidfuzz if embedding service is down
            # (e.g., network in unit tests).
            logger.warning(
                "ContextPrecisionMetric: embedding failed (%s), falling back to rapidfuzz",
                exc,
            )
            relevant, total = self._fuzz_and_score(query, chunks)
            method = "rapidfuzz_fallback"
            details = {
                "relevant": relevant,
                "total": total,
                "method": "rapidfuzz_fallback",
                "similarity_threshold": self.similarity_threshold * 100,
            }

        score = round(relevant / total, 4) if total else 0.0
        return EvalScore(
            "context_precision",
            score,
            f"{relevant}/{total} retrieved chunks are relevant to the query ({method}).",
            detail=details,
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
                "context_recall",
                1.0,
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
                "citation_recall",
                1.0,
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
                "groundedness",
                0.0,
                "No chunks retrieved — response is ungrounded.",
            )
        claims = self.claim_extractor.extract(answer)
        if not claims:
            return EvalScore(
                "groundedness",
                1.0,
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


class SelfConsistencyMetric:
    """Is the generation stable across multiple runs?

    Runs the pipeline N times for the same query and measures pairwise
    similarity between all generated answers using rapidfuzz
    ``token_sort_ratio`` (order-insensitive).  A high score means the
    LLM produces consistent answers — low variance = high reliability.

    This is a **reference-free** quality metric: it needs no ground
    truth, only the pipeline.  Use it in production to detect
    unreliable model behavior.

    Args:
        pipeline_fn: Callable taking ``query`` and returning a dict
            with an ``answer`` key.  If ``None``, returns a
            no-pipeline score of 0.0.
        n_samples: Number of total generations to compare (default 3,
            minimum 2).  The first sample is typically the "official"
            answer being evaluated.
        similarity_fn: Similarity function (default
            ``rapidfuzz.fuzz.token_sort_ratio``).
    """

    def __init__(
        self,
        pipeline_fn: Callable[[str], dict[str, Any]] | None = None,
        n_samples: int = 3,
        adaptive_threshold: bool = True,
        stability_threshold: float = 0.95,
        stability_std_max: float = 0.05,
    ) -> None:
        self.pipeline_fn = pipeline_fn
        self.n_samples = max(2, n_samples)
        self.adaptive_threshold = adaptive_threshold
        self.stability_threshold = stability_threshold
        self.stability_std_max = stability_std_max

    def compute(self, answer: str, query: str = "") -> EvalScore:
        if self.pipeline_fn is None:
            return EvalScore(
                "self_consistency",
                0.0,
                "No pipeline provided — cannot measure consistency.",
                detail={"n_samples": self.n_samples},
            )
        if not answer or not query:
            return EvalScore(
                "self_consistency",
                0.0,
                "Empty answer or query.",
                detail={"n_samples": self.n_samples},
            )

        # Run the pipeline (n_samples - 1) additional times.
        answers: list[str] = [answer]
        # Progressive deepening: threshold increases each iteration
        base_threshold = self.stability_threshold
        std_max = self.stability_std_max

        for i in range(self.n_samples - 1):
            try:
                result = self.pipeline_fn(query)
                generated = result.get("answer", "")
                if generated:
                    answers.append(generated)
            except Exception as exc:
                logger.warning("SelfConsistencyMetric: pipeline run failed: %s", exc)
                continue

            # Early termination: check stability after each new sample
            if len(answers) >= 2:
                sims = self._pairwise_similarities(answers)
                if sims:
                    mean_s = sum(sims) / len(sims)
                    std_s = (sum((s - mean_s) ** 2 for s in sims) / len(sims)) ** 0.5
                    # If mean is high AND std is low, we have stable consistency
                    if mean_s >= base_threshold and std_s <= std_max:
                        # Already stable — no need for more samples
                        break
                    # Adaptive threshold: raise target as we gather more samples
                    if self.adaptive_threshold:
                        base_threshold = min(1.0, base_threshold + 0.02 * (i + 1))

        if len(answers) < 2:
            return EvalScore(
                "self_consistency",
                0.0,
                "Only one successful generation — cannot measure consistency.",
                detail={"n_samples": len(answers)},
            )

        # Final pairwise computation
        similarities = self._pairwise_similarities(answers)
        if not similarities:
            return EvalScore(
                "self_consistency",
                0.0,
                "Could not compute pairwise similarities.",
                detail={"n_samples": len(answers)},
            )

        mean_sim = sum(similarities) / len(similarities)
        std_sim = (sum((s - mean_sim) ** 2 for s in similarities) / len(similarities)) ** 0.5

        score = round(mean_sim, 4)
        return EvalScore(
            "self_consistency",
            score,
            f"Mean pairwise similarity: {mean_sim:.2f} (std {std_sim:.2f}) over {len(answers)} samples.",
            detail={
                "n_samples": len(answers),
                "mean_similarity": round(mean_sim, 4),
                "std_similarity": round(std_sim, 4),
                "pairwise_similarities": [round(s, 4) for s in similarities],
                "answers": answers,
                "early_terminated": len(answers) < self.n_samples,
            },
        )

    @staticmethod
    def _pairwise_similarities(answers: list[str]) -> list[float]:
        """Compute all pairwise token_sort_ratio similarities."""
        results: list[float] = []
        for i in range(len(answers)):
            for j in range(i + 1, len(answers)):
                sim = fuzz.token_sort_ratio(answers[i].lower(), answers[j].lower())
                results.append(sim / 100.0)
        return results


# ---------------------------------------------------------------------- #
# Priority 5: Weighted Composite Score
# ---------------------------------------------------------------------- #


_DEFAULT_WEIGHTS = {
    "faithfulness": 0.20,
    "answer_relevance": 0.15,
    "context_precision": 0.15,
    "context_recall": 0.15,
    "citation_recall": 0.15,
    "groundedness": 0.20,
    "self_consistency": 0.10,
}

_FAILURE_CATEGORIES = {
    "low_coverage": lambda s, d: (
        s.name == "answer_relevance" and (d.get("covered", 0) / max(d.get("total", 1), 1) < 0.5) if d else False
    ),
    "poor_relevance": lambda s, d: s.name == "answer_relevance" and s.score < 0.5,
    "poor_context": lambda s, d: s.name == "context_precision" and s.score < 0.5,
    "low_citation": lambda s, d: s.name == "citation_recall" and s.score < 0.5,
    "poor_grounding": lambda s, d: s.name == "groundedness" and s.score < 0.5,
    "low_faithfulness": lambda s, d: s.name == "faithfulness" and s.score < 0.5,
}


class WeightedCompositeScore:
    """Compute a weighted composite quality score across metrics.

    Priority 5: combines faithfulness, relevance, context precision,
    citation recall, grounding, and self-consistency with configurable
    weights. Also categorizes failures for debugging (low coverage,
    poor relevance, hallucination, poor grounding, low citation,
    poor context).
    """

    # Mutable class-level defaults are safe here: never mutated at runtime
    # (only read via cls.DEFAULT_WEIGHTS / cls.FAILURE_CATEGORIES).
    DEFAULT_WEIGHTS = _DEFAULT_WEIGHTS  # type: ignore[misc]
    FAILURE_CATEGORIES = _FAILURE_CATEGORIES  # type: ignore[misc]

    @classmethod
    def compute(cls, scores: list[EvalScore], weights: dict[str, float] | None = None) -> dict[str, Any]:
        """Compute composite score and failure categories.

        Returns ``{"composite_quality_score": float, "normalized_weights": dict,
        "failure_categories": list, "failure_count": int}``.
        """
        weights = weights or cls.DEFAULT_WEIGHTS
        total = sum(weights.values())
        normalized = {k: (v / total if total > 0 else 0.0) for k, v in weights.items() if v > 0}
        weighted_sum = sum(s.score * normalized.get(s.name, 0.0) for s in scores)
        composite = round(weighted_sum, 4)

        # Categorize failures for debugging
        failures: list[dict] = []
        for s in scores:
            for cat_name, check in cls.FAILURE_CATEGORIES.items():
                try:
                    if check(s, s.detail):
                        failures.append({"metric": s.name, "category": cat_name, "score": s.score})
                except Exception:
                    # If detail doesn't have expected keys, skip
                    pass
        return {
            "composite_quality_score": composite,
            "normalized_weights": normalized,
            "failure_categories": failures,
            "failure_count": len(failures),
        }


# ---------------------------------------------------------------------- #
# Priority 6: Edge Case Hardening (ContextPrecision + SelfConsistency)
# ---------------------------------------------------------------------- #


def handle_edge_case_empty_chunk(query: str, chunks: list[RetrievedChunk]) -> EvalScore:
    """Handle zero-chunk case gracefully for context precision."""
    return EvalScore(
        "context_precision",
        0.0,
        "No chunks retrieved — cannot assess precision.",
        detail={"relevant": 0, "total": 0, "edge_case": "zero_chunks"},
    )


def handle_edge_case_empty_answer(query: str, chunks: list[RetrievedChunk]) -> EvalScore:
    """Handle empty answer gracefully for self-consistency."""
    return EvalScore(
        "self_consistency",
        0.0,
        "Empty answer — cannot measure consistency.",
        detail={"edge_case": "empty_answer"},
    )
