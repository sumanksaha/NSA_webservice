"""RAGAS-style reference metrics (Phase 4, plan item 19).

Implements the six metric classes contractually imported by
``app.rag.evaluation.__init__`` and exercised by
``tests/test_eval_framework.py``:  faithfulness, answer relevance, context
precision/recall, citation recall, and groundedness.

All metrics are deterministic, rule-based reference implementations that
reuse the verification stack (``ClaimExtractor`` + ``EvidenceVerifier``)
and rapidfuzz token overlap — no LLM calls, suitable for CI.  They are
*reference* metrics: swap in LLM-judge implementations behind the same
``compute()`` interface when higher fidelity is needed.

Each ``compute()`` returns an :class:`EvalScore` with ``name``, ``score``
(0.0–1.0), a human-readable ``explanation``, and a ``detail`` dict.
"""

from __future__ import annotations

from app.rag.evaluation.metrics import EvalScore
from app.rag.evaluation.textmatch import (
    chunk_id as _chunk_id,
)
from app.rag.evaluation.textmatch import (
    chunk_text as _chunk_text,
)
from app.rag.evaluation.textmatch import (
    content_tokens as _content_tokens,
)
from app.rag.evaluation.textmatch import (
    token_coverage as _token_coverage,
)
from app.rag.retrieval.result import RetrievedChunk
from app.rag.verification.claim_extractor import ClaimExtractor
from app.rag.verification.evidence_verifier import EvidenceVerifier


def _as_chunks(chunks: list) -> list[RetrievedChunk]:
    """Normalize a mixed list of chunk dicts/objects to ``RetrievedChunk``.

    Lenient to missing ``chunk_id`` / ``score`` / ``text`` — eval-time chunk
    dicts are not guaranteed to be full serializations.
    """
    defaults = {"chunk_id": "", "score": 0.0, "text": ""}
    return [
        c if isinstance(c, RetrievedChunk) else RetrievedChunk.from_dict({**defaults, **c})
        for c in chunks
        if isinstance(c, (RetrievedChunk, dict))
    ]


def _score(name: str, value: float, explanation: str, detail: dict) -> EvalScore:
    return EvalScore(
        name=name,
        score=round(float(value), 4),
        explanation=explanation,
        detail=detail,
    )


# --------------------------------------------------------------------------- #
# Faithfulness
# --------------------------------------------------------------------------- #
class FaithfulnessMetric:
    """Fraction of answer claims entailed by the retrieved evidence.

    Uses the rule-based :class:`ClaimExtractor` for claim segmentation and
    the :class:`EvidenceVerifier` (section match, then textual overlap) for
    entailment.  Returns 1.0 for answers with no verifiable claims
    (trivially faithful) and 0.0 when there is no evidence to support
    anything.
    """

    name = "faithfulness"

    def compute(
        self,
        answer: str,
        chunks: list[RetrievedChunk | dict],
        query: str = "",
    ) -> EvalScore:
        del query  # kept for interface symmetry with LLM-judge implementations
        if not chunks:
            return _score(self.name, 0.0, "No evidence retrieved; faithfulness undefined", {})

        claims = ClaimExtractor().extract(answer or "")
        if not claims:
            return _score(self.name, 1.0, "No factual claims to verify", {"claims": 0})

        verifier = EvidenceVerifier()
        chunk_objs = _as_chunks(chunks)
        verifications = verifier.verify_claims(claims, chunk_objs)
        supported = sum(1 for v in verifications if v.verified)
        ratio = supported / len(claims)

        methods: dict[str, int] = {}
        for v in verifications:
            methods[v.method] = methods.get(v.method, 0) + 1

        return _score(
            self.name,
            ratio,
            f"{supported}/{len(claims)} claims supported by retrieved evidence",
            {"claims": len(claims), "supported": supported, "methods": methods},
        )


# --------------------------------------------------------------------------- #
# Answer relevance
# --------------------------------------------------------------------------- #
class AnswerRelevanceMetric:
    """How well the answer addresses the query (or a reference answer).

    If an ``expected`` reference answer is supplied, scores token overlap
    (Jaccard) against it; otherwise scores fuzzy coverage of the query's
    content tokens in the answer.
    """

    name = "answer_relevance"

    def compute(self, answer: str, query: str, expected: str | None = None) -> EvalScore:
        if not (answer or "").strip():
            return _score(self.name, 0.0, "Empty answer", {})

        if expected:
            answer_tokens = set(_content_tokens(answer))
            expected_tokens = set(_content_tokens(expected))
            union = answer_tokens | expected_tokens
            overlap = len(answer_tokens & expected_tokens) / len(union) if union else 0.0
            return _score(
                self.name,
                overlap,
                f"Jaccard overlap with reference answer: {overlap:.2f}",
                {"mode": "expected", "overlap_tokens": len(answer_tokens & expected_tokens)},
            )

        needles = _content_tokens(query or "")
        if not needles:
            return _score(self.name, 0.0, "Query has no content tokens", {"mode": "query"})

        coverage = _token_coverage(needles, answer)
        return _score(
            self.name,
            coverage,
            f"{coverage:.0%} of query content tokens addressed in the answer",
            {"mode": "query", "query_tokens": needles},
        )


