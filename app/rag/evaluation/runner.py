"""Evaluation runner — batch evaluation over a dataset.

:class:`EvalRunner` accepts a *pipeline callable* that turns a query into
a RAG response (``answer``, ``retrieved_chunks``, ``cited_chunk_ids``, ...).
This decouples the runner from any specific retrieval backend (Qdrant, stub,
mock), following the dependency-injection pattern used throughout the RAG
generation service.

For each dataset entry the runner:
    1. Runs the pipeline callable.
    2. Computes all six metrics.
    3. Persists results via :class:`EvalStorage` (best-effort).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.rag.evaluation.metrics import (
    AnswerRelevanceMetric,
    CitationRecallMetric,
    ContextPrecisionMetric,
    ContextRecallMetric,
    EvalScore,
    FaithfulnessMetric,
    GroundednessMetric,
)
from app.rag.evaluation.storage import EvalStorage
from app.rag.retrieval.result import RetrievedChunk

logger = logging.getLogger(__name__)

#: Type alias for the pipeline function: query -> result dict.
PipelineFn = Callable[
    [str],
    dict[str, Any],
]


@dataclass
class MetricBundle:
    """Container for all six evaluation metrics."""

    scores: list[EvalScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, float]:
        return {s.name: s.score for s in self.scores}

    def get(self, name: str) -> float | None:
        for s in self.scores:
            if s.name == name:
                return s.score
        return None


class _DefaultMetrics:
    """All six metrics instantiated once (shared across evaluations)."""

    def __init__(self) -> None:
        self.faithfulness = FaithfulnessMetric()
        self.answer_relevance = AnswerRelevanceMetric()
        self.context_precision = ContextPrecisionMetric()
        self.context_recall = ContextRecallMetric()
        self.citation_recall = CitationRecallMetric()
        self.groundedness = GroundednessMetric()

    def all(self) -> list:
        return [
            self.faithfulness,
            self.answer_relevance,
            self.context_precision,
            self.context_recall,
            self.citation_recall,
            self.groundedness,
        ]


class EvalRunner:
    """Run batch RAG evaluation over a dataset.

    Args:
        pipeline_fn: Callable that takes a query string and returns a dict
            with keys ``answer`` (str), ``retrieved_chunks``
            (list[RetrievedChunk] or list[dict]), ``cited_chunk_ids``
            (list[str]), and optionally ``retrieval_mrr``.
        storage: Eval storage backend (defaults to :class:`EvalStorage`).
        metrics: Optional pre-configured metric bundle.
    """

    def __init__(
        self,
        pipeline_fn: PipelineFn,
        storage: EvalStorage | None = None,
        metrics: _DefaultMetrics | None = None,
    ) -> None:
        self.pipeline_fn = pipeline_fn
        self.storage = storage or EvalStorage()
        self.metrics = metrics or _DefaultMetrics()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def evaluate_one(
        self,
        query: str,
        expected_answer: str | None,
        expected_citations: list[str],
        query_type: str = "general_qa",
        top_k: int = 10,
    ) -> dict[str, Any]:
        """Evaluate a single query through the full pipeline.

        Returns a result dict with metrics, retrieved chunks, and MRR.
        Does **not** persist — use :meth:`evaluate_batch` for persistence.
        """
        start = time.perf_counter()
        pipeline_result = self.pipeline_fn(query)
        pipeline_latency_ms = int((time.perf_counter() - start) * 1000)

        answer = pipeline_result.get("answer", "")
        raw_chunks = pipeline_result.get("retrieved_chunks", [])
        cited_ids = pipeline_result.get("cited_chunk_ids", [])

        chunks: list[RetrievedChunk] = []
        for raw in raw_chunks:
            if isinstance(raw, RetrievedChunk):
                chunks.append(raw)
            elif isinstance(raw, dict):
                chunks.append(RetrievedChunk.from_dict(raw))

        # Compute all six metrics.
        m = self.metrics
        bundle = MetricBundle()
        bundle.scores.append(
            m.faithfulness.compute(answer, chunks, query=query)
        )
        bundle.scores.append(
            m.answer_relevance.compute(answer, query, expected_answer)
        )
        bundle.scores.append(m.context_precision.compute(query, chunks))
        bundle.scores.append(
            m.context_recall.compute(expected_citations, chunks)
        )
        bundle.scores.append(m.citation_recall.compute(cited_ids, chunks))
        bundle.scores.append(m.groundedness.compute(answer, chunks))

        mrr = self._compute_mrr(expected_citations, chunks)

        result: dict[str, Any] = {
            "query": query,
            "query_type": query_type,
            "answer": answer,
            "retrieved_chunks": [c.to_dict() for c in chunks],
            "cited_chunk_ids": cited_ids,
            "metrics": bundle.to_dict(),
            "metric_details": {s.name: s.detail for s in bundle.scores},
            "metric_explanations": {s.name: s.explanation for s in bundle.scores},
            "retrieval_mrr": mrr,
            "latency_ms": pipeline_latency_ms,
        }
        return result

    def evaluate_batch(
        self,
        dataset_entries: list,
        eval_run_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Run :meth:`evaluate_one` over a list of dataset entries.

        Args:
            dataset_entries: Iterable of objects with ``query``,
                ``expected_answer``, ``expected_citations``,
                ``query_type``, ``difficulty`` attributes (e.g.
                :class:`RAGEvalDataset` rows, or dicts).
            eval_run_id: UUID for the evaluation run.  If ``None``,
                a new UUID is generated.
            persist: Whether to persist results to the DB.

        Returns:
            An :class:`EvalReport`-like dict with per-query results and
            aggregate summary statistics.
        """
        eval_run_id = eval_run_id or str(uuid.uuid4())
        results: list[dict[str, Any]] = []

        for entry in dataset_entries:
            query = getattr(entry, "query", entry.get("query")) if not isinstance(entry, dict) else entry["query"]
            expected_answer = (
                getattr(entry, "expected_answer", entry.get("expected_answer"))
                if not isinstance(entry, dict)
                else entry.get("expected_answer")
            )
            expected_citations = (
                getattr(entry, "expected_citations", entry.get("expected_citations"))
                if not isinstance(entry, dict)
                else entry.get("expected_citations", [])
            )
            query_type = (
                getattr(entry, "query_type", entry.get("query_type"))
                if not isinstance(entry, dict)
                else entry.get("query_type", "general_qa")
            )

            try:
                result = self.evaluate_one(
                    query=query,
                    expected_answer=expected_answer,
                    expected_citations=expected_citations,
                    query_type=query_type,
                )
                results.append(result)

                if persist:
                    self.storage.save_result(
                        eval_run_id=eval_run_id,
                        query=query,
                        expected_answer=expected_answer,
                        expected_citations=expected_citations,
                        actual_answer=result["answer"],
                        actual_citations=result["cited_chunk_ids"],
                        metrics=result["metrics"],
                        retrieval_mrr=result["retrieval_mrr"],
                        latency_ms=result["latency_ms"],
                    )
            except Exception as exc:
                logger.error("EvalRunner.eval error on query %r: %s", query, exc)
                results.append({
                    "query": query,
                    "error": str(exc),
                    "metrics": {},
                })

        return self._summarize(eval_run_id, results)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compute_mrr(
        relevant_ids: list[str], chunks: list[RetrievedChunk]
    ) -> float:
        """Mean Reciprocal Rank — rank of first relevant chunk (1-based)."""
        if not relevant_ids:
            return 0.0
        relevant_set = set(relevant_ids)
        for rank, chunk in enumerate(chunks, start=1):
            if chunk.chunk_id in relevant_set:
                return round(1.0 / rank, 4)
        return 0.0

    def _summarize(
        self, eval_run_id: str, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Aggregate per-query results into summary statistics."""
        metric_names = [
            "faithfulness", "answer_relevance", "context_precision",
            "context_recall", "citation_recall", "groundedness",
        ]
        summary: dict[str, Any] = {
            "eval_run_id": eval_run_id,
            "total": len(results),
            "errors": sum(1 for r in results if "error" in r),
        }
        for name in metric_names:
            vals = [
                r["metrics"].get(name)
                for r in results
                if "metrics" in r and r["metrics"].get(name) is not None
            ]
            summary[f"{name}_avg"] = (
                round(sum(vals) / len(vals), 4) if vals else None
            )
        mrrs = [r.get("retrieval_mrr", 0.0) for r in results if "metrics" in r]
        summary["mrr_avg"] = round(sum(mrrs) / len(mrrs), 4) if mrrs else 0.0
        summary["latency_avg_ms"] = (
            round(sum(r.get("latency_ms", 0) for r in results) / len(results), 2)
            if results else 0
        )
        return {"eval_run_id": eval_run_id, "results": results, "summary": summary}
