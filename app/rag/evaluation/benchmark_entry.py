"""Neutral benchmark-entry shape shared by the evaluator and its metrics.

``QueryBenchmark`` used to live in :mod:`app.rag.evaluation.benchmark`, while
:mod:`app.rag.evaluation.decomposition_metrics` imported it under
``TYPE_CHECKING``. That made the two modules depend on each other: the
evaluator calls the metrics at scoring time, and the metrics describe the
evaluator's entry type.

This module owns only the data shape. It imports nothing from either side, so
both can depend on it without a cycle. Existing callers can keep importing
``QueryBenchmark`` from ``benchmark.py`` (it re-exports this class).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryBenchmark:
    """Single query benchmark entry."""

    query: str
    gold_subquestions: list[str] = field(default_factory=list)
    gold_provisions: dict[str, list[str]] = field(default_factory=dict)
    gold_answer: str = ""
    query_class: str = "general"
    domain: str = ""
    decomposed_subquestions: list[str] = field(default_factory=list)
    predicted_provisions: list[str] = field(default_factory=list)
    answered_subquestions: int = 0

    # --- Phase 4: kind/dependency-level gold expectations ---------------- #
    gold_task_kinds: list[str] = field(default_factory=list)
    gold_dependencies: dict[str, list[str]] = field(default_factory=dict)
    gold_entities: list[str] = field(default_factory=list)
    predicted_task_kinds: list[str] = field(default_factory=list)
    predicted_dependencies: set[tuple[str, str]] = field(default_factory=set)

    # --- Phase 3: requirement-level prediction + gold --------------------- #
    predicted_requirement_graph: Any | None = None
    predicted_requirement_ids: set[str] = field(default_factory=set)
    predicted_requirements: list[dict[str, Any]] = field(default_factory=list)
    # Requirement-level gold (from GoldEntry):
    gold_requirements: list[dict[str, Any]] = field(default_factory=list)
    gold_answer_types: dict[str, str] = field(default_factory=dict)
    gold_mandatory: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "query": self.query,
            "gold_subquestions": self.gold_subquestions,
            "gold_provisions": self.gold_provisions,
            "gold_answer": self.gold_answer,
            "query_class": self.query_class,
            "domain": self.domain,
            "decomposed_subquestions": self.decomposed_subquestions,
            "predicted_provisions": self.predicted_provisions,
            "answered_subquestions": self.answered_subquestions,
            "gold_task_kinds": self.gold_task_kinds,
            "gold_dependencies": {k: list(v) for k, v in self.gold_dependencies.items()},
            "predicted_task_kinds": self.predicted_task_kinds,
            "predicted_dependencies": sorted(self.predicted_dependencies),
            "predicted_requirement_ids": sorted(self.predicted_requirement_ids),
            "predicted_requirements": list(self.predicted_requirements),
        }
