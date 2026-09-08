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
}


class FailureClassifier:
    """Classify retrieval failures by inspecting verification results."""

    def classify(self, verification_result: dict) -> list[str]:
        """Return list of failure types from verification output."""
        failures = []
        # Minimal heuristic: check for missing citations, low groundedness
        if verification_result.get("missing_citations"):
            failures.append(RetrievalFailure.MISSING_PROVISION)
        if verification_result.get("groundedness_score", 1.0) < 0.7:
            failures.append(RetrievalFailure.UNSUPPORTED_FACT_INFERENCE)
        return failures or [RetrievalFailure.MISSING_PROVISION]

    def recovery_strategy(self, failure: str) -> str:
        return _RECOVERY_MAP.get(failure, "dense_expansion")