# --------------------------------------------------------------------------- #
# Context precision / recall
# --------------------------------------------------------------------------- #
class ContextPrecisionMetric:
    """Fraction of retrieved chunks that are relevant to the query."""

    name = "context_precision"

    #: A chunk counts as relevant when at least this fraction of the
    #: query's content tokens appear (fuzzily) in its text.
    RELEVANCE_THRESHOLD = 0.5

    def compute(self, query: str, chunks: list[RetrievedChunk | dict]) -> EvalScore:
        if not chunks:
            return _score(self.name, 0.0, "No chunks retrieved", {"total": 0})

        needles = _content_tokens(query or "")
        relevant = 0
        for chunk in chunks:
            text = _chunk_text(chunk)
            if needles and _token_coverage(needles, text) >= self.RELEVANCE_THRESHOLD:
                relevant += 1

        precision = relevant / len(chunks)
        return _score(
            self.name,
            precision,
            f"{relevant}/{len(chunks)} retrieved chunks relevant to the query",
            {"total": len(chunks), "relevant": relevant},
        )


class ContextRecallMetric:
    """Fraction of the expected (gold) chunk ids that were retrieved."""

    name = "context_recall"

    def compute(
        self,
        relevant_ids: list[str],
        chunks: list[RetrievedChunk | dict],
    ) -> EvalScore:
        if not relevant_ids:
            return _score(self.name, 1.0, "No expected chunks to recall", {})

        retrieved = {_chunk_id(c) for c in chunks}
        expected = set(relevant_ids)
        recalled = retrieved & expected
        recall = len(recalled) / len(expected)

        return _score(
            self.name,
            recall,
            f"{len(recalled)}/{len(expected)} expected chunks retrieved",
            {"missing": sorted(expected - retrieved)},
        )


# --------------------------------------------------------------------------- #
# Citation recall
# --------------------------------------------------------------------------- #
class CitationRecallMetric:
    """Fraction of the answer's citations that point at retrieved chunks.

    Cited-but-not-retrieved ids are phantom citations.  An answer with no
    citations scores 1.0 (nothing cited, nothing wrong) — citation
    *presence* is enforced elsewhere (citation-quality gate).
    """

    name = "citation_recall"

    def compute(
        self,
        cited_ids: list[str],
        chunks: list[RetrievedChunk | dict],
    ) -> EvalScore:
        if not cited_ids:
            return _score(self.name, 1.0, "No citations to check", {})

        retrieved = {_chunk_id(c) for c in chunks}
        valid = [cid for cid in cited_ids if cid in retrieved]
        recall = len(valid) / len(cited_ids)
        phantoms = [cid for cid in cited_ids if cid not in retrieved]

        return _score(
            self.name,
            recall,
            f"{len(valid)}/{len(cited_ids)} citations point at retrieved chunks",
            {"phantom_citations": phantoms},
        )


# --------------------------------------------------------------------------- #
# Groundedness
# --------------------------------------------------------------------------- #
class GroundednessMetric:
    """Mean per-claim grounding confidence of the answer against evidence.

    Differs from :class:`FaithfulnessMetric` in the aggregation: groundedness
    averages the verifier's per-claim *confidence* (so a claim verified only
    by weak textual overlap contributes less than a section-stamped match),
    while faithfulness is the binary supported ratio.
    """

    name = "groundedness"

    #: Confidence at or above which a claim counts as grounded.
    GROUNDED_THRESHOLD = 0.5

    def compute(
        self,
        answer: str,
        chunks: list[RetrievedChunk | dict],
        query: str = "",
    ) -> EvalScore:
        del query  # interface symmetry
        if not chunks:
            return _score(self.name, 0.0, "No evidence retrieved; groundedness undefined", {})

        claims = ClaimExtractor().extract(answer or "")
        if not claims:
            return _score(self.name, 1.0, "No factual claims to ground", {"claims": 0})

        verifier = EvidenceVerifier()
        chunk_objs = _as_chunks(chunks)
        verifications = verifier.verify_claims(claims, chunk_objs)
        grounded = sum(1 for v in verifications if v.confidence >= self.GROUNDED_THRESHOLD)
        mean_conf = sum(v.confidence for v in verifications) / len(claims)

        return _score(
            self.name,
            grounded / len(claims),
            f"{grounded}/{len(claims)} claims grounded (mean confidence {mean_conf:.2f})",
            {"claims": len(claims), "grounded": grounded, "mean_confidence": round(mean_conf, 4)},
        )
