"""Shared helpers for the agent graph nodes.

Timing, state accessors, token telemetry, budget accounting, and Flask
app-context capture — the cross-cutting plumbing every node module needs.
No node logic lives here.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.rag.agent.thresholds import (
    EVIDENCE_COVERAGE_SYNTHESIZE_AT_LEAST as _COVERAGE_FLOOR,
)
from app.rag.agent.thresholds import (
    GROUNDEDNESS_RETRY_BELOW as GROUNDEDNESS_THRESHOLD,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GROUNDEDNESS_THRESHOLD",
    "_BUDGET_DEFAULTS",
    "_COVERAGE_FLOOR",
    "_caller_app",
    "_consume_budget",
    "_enrich_audit_entry",
    "_est_tokens",
    "_ms",
    "_query_for_retrieval",
    "_safe_int",
    "_task_token_cost",
    "logger",
]


def _ms(start: float) -> int:
    """Elapsed milliseconds since ``start``, safe for OS clock adjustments."""
    try:
        return int((time.monotonic() - start) * 1000)
    except (ValueError, TypeError):
        return 0


# NOTE: GROUNDEDNESS_THRESHOLD (expand-and-retry trigger, plan §5.3) and
# _COVERAGE_FLOOR (DAG synthesize/abstain floor) are imported from
# app.rag.agent.thresholds — the single tuning point for every
# routing/gating constant.  The UPPER_CASE aliases stay so existing imports
# keep working.


def _query_for_retrieval(state: dict[str, Any]) -> str:
    """The query to retrieve with.

    Priority: failure-aware targeted query (Phase 2.6 retry) > expanded
    query (groundedness retry) > original query.  Without the targeted
    branch, retries re-retrieved with the *same* query — the Phase 0
    defect where ``targeted_retry`` was decorative.
    """
    return state.get("targeted_query") or state.get("expanded_query") or state.get("query") or ""


def _safe_int(value: Any, default: int) -> int:
    """Coerce to int with a fallback (state values may be None/str)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _est_tokens(*texts: Any) -> int:
    """Estimated token count for the given texts (Phase 4 cost telemetry).

    Uses the centralized :class:`TokenCounter` (tiktoken when available,
    word-count fallback otherwise) — deterministic and cheap.
    """
    from app.rag.verification.token_counter import TokenCounter

    counter = TokenCounter()
    return sum(counter.estimate(str(t)) for t in texts if t)


def _task_token_cost(task: Any, chunks: list[dict[str, Any]] | None) -> int:
    """Estimated token cost of a task's retrieval round (Phase 4 telemetry).

    Counts the task question plus the retrieved evidence text.
    """
    texts = [str(getattr(task, "question", "") or "")]
    texts.extend(str(c.get("text") or "") for c in chunks or [] if isinstance(c, dict))
    return _est_tokens(*texts)


def _enrich_audit_entry(entry: dict[str, Any], **telemetry: Any) -> dict[str, Any]:
    """Attach cost telemetry to an audit entry's detail block (Phase 4).

    Adds ``token_cost`` / ``prompt_tokens`` / ``completion_tokens`` keys to
    ``entry["detail"]`` for every non-None value passed.  Entries already
    carry ``latency_ms``; this completes the per-stage cost picture the
    plan's observability item (§16) calls for.
    """
    detail = entry.setdefault("detail", {})
    for key, value in telemetry.items():
        if value is not None:
            detail[key] = value
    return entry


# Historical budget defaults, used when a state carries no budget dict
# (unit-test fixtures) so consumption never raises.
_BUDGET_DEFAULTS = {
    "max_tasks": 10,
    "max_retrieval_rounds": 5,
    "max_documents": 50,
    "max_llm_calls": 20,
}


def _consume_budget(
    state: dict[str, Any],
    *,
    retrieval_rounds: int = 0,
    documents: int = 0,
    llm_calls: int = 0,
) -> dict[str, Any]:
    """Increment the budget's consumed counters (Phase 3).

    Counters are clamped at their caps so an oversized batch (e.g. a
    retrieval returning more chunks than ``max_documents``) cannot push a
    counter past its tier ceiling.  Missing caps/counters default to the
    historical values, so nodes can consume budget on any state shape.
    """
    budget = dict(state.get("budget") or {})
    for key, default in _BUDGET_DEFAULTS.items():
        budget.setdefault(key, default)
    for key in ("consumed_tasks", "consumed_retrieval_rounds", "consumed_documents", "consumed_llm_calls"):
        budget.setdefault(key, 0)

    def _bump(counter: str, amount: int, cap_key: str) -> None:
        if amount <= 0:
            return
        cap = _safe_int(budget.get(cap_key), _BUDGET_DEFAULTS[cap_key])
        budget[counter] = min(_safe_int(budget.get(counter), 0) + amount, cap)

    _bump("consumed_retrieval_rounds", retrieval_rounds, "max_retrieval_rounds")
    _bump("consumed_documents", documents, "max_documents")
    _bump("consumed_llm_calls", llm_calls, "max_llm_calls")
    return budget


def _caller_app() -> Any | None:
    """The Flask app owning the current request thread, if any.

    ``execute_task_node`` fans work out to a ``ThreadPoolExecutor`` whose
    threads start with no Flask app context — ``run_retrieval_pipeline``
    (Pattern A config, ``db.session``, ``current_app``) would raise
    "Working outside of application context" or silently fall back to env
    config there.  Capturing the app object on the calling thread lets each
    worker push its own ``app_context()`` (a fresh context per worker —
    sharing one context object across threads is unsafe).
    Returns ``None`` outside an app context (unit tests, scripts); workers
    then run unscoped, as before.
    """
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            return current_app._get_current_object()
    except Exception:
        pass
    return None
