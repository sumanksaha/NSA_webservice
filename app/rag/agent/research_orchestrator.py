"""Autoresearch session — the Phase 3 research orchestrator.

This module is the piece that turns the research *queue* into a research
*session*: the system looking at its own unanswered questions and working
on them, one bounded loop at a time.

One session does three things, in order:

1. **Sweep** (:meth:`ResearchOrchestrator.sweep_query_log`) — read
   ``rag_query_log`` rows that have not been evaluated yet and queue a
   :class:`~app.models.rag.RAGResearchTarget` for every stored failure
   signal (pipeline error, hallucination, low groundedness, abstention,
   citationless answer).  Offline, deterministic, no model calls: it reads
   what the live pipeline already recorded.  Rows are stamped
   ``research_swept_at`` so the sweep is idempotent.

2. **Drain** (:meth:`ResearchOrchestrator.run_session`) — take open targets
   highest-occurrence first, classify each into a lane from its stored
   ``failure_class``, build a targeted retrieval plan with
   :class:`~app.rag.planning.targeted_retry.TargetedRetryPlanner` (the same
   failure→arm mapping the agent's retry node uses), and re-ask the
   question through the generation pipeline.

3. **Close the loop** — score the re-asked answer with the frozen gates
   (groundedness floor, citations present, no hallucination, no abstention).
   A target that passes is marked ``resolved`` with the evidence; one that
   fails stays ``open`` with its round counter bumped, so the *next*
   session picks it up again.  ``evaluation``-lane targets (suspected
   grader narrowness) never spend LLM budget — they are flagged for human
   triage instead.

Safety, deliberately conservative for v1:

* every number that bounds spend is cfg-driven
  (``RAG_RESEARCH_SESSION_MAX_TARGETS`` / ``RAG_RESEARCH_SESSION_MAX_LLM_CALLS``),
  mirroring the discovery circuit breaker.
* a failing target never kills the session: per-target try/except records
  the error on the target and continues.
* nothing here writes to the corpus.  Evidence-missing targets are research
  *findings*; corpus ingestion stays behind the human-approval gate
  (``step3_em_fill_approved.json``) and lands with Phase 2 ingestion.
* the whole loop is gated by the ``RAG_RESEARCH_ENABLED`` master switch at
  the :func:`run_research_session` entry point.

Retrieval and generation are injected seams (defaults resolve the
production pipeline lazily at call time), so the session logic — lane
routing, scoring, resolution, budgets — is testable without Qdrant or an
LLM key.  Transport-agnostic by design: routes, Celery tasks, and CLI
scripts all call the same plain function.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.extensions import db
from app.models.rag import RAGQueryLog, RAGResearchTarget
from app.rag.agent.thresholds import GROUNDEDNESS_RETRY_BELOW
from app.rag.generation.abstention import is_abstention
from app.rag.research.failure_queue import enqueue_research_target, open_targets

logger = logging.getLogger(__name__)

__all__ = ["ResearchOrchestrator", "run_research_session"]

#: Target ``failure_class`` (set by the rating tool / detector / triage) to
#: the retrieval-failure code whose recovery strategy the targeted retry
#: planner understands.  ``None`` → plain re-ask (no failure signal).
_FAILURE_TO_RETRIEVAL_FAILURE: dict[str, str | None] = {
    "evidence_missing": "missing_provision",
    "context_assembly": "low_relevance",
    "model_wrong": "wrong_provision",
    "ungrounded": "insufficient_evidence_coverage",
    "citationless": "insufficient_authority",
    "weak_groundedness": "insufficient_evidence_coverage",
    "pipeline_error": None,
}

#: Lane that never spends LLM budget — a suspected grader/reference problem
#: is a human-triage item, not a research experiment.
LANE_EVALUATION = "evaluation"

#: Groundedness floor shared with the agent retry gate — a re-asked answer
#: below it is treated as ungrounded, exactly as the live graph treats it.
GROUNDEDNESS_FLOOR: float = GROUNDEDNESS_RETRY_BELOW


class ResearchOrchestrator:
    """Run bounded autoresearch sessions over the research queue."""

    def __init__(
        self,
        *,
        generator: Any | None = None,
        planner: Any | None = None,
    ) -> None:
        """
        Args:
            generator: Callable ``(query=..., query_type=..., top_k=...)``
                returning a generation-result dict.  Defaults to
                ``app.rag.tasks.run_generation_pipeline``, resolved lazily.
            planner: Targeted-retry planner.  Defaults to
                ``TargetedRetryPlanner()``, constructed lazily.
        """
        self._generator = generator
        self._planner = planner

    # ------------------------------------------------------------------ #
    # Session                                                             #
    # ------------------------------------------------------------------ #

    def run_session(self, *, max_targets: int | None = None, sweep: bool = True) -> dict[str, Any]:
        """Run one autoresearch session; returns a JSON-safe report.

        Args:
            max_targets: Override ``cfg.research_session_max_targets``.
            sweep: Run the query-log sweep first (default on; disable for
                session-only runs, e.g. tests or manual re-runs).
        """
        from app.shared.config import cfg

        report: dict[str, Any] = {
            "enabled": True,
            "swept": 0,
            "targets_considered": 0,
            "targets_researched": 0,
            "llm_calls_used": 0,
            "resolved": [],
            "still_open": [],
            "skipped": [],
            "skipped_detail": [],
            "errors": [],
            "actions": [],
        }

        if sweep and cfg.research_sweep_enabled:
            try:
                report["swept"] = self.sweep_query_log()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("research sweep failed (%s) — continuing with queue as-is", exc)
                report["swept"] = -1

        target_cap = max_targets if max_targets is not None else cfg.research_session_max_targets
        llm_budget = cfg.research_session_max_llm_calls
        targets = open_targets(limit=target_cap)
        report["targets_considered"] = len(targets)

        for target in targets:
            if report["llm_calls_used"] >= llm_budget:
                report["skipped"].append(target.id)
                report["skipped_detail"].append({"target_id": target.id, "reason": "llm_budget_exhausted"})
                continue
            if target.failure_class == LANE_EVALUATION:
                target.detail = {**(target.detail or {}), "needs_human_triage": True}
                db.session.commit()
                report["skipped"].append(target.id)
                report["skipped_detail"].append({"target_id": target.id, "reason": "evaluation_lane"})
                continue
            self._research_target(target, report)

        db.session.commit()
        return report

    def _research_target(self, target: RAGResearchTarget, report: dict[str, Any]) -> None:
        """Re-ask one target's question and resolve or re-open it."""
        plan = self._plan(target)
        report["actions"].append({
            "target_id": target.id,
            "query": plan.query,
            "arm": plan.arm,
            "strategy": plan.strategy,
        })
        try:
            result = self._generate(query=plan.query, query_type=target.query_type, top_k=plan.top_k_override)
        except Exception as exc:
            logger.warning("research target %s failed to re-ask (%s)", target.id, exc)
            detail = {**(target.detail or {})}
            detail["last_error"] = str(exc)[:500]
            target.detail = detail
            db.session.commit()
            report["errors"].append({"target_id": target.id, "error": str(exc)[:500]})
            report["still_open"].append(target.id)
            return

        report["llm_calls_used"] += 1
        score = _score_answer(result)

        detail = {**(target.detail or {})}
        detail["research_rounds"] = int(detail.get("research_rounds", 0)) + 1
        detail["last_score"] = score
        detail["last_arm"] = plan.arm
        detail["last_query"] = plan.query
        target.detail = detail

        if score["passed"]:
            target.status = "resolved"
            target.resolution = (
                f"re-ask passed quality gates (groundedness={score['groundedness']:.2f}, "
                f"citations={score['citations']}, arm={plan.arm})"
            )
            detail["last_outcome"] = "recovered"
            report["resolved"].append(target.id)
        else:
            detail["last_outcome"] = "not_recovered"
            report["still_open"].append(target.id)
        db.session.commit()
        report["targets_researched"] += 1

    def _plan(self, target: RAGResearchTarget):
        """Build the targeted retry plan for one target."""
        from app.shared.config import cfg

        if self._planner is None:
            from app.rag.planning.targeted_retry import TargetedRetryPlanner

            self._planner = TargetedRetryPlanner()
        failure_code = _FAILURE_TO_RETRIEVAL_FAILURE.get(target.failure_class, "missing_provision")
        failures = [failure_code] if failure_code else []
        return self._planner.target_plan(
            target.query,
            failures,
            target.query_type or "",
            context={"top_k": cfg.context_max_chunks},
        )

    def _generate(self, *, query: str, query_type: str | None, top_k: int | None) -> dict[str, Any]:
        from app.shared.config import cfg

        if self._generator is None:
            from app.rag.tasks import run_generation_pipeline

            self._generator = run_generation_pipeline
        return self._generator(
            query=query,
            query_type=query_type or "",
            top_k=top_k or cfg.context_max_chunks,
        )

    # ------------------------------------------------------------------ #
    # Detector sweep                                                      #
    # ------------------------------------------------------------------ #

    def sweep_query_log(self, *, max_rows: int | None = None) -> int:
        """Queue research targets from unswept ``rag_query_log`` failure signals.

        Returns the number of targets queued (rows that needed no target are
        still stamped swept, so they are never re-evaluated).
        """
        from app.shared.config import cfg

        cap = max_rows if max_rows is not None else cfg.research_sweep_max_rows
        rows = (
            db.session
            .query(RAGQueryLog)
            .filter(RAGQueryLog.research_swept_at.is_(None))
            .order_by(RAGQueryLog.created_at.desc())
            .limit(cap)
            .all()
        )
        now = datetime.now(UTC)
        enqueued = 0
        for row in rows:
            signal = _detect_failure_signal(row)
            if signal:
                enqueue_research_target(
                    row.query,
                    failure_class=signal,
                    query_type=row.query_type,
                    source="detector",
                    detail={
                        "query_log_id": str(row.id),
                        "groundedness_score": row.groundedness_score,
                        "hallucination_detected": bool(row.hallucination_detected),
                        "abstained": signal == "evidence_missing" and _is_abstained(row),
                    },
                    query_log_id=str(row.id),
                )
                enqueued += 1
            row.research_swept_at = now
        db.session.commit()
        return enqueued


