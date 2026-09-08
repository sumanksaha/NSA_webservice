"""2.6 — Targeted Retry (Intelligence Layer).

Replace generic query expansion with failure-aware targeted retrieval.
When verification fails, diagnose *what evidence is missing* and target
retrieval for that specific gap.

ponytail: deterministic targeting based on failure classification.
Upgrade path: learned targeting if taxonomy proves insufficient.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.rag.planning.failure_classifier import RetrievalFailure


class TargetedRetryPlanner:
    """Plan targeted retrieval queries based on verification failures."""

    def __init__(self) -> None:
        pass

    def target_query(
        self,
        query: str,
        failures: list[RetrievalFailure],
        query_type: str,
        context: dict,
    ) -> str:
        """Generate a targeted query for missing evidence."""
        if not failures:
            return query

        failure = failures[0]
        strategy = self._recovery_strategy(failure, query_type, context)

        if strategy == "identifier_search":
            return self._target_identifier(query)
        elif strategy == "collection_reroute":
            return self._target_collection(query)
        elif strategy == "temporal_filter":
            return self._target_temporal(query)
        elif strategy == "hierarchy_graph":
            return self._target_hierarchy(query)
        elif strategy == "definition_search":
            return self._target_definition(query)
        elif strategy == "kg_traversal":
            return self._target_kg(query)
        elif strategy == "temporal_retrieval":
            return self._target_temporal(query)
        elif strategy == "authority_retrieval":
            return self._target_authority(query)
        elif strategy == "case_law_retrieval":
            return self._target_case_law(query)
        elif strategy == "dense_expansion":
            return self._target_dense(query)
        else:
            return self._generic_expand(query)

    def _recovery_strategy(
        self,
        failure: RetrievalFailure,
        query_type: str,
        context: dict,
    ) -> str:
        """Map failure to recovery strategy."""
        from app.rag.planning.failure_classifier import RetrievalFailure

        # Simple mapping: failure to strategy (mirrors failure_classifier)
        mapping = {
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
        return mapping.get(failure, "dense_expansion")

    # Target generation methods
    def _target_identifier(self, query: str) -> str:
        # Extract section/act keywords and look for them via identifier route
        # Simple: keep query but boost identifier route
        return f"{query} (identifier)"

    def _target_collection(self, query: str) -> str:
        # Reroute to a different collection
        return query  # placeholder

    def _target_temporal(self, query: str) -> str:
        # Add temporal qualifier
        return f"{query} (temporal)"

    def _target_hierarchy(self, query: str) -> str:
        # Traverse hierarchy relationships
        return f"{query} (hierarchy)"

    def _target_definition(self, query: str) -> str:
        # Add definition focus
        return f"{query} (definition)"

    def _target_kg(self, query: str) -> str:
        # Trigger KG traversal
        return f"{query} (kg)"

    def _target_authority(self, query: str) -> str:
        # Focus on authority-related chunks
        return f"{query} (authority)"

    def _target_case_law(self, query: str) -> str:
        # Focus on case law
        return f"{query} (case_law)"

    def _target_dense(self, query: str) -> str:
        # Generic dense expansion
        return f"{query} (expand)"

    def _generic_expand(self, query: str) -> str:
        # Fallback: traditional expansion
        return f"{query} explain"
