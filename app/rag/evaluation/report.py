"""Evaluation report — aggregate and summarize evaluation results.

Provides :class:`EvalReport` and :class:`EvalSummary` dataclasses for
serializing the output of :class:`app.rag.evaluation.runner.EvalRunner`
into a structured, JSON-serializable summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalSummary:
    """Aggregate statistics across an evaluation run.

    Attributes:
        total: Number of queries evaluated.
        errors: Number of queries that errored.
        metric_averages: Dict of metric name -> average score.
        mrr_avg: Average Mean Reciprocal Rank.
        latency_avg_ms: Average pipeline latency in ms.
        passed: Number of queries that passed all metric thresholds.
    """

    total: int = 0
    errors: int = 0
    metric_averages: dict[str, float] = field(default_factory=dict)
    mrr_avg: float = 0.0
    latency_avg_ms: float = 0.0
    passed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "errors": self.errors,
            "metric_averages": self.metric_averages,
            "mrr_avg": self.mrr_avg,
            "latency_avg_ms": self.latency_avg_ms,
            "passed": self.passed,
        }


@dataclass
class EvalReport:
    """Full evaluation report: per-query results + summary.

    Attributes:
        eval_run_id: UUID identifying this evaluation run.
        results: Per-query result dicts.
        summary: Aggregate :class:`EvalSummary`.
    """

    eval_run_id: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    summary: EvalSummary = field(default_factory=EvalSummary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_run_id": self.eval_run_id,
            "results": self.results,
            "summary": self.summary.to_dict(),
        }
