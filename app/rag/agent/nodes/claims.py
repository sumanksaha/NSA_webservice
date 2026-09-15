"""Claim-level verification shared by the linear and DAG paths (item 15).

``generate_node`` and ``synthesize_node`` both measure answer quality here;
threshold *enforcement* lives in the graph routers — this module only
measures.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["_verify_claims"]


def _verify_claims(answer: str, chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Claim-level verification of a generated answer (V2 plan item 15).

    Extracts factual claims via the rule-based :class:`ClaimExtractor` and
    verifies each against the evidence chunks via the
    :class:`EvidenceVerifier` (section-match + textual overlap, no LLM).

    Phase 3 upgrade: instead of returning only ``verified`` + ``confidence``,
    the claim report now carries per-claim :class:`ClaimVerification` status
    (``SUPPORTED`` / ``PARTIALLY_SUPPORTED`` / ``UNSUPPORTED`` /
    ``CONTRADICTED``) with per-claim evidence, authority and contradiction
    signals.  All signals are derived from the evidence chunks themselves
    (chunk-keyed authority weights, verifier contradiction pairs, repeal-
    language temporal flags), so each claim is judged only by the evidence
    that supports it — identical on the linear and DAG paths.

    Returns ``None`` when the answer carries no verifiable claims.
    """
    if not answer or not answer.strip():
        return None
    from app.rag.agent.sufficiency import (
        as_retrieved_chunks,
        chunk_authority_score,
        chunk_temporally_invalid,
    )
    from app.rag.evidence_task import build_claim_verification
    from app.rag.verification.claim_extractor import ClaimExtractor
    from app.rag.verification.evidence_verifier import EvidenceVerifier

    claims = ClaimExtractor().extract(answer)
    if not claims:
        return None
    evidence_dicts = [c for c in chunks if isinstance(c, dict)]
    evidence_chunks = as_retrieved_chunks(evidence_dicts)
    verifications = EvidenceVerifier().verify_claims(claims, evidence_chunks)
    verifications_dict = [
        {
            "verified": v.verified,
            "confidence": round(v.confidence, 3),
            "method": v.method,
            "supporting_chunks": v.supporting_chunks,
        }
        for v in verifications
    ]
    # Chunk-keyed signal maps — per-claim inputs, not flattened globals.
    chunk_authority = {
        str(c.get("chunk_id") or ""): chunk_authority_score(c) for c in evidence_dicts if c.get("chunk_id")
    }
    invalid_ids = {str(c.get("chunk_id") or "") for c in evidence_dicts if chunk_temporally_invalid(c)}
    try:
        all_pairs = EvidenceVerifier().find_contradictions(evidence_chunks)
        contradictions = [
            {"chunk_a": p.a.chunk_id, "chunk_b": p.b.chunk_id, "kind": p.kind, "values": list(p.values)}
            for p in all_pairs
        ]
    except Exception:  # contradiction detection is best-effort enrichment
        contradictions = []
    claim_verifications = build_claim_verification(
        [c.to_dict() for c in claims],
        verifications_dict,
        chunk_authority=chunk_authority,
        contradictions=contradictions,
        temporally_invalid_ids=invalid_ids,
    )
    verified_count = sum(1 for v in verifications if v.verified)
    return {
        "claims": [cv.to_dict() for cv in claim_verifications],
        "claim_groundedness": verified_count / len(claims),
        "unverified_claims": [c.text for c, v in zip(claims, verifications, strict=True) if not v.verified],
        "claim_statuses": [cv.status.value for cv in claim_verifications],
    }
