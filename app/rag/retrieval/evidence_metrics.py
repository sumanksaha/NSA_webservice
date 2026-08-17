"""Evidence-set metrics — Evidence Set Recall and related measures.

These metrics are separate from conventional R@10.  They measure whether the
selected evidence set contains all provisions required to answer the query.

Key metrics:

- ``EvidenceSetRecall``: fraction of required provisions covered by the set.
- ``EvidenceSetPrecision``: fraction of selected items that are relevant.
- ``EvidenceSetF1``: harmonic mean.
- ``EvidenceCoverageAtK``: coverage when selecting top-K items.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.rag.retrieval.evidence_selector import EvidenceSet

logger = logging.getLogger(__name__)


@dataclass
class EvidenceMetricResult:
    """Result of an evidence-set metric computation."""

    metric_name: str
    value: float
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric_name,
            "value": round(self.value, 6),
            "detail": self.detail,
        }


def evidence_set_recall(
    selected_ids: list[str | None],
    gold_ids: list[str | None],
) -> EvidenceMetricResult:
    """Compute Evidence Set Recall.

    Recall = |selected ∩ gold| / |gold|

    Args:
        selected_ids: Provision/chunk IDs in the selected evidence set.
        gold_ids: Provision/chunk IDs required to answer the query (gold).

    Returns:
        ``EvidenceMetricResult`` with the recall score.
    """
    selected_set = set(s for s in selected_ids if s)
    gold_set = set(g for g in gold_ids if g)

    if not gold_set:
        # No gold provisions → vacuously perfect recall
        return EvidenceMetricResult(
            "evidence_set_recall", 1.0,
            {"selected": list(selected_set), "gold": list(gold_set), "note": "empty gold set"},
        )

    intersect = selected_set & gold_set
    recall = len(intersect) / len(gold_set)

    return EvidenceMetricResult(
        "evidence_set_recall", recall,
        {"selected": sorted(selected_set), "gold": sorted(gold_set),
         "intersection": sorted(intersect), "missing": sorted(gold_set - selected_set)},
    )


def evidence_set_precision(
    selected_ids: list[str | None],
    gold_ids: list[str | None],
) -> EvidenceMetricResult:
    """Compute Evidence Set Precision.

    Precision = |selected ∩ gold| / |selected|
    """
    selected_set = set(s for s in selected_ids if s)
    gold_set = set(g for g in gold_ids if g)

    if not selected_set:
        return EvidenceMetricResult(
            "evidence_set_precision", 0.0,
            {"note": "empty selected set"},
        )

    intersect = selected_set & gold_set
    precision = len(intersect) / len(selected_set)

    return EvidenceMetricResult(
        "evidence_set_precision", precision,
        {"intersection_size": len(intersect), "selected_size": len(selected_set)},
    )


def evidence_set_f1(
    selected_ids: list[str | None],
    gold_ids: list[str | None],
) -> EvidenceMetricResult:
    """Compute Evidence Set F1 (harmonic mean of precision and recall)."""
    prec = evidence_set_precision(selected_ids, gold_ids)
    rec = evidence_set_recall(selected_ids, gold_ids)

    f1 = 0.0 if prec.value + rec.value == 0 else 2 * prec.value * rec.value / (prec.value + rec.value)

    return EvidenceMetricResult(
        "evidence_set_f1", f1,
        {"precision": prec.value, "recall": rec.value},
    )


def evidence_coverage_at_k(
    ranked_ids: list[str | None],
    gold_ids: list[str | None],
    k: int = 3,
) -> EvidenceMetricResult:
    """Compute coverage when selecting top-K ranked items.

    Unlike recall (which uses the evidence selector's output), this measures
    what coverage you'd get with a simple top-K slice of the reranked list.
    """
    top_k = [s for s in ranked_ids[:k] if s]
    gold_set = set(g for g in gold_ids if g)
    top_k_set = set(top_k)

    if not gold_set:
        return EvidenceMetricResult(
            "evidence_coverage_at_k", 1.0,
            {"k": k, "note": "empty gold set"},
        )

    intersect = top_k_set & gold_set
    coverage = len(intersect) / len(gold_set)

    return EvidenceMetricResult(
        "evidence_coverage_at_k", coverage,
        {"k": k, "covered": sorted(intersect), "missing": sorted(gold_set - top_k_set)},
    )


def evaluate_evidence_set(
    evidence_set: EvidenceSet,
    gold_ids: list[str | None],
    ranked_chunks: list[Any] | None = None,
) -> list[EvidenceMetricResult]:
    """Evaluate an evidence set against gold provisions.

    Args:
        evidence_set: The selected evidence set.
        gold_ids: Gold provision/chunk IDs required for the query.
        ranked_chunks: Optional original ranked list (for k-coverage).

    Returns:
        List of metric results: recall, precision, f1, and optionally
        coverage@k.
    """
    results: list[EvidenceMetricResult] = []

    selected_ids = evidence_set.chunk_ids
    results.append(evidence_set_recall(selected_ids, gold_ids))
    results.append(evidence_set_precision(selected_ids, gold_ids))
    results.append(evidence_set_f1(selected_ids, gold_ids))

    if ranked_chunks is not None:
        ranked_ids = [getattr(c, "chunk_id", None) for c in ranked_chunks]
        for k in (2, 3, 5):
            if len(ranked_chunks) >= k:
                results.append(evidence_coverage_at_k(ranked_ids, gold_ids, k=k))

    return results


# --------------------------------------------------------------------------- #
# Batch evaluation
# --------------------------------------------------------------------------- #


@dataclass
class EvidenceBatchResult:
    """Aggregated evidence-set metrics across a batch of queries."""

    num_queries: int
    avg_recall: float
    avg_precision: float
    avg_f1: float
    coverage_at_3: float
    coverage_at_5: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_queries": self.num_queries,
            "avg_recall": round(self.avg_recall, 6),
            "avg_precision": round(self.avg_precision, 6),
            "avg_f1": round(self.avg_f1, 6),
            "coverage_at_3": round(self.coverage_at_3, 6),
            "coverage_at_5": round(self.coverage_at_5, 6),
        }


def evaluate_evidence_batch(
    evidence_sets: list[EvidenceSet],
    gold_sets: list[list[str | None]],
    ranked_lists: list[list[Any]] | None = None,
) -> EvidenceBatchResult:
    """Compute aggregate evidence-set metrics across multiple queries.

    Args:
        evidence_sets: Selected evidence sets, one per query.
        gold_sets: Gold provision IDs per query.
        ranked_lists: Optional ranked chunk lists per query (for coverage@K).
    """
    if not evidence_sets or not gold_sets:
        return EvidenceBatchResult(0, 0.0, 0.0, 0.0, 0.0, 0.0)

    num = min(len(evidence_sets), len(gold_sets))
    recalls: list[float] = []
    precisions: list[float] = []
    f1s: list[float] = []
    cov3s: list[float] = []
    cov5s: list[float] = []

    for i in range(num):
        es = evidence_sets[i]
        gold = gold_sets[i]
        selected_ids = es.chunk_ids

        rec = evidence_set_recall(selected_ids, gold)
        prem = evidence_set_precision(selected_ids, gold)
        f1 = evidence_set_f1(selected_ids, gold)

        recalls.append(rec.value)
        precisions.append(prem.value)
        f1s.append(f1.value)

        if ranked_lists and i < len(ranked_lists):
            ranked_ids = [getattr(c, "chunk_id", None) for c in ranked_lists[i]]
            for k, collector in [(3, cov3s), (5, cov5s)]:
                c = evidence_coverage_at_k(ranked_ids, gold, k=k)
                collector.append(c.value)

    return EvidenceBatchResult(
        num_queries=num,
        avg_recall=sum(recalls) / len(recalls) if recalls else 0.0,
        avg_precision=sum(precisions) / len(precisions) if precisions else 0.0,
        avg_f1=sum(f1s) / len(f1s) if f1s else 0.0,
        coverage_at_3=sum(cov3s) / len(cov3s) if cov3s else 0.0,
        coverage_at_5=sum(cov5s) / len(cov5s) if cov5s else 0.0,
    )


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Basic recall check
    r = evidence_set_recall(["a", "b", "c"], ["a", "c", "d"])
    assert r.value == 2 / 3, r

    p = evidence_set_precision(["a", "b", "c"], ["a", "c", "d"])
    assert p.value == 2 / 3, p

    f1 = evidence_set_f1(["a", "b"], ["a", "c"])
    assert 0 < f1.value < 1, f1

    cov = evidence_coverage_at_k(["a", "b", "c", "d"], ["a", "c", "e"], k=3)
    assert abs(cov.value - 2/3) < 0.001, cov

    # Empty gold → recall = 1.0
    r = evidence_set_recall(["a"], [])
    assert r.value == 1.0

