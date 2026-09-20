"""2.6 — Targeted Retry (Intelligence Layer).

Replace generic query expansion with failure-aware targeted retrieval.
When verification fails, diagnose *what evidence is missing* and target
retrieval for that specific gap.

ponytail: deterministic targeting based on failure classification.
Upgrade path: learned targeting if taxonomy proves insufficient.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.rag.planning.failure_classifier import FailureClassifier

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
        strategy = FailureClassifier().recovery_strategy(failure)

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
            return self._target_kg(query, context=context)
        elif strategy == "kg_reasoning":
            return self._target_kg_reasoning(query, context=context)
        elif strategy == "temporal_retrieval":
            return self._target_temporal(query)
        elif strategy == "authority_retrieval":
            return self._target_authority(query)
        elif strategy == "case_law_retrieval":
            return self._target_case_law(query)
        elif strategy in ("dense_expansion", "expand_query", "semantic_expansion"):
            return self._target_dense(query)
        elif strategy == "abstain":
            # No rewrite: abstention is the router's job (abstain_node).
            # A rewritten query would only disguise a retry that must not run.
            return query
        else:
            return self._generic_expand(query)

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

    def _target_kg(self, query: str, context: dict | None = None) -> str:
        """KG-based targeted query: expand via KG relationships.

        Extracts Section/provision identifiers and appends the KG
        relationship hints the retriever's KG arm understands
        (cross-reference, exception, authority), plus any KG paths
        already stored in context (from ``kg_reason_node``).
        """
        import re as _re

        sections = _re.findall(r"[Ss]ection\s+(\d+[A-Za-z]?)", query or "")
        hints = ["HAS_CROSS_REFERENCES", "HAS_EXCEPTION", "GRANTS_POWER_TO"]
        kg_paths = (context or {}).get("kg_paths") or []
        path_hint = ""
        if kg_paths:
            first_item = kg_paths[0]
            if isinstance(first_item, str):
                first = first_item
            elif isinstance(first_item, dict):
                steps = first_item.get("steps", [""])
                first = steps[0] if steps else ""
            else:
                # ReasoningPath object (direct reasoner caller, not kg_reason_node dicts).
                steps = getattr(first_item, "steps", [""])
                first = steps[0] if steps else ""
            path_hint = f" related:{first}" if first else ""
        if sections:
            return f"{query} Section {' Section '.join(sections)} ({' '.join(hints)}){path_hint}"
        return f"{query} ({' '.join(hints)}){path_hint}"

    def _target_kg_reasoning(self, query: str, context: dict | None = None) -> str:
        """KG-reasoning query: route through the KG reasoner capabilities."""
        capability = (context or {}).get("kg_capability", "actionable_answer")
        return f"{self._target_kg(query, context)} [{capability}]"

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
