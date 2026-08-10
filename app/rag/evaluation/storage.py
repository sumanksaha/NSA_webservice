"""Evaluation storage — persist evaluation results and datasets.

Wraps the ``RAGEvalResult`` and ``RAGEvalDataset`` ORM models with a
clean service API, following the best-effort persistence + hash-chained
audit pattern from :class:`app.rag.retrieval.logger.RetrievalLogger`
and :class:`app.rag.generation.logger.GenerationLogger`.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.extensions import db
from app.models.rag import RAGEvalDataset, RAGEvalResult
from app.services.audit import log_audit

logger = logging.getLogger(__name__)


class EvalStorage:
    """Persist evaluation results and datasets.

    All writes are best-effort: failures are logged and swallowed so
    evaluation is never blocked by storage errors.
    """

    def __init__(self, actor: str = "rag_eval_runner") -> None:
        self.actor = actor

    # ------------------------------------------------------------------ #
    # RAGEvalResult
    # ------------------------------------------------------------------ #

    def save_result(
        self,
        eval_run_id: str,
        query: str,
        *,
        expected_answer: str | None = None,
        expected_citations: list[str] | None = None,
        actual_answer: str | None = None,
        actual_citations: list[str] | None = None,
        metrics: dict[str, float] | None = None,
        retrieval_mrr: float | None = None,
        latency_ms: int | None = None,
    ) -> RAGEvalResult | None:
        """Persist a single evaluation result row."""
        metrics = metrics or {}
        try:
            result = RAGEvalResult(
                eval_run_id=eval_run_id,
                query=query,
                expected_answer=expected_answer,
                expected_citations=expected_citations or [],
                actual_answer=actual_answer,
                actual_citations=actual_citations or [],
                faithfulness_score=metrics.get("faithfulness"),
                answer_relevance_score=metrics.get("answer_relevance"),
                context_precision_score=metrics.get("context_precision"),
                context_recall_score=metrics.get("context_recall"),
                citation_recall_score=metrics.get("citation_recall"),
                groundedness_score=metrics.get("groundedness"),
                avg_score=self._avg(metrics),
                retrieval_mrr=retrieval_mrr,
                latency_ms=latency_ms,
                passed=self._passed(metrics),
            )
            db.session.add(result)
            db.session.commit()

            self._audit(eval_run_id, query, metrics, result)
            return result
        except Exception as exc:
            logger.warning("EvalStorage.save_result failed: %s", exc)
            db.session.rollback()
            return None

    # ------------------------------------------------------------------ #
    # RAGEvalDataset
    # ------------------------------------------------------------------ #

    def save_dataset_entry(
        self,
        name: str,
        query: str,
        query_type: str,
        expected_answer: str,
        *,
        expected_section: str | None = None,
        expected_citations: list[str] | None = None,
        difficulty: str = "medium",
    ) -> RAGEvalDataset | None:
        """Create or replace a dataset entry (idempotent by query+name)."""
        try:
            entry = db.session.query(RAGEvalDataset).filter_by(
                name=name, query=query
            ).first()
            if entry is None:
                entry = RAGEvalDataset(
                    name=name,
                    query=query,
                    query_type=query_type,
                    expected_answer=expected_answer,
                    expected_section=expected_section,
                    expected_citations=expected_citations or [],
                    difficulty=difficulty,
                )
                db.session.add(entry)
            else:
                entry.query_type = query_type
                entry.expected_answer = expected_answer
                entry.expected_section = expected_section
                entry.expected_citations = expected_citations or []
                entry.difficulty = difficulty
            db.session.commit()
            return entry
        except Exception as exc:
            logger.warning("EvalStorage.save_dataset_entry failed: %s", exc)
            db.session.rollback()
            return None

    def list_dataset(self, name: str | None = None) -> list[RAGEvalDataset]:
        """List active dataset entries (optionally filtered by name)."""
        q = db.session.query(RAGEvalDataset).filter_by(is_active=True)
        if name:
            q = q.filter_by(name=name)
        return q.order_by(RAGEvalDataset.created_at.desc()).all()

    def list_results(self, eval_run_id: str) -> list[RAGEvalResult]:
        """List all result rows for an evaluation run."""
        return (
            db.session.query(RAGEvalResult).filter_by(eval_run_id=eval_run_id)
            .order_by(RAGEvalResult.created_at.asc())
            .all()
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _avg(metrics: dict[str, float]) -> float | None:
        if not metrics:
            return None
        vals = [v for v in metrics.values() if v is not None]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 4)

    @staticmethod
    def _passed(metrics: dict[str, float]) -> bool:
        """A result "passes" if all metrics >= 0.5 (configurable threshold)."""
        if not metrics:
            return False
        return all(v >= 0.5 for v in metrics.values() if v is not None)

    def _audit(
        self,
        eval_run_id: str,
        query: str,
        metrics: dict[str, float],
        result: RAGEvalResult,
    ) -> None:
        try:
            log_audit(
                entity_type="rag_eval",
                entity_id=eval_run_id,
                action="EVAL_RESULT",
                actor=self.actor,
                details={
                    "query_hash": hashlib.sha256(query.encode()).hexdigest()[:16],
                    "avg_score": result.avg_score,
                    "passed": result.passed,
                    "metrics": metrics,
                    "result_id": result.id,
                },
            )
        except Exception as audit_exc:
            logger.warning("Eval audit log failed: %s", audit_exc)
