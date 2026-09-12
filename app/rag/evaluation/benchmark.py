"""Decomposition benchmark for measuring query planning quality (Phase 4, plan item 19).

Compares the planner's decomposition against the gold dataset in
:mod:`app.rag.evaluation.gold_dataset` and reports:

- **task_recall** — fraction of gold task kinds matched by predicted tasks
  (multiset match: duplicates must be predicted to count).
- **task_precision** — fraction of predicted tasks that map to gold kinds
  (extra or duplicate tasks hurt).
- **decomposition_f1** — per-entry harmonic mean of recall/precision; a
  decomposition is "atomic" exactly when its task multiset equals gold.
- **dependency_accuracy** — Jaccard overlap of dependency edges
  (``(dep_kind, kind)`` pairs) between prediction and gold.
- **over/under-decomposition rates** — entries with too many / too few tasks.
- **exact_match_rate** — strict multiset equality (kept for continuity with
  the original ``decomposition_accuracy`` metric).
- **per_query_class** — the above, broken out by query class.

Legacy API (``add`` with ``gold_subquestions``, strict string equality) is
preserved; entries using it are scored with the original metrics.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.rag.evaluation.gold_dataset import GoldEntry
    from app.rag.planning.query_planner import DecompositionResult


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


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


class DecompositionBenchmark:
    """Benchmark for measuring query decomposition and evidence coverage."""

    def __init__(self) -> None:
        self.queries: list[QueryBenchmark] = []

    # ------------------------------------------------------------------ #
    # Population
    # ------------------------------------------------------------------ #
    def add(
        self,
        query: str,
        gold_subquestions: list[str] | None = None,
        gold_provisions: dict[str, list[str]] | None = None,
        gold_answer: str = "",
        query_class: str = "general",
        domain: str = "",
    ) -> None:
        """Add a query to the benchmark (legacy subquestion-style API)."""
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

    def add_gold_entry(self, entry: GoldEntry) -> QueryBenchmark:
        """Add a gold-dataset entry (kind/dependency-level expectations).

        Phase 3: also transfers requirement-level gold fields
        (gold_requirements, gold_answer_types, gold_mandatory) so the
        requirement-level metrics can score against them.
        """
        self.queries.append(
            QueryBenchmark(
                query=entry["query"],
                query_class=entry.get("query_class", "general"),
                domain=entry.get("domain", ""),
                gold_task_kinds=list(entry.get("gold_task_kinds") or []),
                gold_dependencies=dict(entry.get("gold_dependencies") or {}),
                gold_entities=list(entry.get("gold_entities") or []),
                gold_requirements=list(entry.get("gold_requirements") or []),
                gold_answer_types=dict(entry.get("gold_answer_types") or {}),
                gold_mandatory=set(entry.get("gold_mandatory") or []),
            )
        )

    @classmethod
    def from_gold_dataset(cls, entries: list[GoldEntry] | None = None) -> DecompositionBenchmark:
        """Build a benchmark pre-populated from the gold dataset.

        Args:
            entries: Gold entries; defaults to the full
                :data:`app.rag.evaluation.gold_dataset.GOLD_DECOMPOSITION`.
        """
        if entries is None:
            from app.rag.evaluation.gold_dataset import GOLD_DECOMPOSITION

            entries = GOLD_DECOMPOSITION
        bench = cls()
        for entry in entries:
            bench.add_gold_entry(entry)
        return bench

    def record_prediction(
        self,
        query: str,
        decomposition: DecompositionResult,
        query_class: str | None = None,
    ) -> QueryBenchmark | None:
        """Record the planner's decomposition for a gold query.

        Matches the entry by exact query text; returns ``None`` (and logs a
        warning) when the query is not in the benchmark.
        """
        entry = next((q for q in self.queries if q.query == query), None)
        if entry is None:
            logger.warning("record_prediction: %r not in benchmark", query)
            return None

        tasks = list(getattr(decomposition, "tasks", None) or [])
        entry.predicted_task_kinds = [t.evidence_requirement.value for t in tasks]
        by_id = {t.task_id: t for t in tasks}
        edges: set[tuple[str, str]] = set()
        for task in tasks:
            for dep_id in task.dependency or []:
                dep_task = by_id.get(dep_id)
                if dep_task is not None:
                    edges.add((dep_task.evidence_requirement.value, task.evidence_requirement.value))
        entry.predicted_dependencies = edges
        if query_class:
            entry.query_class = query_class

        # Phase 3: record requirement-graph-level prediction for the richer
        # metrics (requirement_coverage, atomicity, efficiency, evidence completeness).
        try:
            from app.rag.evaluation.decomposition_metrics import record_requirement_prediction

            rg = getattr(decomposition, "requirement_graph", None)
            if rg is not None:
                record_requirement_prediction(entry, rg)
        except Exception as exc:
            logger.debug("record_prediction: requirement recording failed (%s)", exc)
        return entry

    # ------------------------------------------------------------------ #
    # Scoring
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        sufficiency_map: dict[str, dict[str, bool]] | None = None,
    ) -> dict[str, Any]:
        """Run evaluation and return metrics.

        ``sufficiency_map`` (optional, Phase 3) maps query text →
        {requirement_id: sufficient} from a real agent run (the
        ``requirement_sufficiency`` state key produced by
        ``evidence_sufficiency_node``).  When provided, the requirement-level
        evidence_completeness (EC) metric reflects actual retrieval outcomes
        instead of the conservative 0.0 default.
        """
        if not self.queries:
            return {"error": "No queries in benchmark"}

        legacy = [q for q in self.queries if q.gold_subquestions and not q.gold_task_kinds]
        kind_scored = [q for q in self.queries if q.gold_task_kinds]

        report: dict[str, Any] = {"total_queries": len(self.queries)}
        if kind_scored:
            report.update(self._evaluate_kind_entries(kind_scored))
        if legacy:
            report.update(self._evaluate_legacy_entries(legacy))
        # Phase 3: requirement-level metrics (RC, AS, DE, EC) when gold
        # requirements are present.
        try:
            from app.rag.evaluation.decomposition_metrics import requirement_level_report

            req_report = requirement_level_report(self, sufficiency_map=sufficiency_map)
            if "error" not in req_report:
                report["requirement_level"] = req_report
        except Exception as exc:  # best-effort: metrics should not crash evaluation
            logger.debug("evaluate: requirement_level_report failed (%s)", exc)
        return report

    def _evaluate_kind_entries(self, entries: list[QueryBenchmark]) -> dict[str, Any]:
        rec: list[float] = []
        prec: list[float] = []
        f1: list[float] = []
        dep: list[float] = []
        exact: list[float] = []
        over = 0
        under = 0
        per_class: dict[str, dict[str, list[float]]] = {}

        for q in entries:
            gold = Counter(q.gold_task_kinds)
            pred = Counter(q.predicted_task_kinds)
            matched = sum((gold & pred).values())
            n_gold, n_pred = sum(gold.values()), sum(pred.values())

            recall = matched / n_gold if n_gold else 0.0
            precision = matched / n_pred if n_pred else 0.0
            rec.append(recall)
            prec.append(precision)
            if n_gold + n_pred:
                f1.append(2 * matched / (n_gold + n_pred))
            exact.append(1.0 if gold == pred else 0.0)

            if n_pred > n_gold:
                over += 1
            elif n_pred < n_gold:
                under += 1

            gold_edges = {(d, kind) for kind, deps in (q.gold_dependencies or {}).items() for d in deps}
            pred_edges = set(q.predicted_dependencies or set())
            union = gold_edges | pred_edges
            if union:
                dep.append(len(gold_edges & pred_edges) / len(union))

            stats = per_class.setdefault(q.query_class, {"recall": [], "f1": []})
            stats["recall"].append(recall)
            stats["f1"].append(f1[-1] if n_gold + n_pred else 0.0)

        n = len(entries)
        return {
            "task_recall": _mean(rec),
            "task_precision": _mean(prec),
            "decomposition_f1": _mean(f1),
            "dependency_accuracy": _mean(dep),
            "exact_match_rate": _mean(exact),
            "over_decomposition_rate": round(over / n, 4),
            "under_decomposition_rate": round(under / n, 4),
            "per_query_class": {
                cls: {
                    "count": len(stats["recall"]),
                    "avg_recall": _mean(stats["recall"]),
                    "avg_f1": _mean(stats["f1"]),
                }
                for cls, stats in sorted(per_class.items())
            },
        }

    def _evaluate_legacy_entries(self, entries: list[QueryBenchmark]) -> dict[str, Any]:
        total = len(entries)
        correct = sum(1 for q in entries if q.decomposed_subquestions == q.gold_subquestions)
        total_subqs = sum(len(q.gold_subquestions) for q in entries)
        covered_subqs = sum(q.answered_subquestions for q in entries)

        class_stats: dict[str, dict[str, int]] = {}
        for q in entries:
            cls = q.query_class
            if cls not in class_stats:
                class_stats[cls] = {"total": 0, "correct": 0, "covered": 0}
            class_stats[cls]["total"] += 1
            if q.decomposed_subquestions == q.gold_subquestions:
                class_stats[cls]["correct"] += 1
            class_stats[cls]["covered"] += q.answered_subquestions

        return {
            "legacy": {
                "decomposition_accuracy": round(correct / total, 4) if total else 0.0,
                "subquestion_coverage": (round(covered_subqs / total_subqs, 4) if total_subqs else 0.0),
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
        }
