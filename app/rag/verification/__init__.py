"""RAG verification sub-package — Phase 3 deliverable (Hallucination Detection).

Builds the claim-extraction + evidence-verification pipeline that verifies
LLM responses against retrieved chunks.  Phase 2's ``ResponseSanitizer``
provides a heuristic foundation; Phase 3 lifts this into a dedicated,
standalone verification layer with claim-level granularity.

Components:
    ``ClaimExtractor``      — extract factual claims from LLM responses (regex/sentence-based)
    ``EvidenceVerifier``    — verify each claim against retrieved chunks (rapidfuzz)
    ``CitationValidator``   — validate citations against retrieved chunk metadata
    ``GroundednessScore``   — 0-1 groundedness scoring dataclass + helpers
    ``HallucinationDetector`` — orchestrator: claims → evidence → citations → report

Reuses:
- rapidfuzz ``partial_ratio`` (installed — pattern from ``app/search/indexer.py``)
- ``GroundedLLMClient`` stub fallback pattern (Phase 2)
- ``compute_hash`` / ``log_audit`` hash-chained audit (R0)
- ``score_field`` confidence pattern from ``app/metadata_extractor/confidence.py``
"""

from app.rag.verification.claim_extractor import ClaimExtractor, ExtractedClaim
from app.rag.verification.citation_validator import (
    CitationValidationResult,
    CitationValidator,
)
from app.rag.verification.evidence_verifier import (
    EvidenceVerification,
    EvidenceVerifier,
)
from app.rag.verification.hallucination_detector import (
    HallucinationDetector,
    HallucinationReport,
)
from app.rag.verification.scorer import GroundednessScore, GroundednessScorer
from app.rag.verification.token_counter import TokenCounter, TokenUsage

__all__ = [
    "ClaimExtractor",
    "ExtractedClaim",
    "EvidenceVerifier",
    "EvidenceVerification",
    "CitationValidator",
    "CitationValidationResult",
    "GroundednessScore",
    "GroundednessScorer",
    "TokenCounter",
    "TokenUsage",
    "HallucinationDetector",
    "HallucinationReport",
]
