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
            return self._target_kg(query, context=context)
        elif strategy == "kg_reasoning":
            return self._target_kg_reasoning(query, context=context)
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
            RetrievalFailure.KG_TRAVERSAL_FAILED: "kg_traversal",
            RetrievalFailure.KG_LINEAGE_GAP: "kg_traversal",
            RetrievalFailure.KG_CONFLICT_UNRESOLVED: "kg_reasoning",
            RetrievalFailure.KG_ENTITY_UNRESOLVED: "kg_reasoning",
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

    def build_kg_queries(self, query: str, failures: list | None = None, context: dict | None = None) -> list[str]:
        """Multiple KG-targeted variants for iterative retrieval rounds.

        ``failures`` selects the hint set: lineage gaps favour amendment
        edges, conflict failures favour authority edges; default covers all.
        """
        failures = [str(f) for f in (failures or [])]
        if any("lineage" in f for f in failures):
            hints = ["AMENDED_BY", "SUPERSEDED_BY"]
        elif any("conflict" in f for f in failures):
            hints = ["GRANTS_POWER_TO", "HAS_AUTHORITY"]
        else:
            hints = ["HAS_CROSS_REFERENCES", "HAS_EXCEPTION", "GRANTS_POWER_TO"]
        base = self._target_kg(query, context=context)
        return [
            base,
            f"{query} ({' '.join(hints[:1])})",
            f"{query} ({' '.join(hints[1:])})",
        ]

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
