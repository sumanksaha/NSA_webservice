"""Decomposition benchmark for measuring query planning quality.

Tracks decomposition accuracy, subquestion coverage, and query-class performance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
        }


class DecompositionBenchmark:
    """Benchmark for measuring query decomposition and evidence coverage."""

    def __init__(self) -> None:
        self.queries: list[QueryBenchmark] = []

    def add(
        self,
        query: str,
        gold_subquestions: list[str] | None = None,
        gold_provisions: dict[str, list[str]] | None = None,
        gold_answer: str = "",
        query_class: str = "general",
        domain: str = "",
    ) -> None:
        """Add a query to the benchmark."""
        self.queries.append(
            QueryBenchmark(
                query=query,
                gold_subquestions=gold_subquestions or [],
                gold_provisions=gold_provisions or {},
                gold_answer=gold_answer,
                query_class=query_class,
                domain=domain,
            )
        )

    def evaluate(self) -> dict[str, Any]:
        """Run evaluation and return metrics."""

        if not self.queries:
            return {"error": "No queries in benchmark"}

        total = len(self.queries)

        # Decomposition accuracy: subquestions match gold standard
        correct_decomps = sum(1 for q in self.queries if q.decomposed_subquestions == q.gold_subquestions)
        decomp_accuracy = correct_decomps / total if total else 0.0

        # Subquestion coverage: % of gold subquestions with evidence
        total_subqs = sum(len(q.gold_subquestions) for q in self.queries)
        covered_subqs = sum(q.answered_subquestions for q in self.queries)
        subq_coverage = covered_subqs / total_subqs if total_subqs else 0.0

        # Per-query-class performance
        class_stats: dict[str, dict[str, int]] = {}
        for q in self.queries:
            cls = q.query_class
            if cls not in class_stats:
                class_stats[cls] = {"total": 0, "correct": 0, "covered": 0}
            class_stats[cls]["total"] += 1
            if q.decomposed_subquestions == q.gold_subquestions:
                class_stats[cls]["correct"] += 1
            class_stats[cls]["covered"] += q.answered_subquestions

        return {
            "total_queries": total,
            "decomposition_accuracy": round(decomp_accuracy, 4),
            "subquestion_coverage": round(subq_coverage, 4),
            "total_subquestions": total_subqs,
            "covered_subquestions": covered_subqs,
            "per_query_class": {
                cls: {
                    "recall": round(stats["correct"] / stats["total"] if stats["total"] else 0, 4),
                    "count": stats["total"],
                    "avg_coverage": round(stats["covered"] / stats["total"] if stats["total"] else 0, 4),
                }
                for cls, stats in class_stats.items()
            },
        }
