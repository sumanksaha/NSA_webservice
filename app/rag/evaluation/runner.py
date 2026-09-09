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
from dataclasses import dataclass, field

from app.rag.evaluation.metrics import CoverageMetrics
from app.rag.evaluation.storage import EvalStorage
from app.rag.retrieval.result import RetrievedChunk

logger = logging.getLogger(__name__)

#: Type alias for the pipeline function: query -> result dict.
PipelineFn = Callable[
    [str],
    dict[str, object],
]


@dataclass
class MetricBundle:
    """Container for evaluation results.

    Currently stores only latency and MRR; detailed per-metric scores
    are available on demand from the pipeline backend.
    """

    scores: list = field(default_factory=list)

    def to_dict(self) -> dict[str, float]:
        return {}

    def get(self, name: str) -> float | None:
        return None


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

        Returns a result dict with basic metadata (latency, MRR).
        Does **not** compute detailed per-metric scores — use
        :meth:`evaluate_batch` with a custom metric backend for that.

        Returns:
            A dict with keys: ``query``, ``answer``, ``retrieved_chunks``,
            ``cited_chunk_ids``, ``metrics`` (lightweight), ``retrieval_mrr``,
            ``latency_ms``.
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

        result: dict[str, object] = {
            "query": query,
            "query_type": query_type,
            "answer": answer,
            "retrieved_chunks": [c.to_dict() for c in chunks],
            "cited_chunk_ids": cited_ids or [],
            "metrics": {
                "latency_ms": pipeline_latency_ms,
                "coverage": coverage.to_dict(),
            },
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
            query = getattr(entry, "query", entry.get("query")) if not isinstance(entry, dict) else entry["query"]

            try:
                result = self.evaluate_one(query=query)
                results.append(result)

                if persist:
                    self.storage.save_result(
                        eval_run_id=eval_run_id,
                        query=query,
                        expected_answer=result.get("answer", ""),
                        actual_answer=result["answer"],
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
        metric_names = ["latency_ms"]
        summary: dict[str, object] = {
            "eval_run_id": eval_run_id,
            "total": len(results),
            "errors": sum(1 for r in results if "error" in r),
        }
        for name in metric_names:
            vals = [r.get(name, 0) for r in results if isinstance(r.get(name), (int, float))]
            summary[f"{name}_avg"] = round(sum(vals) / len(vals), 2) if vals else None
        mrrs = [r.get("retrieval_mrr", 0.0) for r in results if isinstance(r.get("retrieval_mrr"), (int, float))]
        summary["mrr_avg"] = round(sum(mrrs) / len(mrrs), 4) if mrrs else 0.0
        summary["latency_avg_ms"] = (
            round(sum(r.get("latency_ms", 0) for r in results) / len(results), 2) if results else 0
        )
        return {"eval_run_id": eval_run_id, "results": results, "summary": summary}
