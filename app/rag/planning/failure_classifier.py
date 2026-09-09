"""2.7 — Retrieval Failure Classifier (Intelligence Layer).

Classifies verification failures into specific recovery strategies.
Maps failure → targeted retrieval action.

ponytail: deterministic taxonomy — no LLM needed for classification.
Upgrade path: LLM-based diagnosis if taxonomy proves insufficient.
"""

from __future__ import annotations

from enum import StrEnum


class RetrievalFailure(StrEnum):
    MISSING_PROVISION = "missing_provision"
    WRONG_PROVISION = "wrong_provision"
    WRONG_ACT = "wrong_act"
    WRONG_JURISDICTION = "wrong_jurisdiction"
    MISSING_EXCEPTION = "missing_exception"
    MISSING_DEFINITION = "missing_definition"
    MISSING_CROSS_REF = "missing_cross_ref"
    TEMPORAL_CONFLICT = "temporal_conflict"
    INSUFFICIENT_AUTHORITY = "insufficient_authority"
    INSUFFICIENT_CASE_LAW = "insufficient_case_law"
    UNSUPPORTED_FACT_INFERENCE = "unsupported_fact_inference"
    # P2: expanded failure taxonomy
    INSUFFICIENT_EVIDENCE_COVERAGE = "insufficient_evidence_coverage"
    LOW_RELEVANCE = "low_relevance"
    INSUFFICIENT_AUTHORITY_SCORE = "insufficient_authority_score"
    MISSING_SPECIFICITY = "missing_specificity"
    EVIDENCE_CONTRADICTION = "evidence_contradiction"
    TEMPORAL_INVALIDITY = "temporal_invalidity"
    CONFLICTING_AUTHORITIES = "conflicting_authorities"
    ABSTAIN_REQUIRED = "abstain_required"


# Recovery mapping: failure → retrieval strategy
_RECOVERY_MAP: dict[str, str] = {
    RetrievalFailure.MISSING_PROVISION: "identifier_search",
    RetrievalFailure.WRONG_PROVISION: "collection_reroute",
    RetrievalFailure.WRONG_ACT: "identifier_search",
    RetrievalFailure.WRONG_JURISDICTION: "temporal_filter",
    RetrievalFailure.MISSING_EXCEPTION: "hierarchy_graph",
    RetrievalFailure.MISSING_DEFINITION: "definition_search",
    RetrievalFailure.MISSING_CROSS_REF: "kg_traversal",
    RetrievalFailure.TEMPORAL_CONFLICT: "temporal_retrieval",
    RetrievalFailure.INSUFFICIENT_AUTHORITY: "authority_retrieval",
    RetrievalFailure.INSUFFICIENT_CASE_LAW: "case_law_retrieval",
    RetrievalFailure.UNSUPPORTED_FACT_INFERENCE: "dense_expansion",
    # P2 expanded recovery mapping
    RetrievalFailure.INSUFFICIENT_EVIDENCE_COVERAGE: "expand_query",
    RetrievalFailure.LOW_RELEVANCE: "semantic_expansion",
    RetrievalFailure.INSUFFICIENT_AUTHORITY_SCORE: "authority_retrieval",
    RetrievalFailure.MISSING_SPECIFICITY: "identifier_search",
    RetrievalFailure.EVIDENCE_CONTRADICTION: "temporal_retrieval",
    RetrievalFailure.TEMPORAL_INVALIDITY: "temporal_retrieval",
    RetrievalFailure.CONFLICTING_AUTHORITIES: "hierarchy_graph",
    RetrievalFailure.ABSTAIN_REQUIRED: "abstain",
}


class FailureClassifier:
    """Classify retrieval failures by inspecting verification results."""

    def classify(self, verification_result: dict) -> list[str]:
        """Return list of failure types from verification output.

        P2: expanded to cover evidence coverage, relevance, authority,
        specificity, contradiction, and temporal validity signals.
        """
        failures: list[str] = []
        # Citation-based failures
        if verification_result.get("missing_citations"):
            failures.append(RetrievalFailure.MISSING_PROVISION)
        # Groundedness-based
        if verification_result.get("groundedness_score", 1.0) < 0.7:
            failures.append(RetrievalFailure.UNSUPPORTED_FACT_INFERENCE)
        # P2: evidence coverage check
        if verification_result.get("evidence_coverage", 1.0) < 0.5:
            failures.append(RetrievalFailure.INSUFFICIENT_EVIDENCE_COVERAGE)
        # P2: low relevance
        if verification_result.get("relevance_score", 1.0) < 0.5:
            failures.append(RetrievalFailure.LOW_RELEVANCE)
        # P2: authority score
        if verification_result.get("authority_score", 1.0) < 0.4:
            failures.append(RetrievalFailure.INSUFFICIENT_AUTHORITY_SCORE)
        # P2: contradiction
        if verification_result.get("has_contradiction", False):
            failures.append(RetrievalFailure.EVIDENCE_CONTRADICTION)
        # P2: temporal validity
        if verification_result.get("temporal_conflict", False):
            failures.append(RetrievalFailure.TEMPORAL_INVALIDITY)
        # P2: conflicting authorities
        if verification_result.get("conflicting_authorities", False):
            failures.append(RetrievalFailure.CONFLICTING_AUTHORITIES)
        # P2: abstention if evidence is critically insufficient
        if (
            verification_result.get("budget_exhausted", False)
            and verification_result.get("evidence_coverage", 1.0) < 0.3
        ):
            failures.append(RetrievalFailure.ABSTAIN_REQUIRED)
        return failures or [RetrievalFailure.MISSING_PROVISION]

    def recovery_strategy(self, failure: str) -> str:
        return _RECOVERY_MAP.get(failure, "dense_expansion")