def _is_abstained(row: RAGQueryLog) -> bool:
    return is_abstention(row.response_text or "")


def _detect_failure_signal(row: RAGQueryLog) -> str | None:
    """Classify one logged query into a failure class, or ``None`` if satisfactory.

    Precedence: an outright pipeline error beats everything; then an
    abstention (which per the ADR-0010 audit usually names genuinely missing
    evidence, so it routes to the evidence lane for triage); then
    hallucination and low groundedness; then a citationless answer.
    """
    if row.error:
        return "pipeline_error"
    answer = row.response_text or ""
    if is_abstention(answer):
        return "evidence_missing"
    if row.hallucination_detected:
        return "ungrounded"
    groundedness = row.groundedness_score
    if groundedness is not None and groundedness < GROUNDEDNESS_FLOOR:
        return "ungrounded"
    if answer.strip() and not row.cited_chunk_ids:
        return "citationless"
    return None


def _score_answer(result: dict[str, Any]) -> dict[str, Any]:
    """Score one re-asked answer against the frozen quality gates.

    Gates: a substantive (non-abstaining) answer, no hallucination flag,
    groundedness at or above the retry floor, and at least one citation.
    Deliberately conservative — a target only resolves on evidence that the
    *pipeline itself* produced, never on a lenient reading.
    """
    answer = str(result.get("answer") or "")
    groundedness = result.get("groundedness_score")
    g = float(groundedness) if isinstance(groundedness, (int, float)) else None
    citations = result.get("citations") or []
    abstained = is_abstention(answer)
    hallucination = bool(result.get("hallucination_detected"))
    passed = not abstained and not hallucination and g is not None and g >= GROUNDEDNESS_FLOOR and len(citations) > 0
    return {
        "passed": passed,
        "groundedness": g,
        "citations": len(citations),
        "abstained": abstained,
        "hallucination": hallucination,
    }


def run_research_session(**kwargs: Any) -> dict[str, Any]:
    """Cfg-gated entry point for one autoresearch session.

    Returns a disabled report (without touching any target) when the
    ``RAG_RESEARCH_ENABLED`` master switch is off.
    """
    from app.shared.config import cfg

    if not cfg.research_enabled:
        return {
            "enabled": False,
            "reason": "research_disabled",
            "swept": 0,
            "targets_considered": 0,
            "targets_researched": 0,
            "llm_calls_used": 0,
            "resolved": [],
            "still_open": [],
            "skipped": [],
            "skipped_detail": [],
            "errors": [],
            "actions": [],
        }
    report = ResearchOrchestrator().run_session(**kwargs)
    return {"enabled": True, **report}
