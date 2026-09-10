"""Evaluation runner — batch evaluation over a dataset.

:class:`EvalRunner` accepts a *pipeline callable* that turns a query into
a RAG response (``answer``, ``retrieved_chunks``, ``cited_chunk_ids``, ...).
This decouples the runner from any specific retrieval backend (Qdrant, stub,
mock), following the dependency-injection pattern used throughout the RAG
generation service.

For each dataset entry the runner:
    1. Runs the pipeline callable.
    2. Computes lightweight metadata (latency, MRR).
    3. Persists results via :class:`EvalStorage` (best-effort).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from app.rag.evaluation.metrics import CoverageMetrics, EvalScore
from app.rag.evaluation.ragas_metrics import (
    AnswerRelevanceMetric,
    CitationRecallMetric,
    ContextPrecisionMetric,
    ContextRecallMetric,
    FaithfulnessMetric,
    GroundednessMetric,
)
from app.rag.evaluation.storage import EvalStorage
from app.rag.retrieval.result import RetrievedChunk

logger = logging.getLogger(__name__)

#: Type alias for the pipeline function: query -> result dict.
PipelineFn = Callable[
    [str],
    dict[str, object],
]


class EvalRunner:
    """Run batch RAG evaluation over a dataset.

    Args:
        pipeline_fn: Callable that takes a query string and returns a dict
            with keys ``answer`` (str), ``retrieved_chunks``
            (list[RetrievedChunk] or list[dict]), ``cited_chunk_ids``
            (list[str]), and optionally ``retrieval_mrr``.
        storage: Eval storage backend (defaults to :class:`EvalStorage`).
    """

    def __init__(
        self,
        pipeline_fn: PipelineFn,
        storage: EvalStorage | None = None,
    ) -> None:
        self.pipeline_fn = pipeline_fn
        self.storage = storage or EvalStorage()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def evaluate_one(
        self,
        query: str,
        expected_answer: str | None = None,
        expected_citations: list[str] | None = None,
        query_type: str = "general_qa",
        top_k: int = 10,
    ) -> dict[str, object]:
        """Evaluate a single query through the full pipeline.

        Runs the pipeline callable, then scores the response with the
        deterministic RAGAS-style reference metrics (faithfulness, answer
        relevance, context precision/recall, citation recall, groundedness),
        EvidenceTask coverage, and retrieval MRR.

        Returns:
            A dict with keys: ``query``, ``answer``, ``retrieved_chunks``,
            ``cited_chunk_ids``, ``metrics`` (per-metric scores),
            ``metric_details``, ``metric_explanations``, ``coverage``,
            ``retrieval_mrr``, ``latency_ms``.
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

        # Evidence Task coverage metrics (Phase 2+)
        coverage = CoverageMetrics()
        coverage.update(
            evidence_tasks=pipeline_result.get("evidence_tasks"),
            retrieval_plan=pipeline_result.get("retrieval_plan"),
            chunks=[c.to_dict() for c in chunks],
        )

        mrr = self._compute_mrr(expected_citations or [], chunks)

        # RAGAS-style reference metrics (Phase 4) — deterministic, no LLM.
        metric_scores: dict[str, EvalScore] = {
            "faithfulness": FaithfulnessMetric().compute(answer, chunks, query=query),
            "answer_relevance": AnswerRelevanceMetric().compute(
                answer, query, expected_answer
            ),
            "context_precision": ContextPrecisionMetric().compute(query, chunks),
            "context_recall": ContextRecallMetric().compute(
                expected_citations or [], chunks
            ),
            "citation_recall": CitationRecallMetric().compute(cited_ids or [], chunks),
            "groundedness": GroundednessMetric().compute(answer, chunks),
        }

        result: dict[str, object] = {
            "query": query,
            "query_type": query_type,
            "answer": answer,
            "retrieved_chunks": [c.to_dict() for c in chunks],
            "cited_chunk_ids": cited_ids or [],
            "metrics": {name: s.score for name, s in metric_scores.items()},
            "metric_details": {
                name: s.detail for name, s in metric_scores.items()
            },
            "metric_explanations": {
                name: s.explanation for name, s in metric_scores.items()
            },
            "coverage": coverage.to_dict(),
            "retrieval_mrr": mrr,
            "latency_ms": pipeline_latency_ms,
        }
        return result

    def evaluate_batch(
        self,
        dataset_entries: list,
        eval_run_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, object]:
        """Run :meth:`evaluate_one` over a list of dataset entries.

        Args:
            dataset_entries: Iterable of objects with ``query`` attribute
                (e.g. :class:`RAGEvalDataset` rows, or dicts).
            eval_run_id: UUID for the evaluation run.  If ``None``,
                a new UUID is generated.
            persist: Whether to persist results to the DB.

        Returns:
            An :class:`EvalReport`-like dict with per-query results and
            aggregate summary statistics (latency_avg_ms, mrr_avg).
        """
        eval_run_id = eval_run_id or str(uuid.uuid4())
        results: list[dict[str, object]] = []

        for entry in dataset_entries:
            if isinstance(entry, dict):
                query = entry.get("query")
                expected_answer = entry.get("expected_answer")
                expected_citations = entry.get("expected_citations")
                query_type = entry.get("query_type", "general_qa")
            else:
                query = getattr(entry, "query", None)
                expected_answer = getattr(entry, "expected_answer", None)
                expected_citations = getattr(entry, "expected_citations", None)
                query_type = getattr(entry, "query_type", "general_qa")

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
                        actual_answer=result.get("answer", ""),
                        actual_citations=result.get("cited_chunk_ids") or [],
                        metrics=result.get("metrics", {}),
                        retrieval_mrr=result.get("retrieval_mrr", 0.0),
                        latency_ms=result.get("latency_ms", 0),
                    )
            except Exception as exc:
                logger.error("EvalRunner.eval error on query %r: %s", query, exc)
                results.append({
                    "query": query,
                    "error": str(exc),
                    "metrics": {"latency_ms": 0},
                    "retrieval_mrr": 0.0,
                    "latency_ms": 0,
                })

        return self._summarize(eval_run_id, results)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compute_mrr(relevant_ids: list[str], chunks: list[RetrievedChunk]) -> float:
        """Mean Reciprocal Rank — rank of first relevant chunk (1-based)."""
        if not relevant_ids:
            return 0.0
        relevant_set = set(relevant_ids)
        for rank, chunk in enumerate(chunks, start=1):
            if chunk.chunk_id in relevant_set:
                return round(1.0 / rank, 4)
        return 0.0

    @staticmethod
    def _summarize(
        eval_run_id: str,
        results: list[dict[str, object]],
    ) -> dict[str, object]:
        """Aggregate per-query results into summary statistics."""
        summary: dict[str, object] = {
            "eval_run_id": eval_run_id,
            "total": len(results),
            "errors": sum(1 for r in results if "error" in r),
        }
        # Per-metric averages over successful results (Phase 4).
        for name in (
            "faithfulness",
            "answer_relevance",
            "context_precision",
            "context_recall",
            "citation_recall",
            "groundedness",
        ):
            vals = [
                r["metrics"][name]
                for r in results
                if isinstance(r.get("metrics"), dict)
                and isinstance(r["metrics"].get(name), (int, float))
            ]
            summary[f"{name}_avg"] = round(sum(vals) / len(vals), 4) if vals else None
        passed = sum(
            1
            for r in results
            if isinstance(r.get("metrics"), dict)
            and r["metrics"]
            and all(
                isinstance(v, (int, float)) and v >= 0.5
                for v in r["metrics"].values()
            )
        )
        summary["passed"] = passed
        mrrs = [r.get("retrieval_mrr", 0.0) for r in results if isinstance(r.get("retrieval_mrr"), (int, float))]
        summary["mrr_avg"] = round(sum(mrrs) / len(mrrs), 4) if mrrs else 0.0
        summary["latency_avg_ms"] = (
            round(sum(r.get("latency_ms", 0) for r in results) / len(results), 2) if results else 0
        )
        return {"eval_run_id": eval_run_id, "results": results, "summary": summary}
