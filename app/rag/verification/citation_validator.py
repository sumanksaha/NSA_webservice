"""Citation validator — verify that every citation in an LLM response maps
to a real retrieved chunk and that the cited section numbers are consistent.

This is the Phase 3 standalone validation layer.  Phase 2's
``ResponseSanitizer`` also separates valid/invalid citations, but the
``CitationValidator`` here adds *section-number consistency* checking and
produces a scored, serializable result for the hallucination report.

Reuses:
- ``Citation`` / ``RetrievedChunk`` dataclasses from ``app/rag/retrieval/result``
- ``score_field`` method-based scoring pattern from
  ``app/metadata_extractor/confidence.py``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.rag.retrieval.result import Citation, RetrievedChunk

logger = logging.getLogger(__name__)

#: Confidence for a citation whose chunk_id is in the retrieved set.
_VALID_CITATION_CONFIDENCE = 1.0

#: Confidence for a citation that maps to a chunk but whose section
#: number doesn't match — it's likely the right document but the wrong
#: sub-reference.
_SECTION_MISMATCH_CONFIDENCE = 0.55

#: Confidence for a citation that doesn't map to any retrieved chunk.
_INVALID_CITATION_CONFIDENCE = 0.0


@dataclass
class CitationValidationResult:
    """Aggregated result of validating a set of citations.

    Attributes:
        valid: Citations that map to a retrieved chunk.
        invalid: Citations with no matching chunk.
        section_mismatches: Citations whose section_number differs from
            the chunk's own ``section_number``.
        score: Overall validation score (valid + partial / total).
        detail: Per-citation validation detail dicts.
    """

    valid: list[Citation] = field(default_factory=list)
    invalid: list[Citation] = field(default_factory=list)
    section_mismatches: list[tuple[Citation, str]] = field(default_factory=list)
    score: float = 0.0
    detail: list[dict[str, Any]] = field(default_factory=list)


class CitationValidator:
    """Validate response citations against retrieved chunks.

    A citation is **valid** when its ``chunk_id`` appears among the
    retrieved ``chunks``.  When the ``chunk_id`` exists but the
    citation's ``section_number`` differs from the chunk's own
    ``section_number``, it is recorded as a section mismatch (partial
    validity — the document is right but the reference may be off).
    """

    def validate(
        self,
        citations: list[Citation],
        chunks: list[RetrievedChunk],
    ) -> CitationValidationResult:
        """Validate *citations* against *chunks*.

        Args:
            citations: Citations extracted from the LLM response.
            chunks: The retrieved chunks (ground-truth for validation).

        Returns:
            A :class:`CitationValidationResult`.
        """
        chunk_by_id = {c.chunk_id: c for c in chunks}
        total = len(citations)

        result = CitationValidationResult()
        if total == 0:
            return result

        score_sum = 0.0
        for cit in citations:
            chunk = chunk_by_id.get(cit.chunk_id)

            if chunk is None:
                result.invalid.append(cit)
                result.detail.append(
                    {"chunk_id": cit.chunk_id, "status": "invalid",
                     "score": _INVALID_CITATION_CONFIDENCE}
                )
                score_sum += _INVALID_CITATION_CONFIDENCE
                continue

            # Section-number consistency check.
            if (
                cit.section_number
                and chunk.section_number
                and cit.section_number != chunk.section_number
            ):
                result.section_mismatches.append((cit, chunk.section_number))
                result.detail.append(
                    {"chunk_id": cit.chunk_id, "status": "section_mismatch",
                     "expected": chunk.section_number,
                     "actual": cit.section_number,
                     "score": _SECTION_MISMATCH_CONFIDENCE}
                )
                score_sum += _SECTION_MISMATCH_CONFIDENCE
                continue

            result.valid.append(cit)
            result.detail.append(
                {"chunk_id": cit.chunk_id, "status": "valid",
                 "score": _VALID_CITATION_CONFIDENCE}
            )
            score_sum += _VALID_CITATION_CONFIDENCE

        result.score = round(score_sum / total, 4)
        return result
