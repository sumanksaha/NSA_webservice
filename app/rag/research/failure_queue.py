"""Failure queue — the autoresearch input list.

Every question the Legal RAG answered badly lands here: low human answer
ratings (``POST /api/rag/feedback``), and later the failure detector that
sweeps ``rag_query_log`` (abstentions, hallucinations, retry exhaustion) and
offline benchmark failures.  The next autoresearch session drains
:func:`open_targets` first, before touching the benchmark.

Deduplication is by ``query_hash`` — SHA-256 over the *normalized* query —
so a question that keeps failing accumulates ``occurrences`` on one row
instead of piling up.  Frequency is the priority signal the orchestrator
should sort on.

Failure classes (``failure_class``) are the lane the target belongs to:

* ``evidence_missing`` — the corpus/answerability check says the answer is
  not establishable from retrieved evidence (corpus-gap lane).
* ``model_wrong`` — evidence was available, the legal position is wrong
  (reasoning lane: prompt conditioning, multi-hop, KG reasoner).
* ``context_assembly`` — evidence existed in the pool but never reached the
  prompt.
* ``ungrounded`` / ``citationless`` — verification failures.
* ``evaluation`` — suspected grader/reference narrowness (evaluator lane).
* ``pipeline_error`` — the pipeline itself failed.

Deterministic; no model calls.  All DB access goes through the plain
SQLAlchemy session so the module works inside and outside a request.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.extensions import db
from app.models.rag import RAGResearchTarget

__all__ = [
    "DEFAULT_FAILURE_CLASS",
    "FEEDBACK_REASONS",
    "REASON_TO_FAILURE_CLASS",
    "enqueue_from_feedback",
    "enqueue_research_target",
    "normalize_query",
    "open_targets",
    "query_hash",
]

#: Checkbox reasons offered in the rating UI.  They mirror the five measured
#: generation failure modes in the human audit (proviso blindness, definition
#: anchoring, conjunction drift, penalty incompleteness, polarity inversion)
#: plus the verification failure modes.
FEEDBACK_REASONS: tuple[str, ...] = (
    "wrong_section",  # wrong primary provision cited
    "missed_proviso",  # proviso/exception ignored
    "definition_anchoring",  # anchored on a definition, not the operative clause
    "conjunction_drift",  # AND requirements read as OR
    "penalty_incomplete",  # base penalty only, continuing fines omitted
    "polarity_inverted",  # exception read as permission (or vice versa)
    "hallucinated_content",  # asserts something the corpus does not say
    "missing_citation",  # answer has no/invalid citations
    "incomplete",  # a required element of the answer is missing
    "irrelevant_retrieval",  # retrieved context was off-topic
    "other",
)

#: Human reason -> failure class.  Unlisted reasons fall back to
#: :data:`DEFAULT_FAILURE_CLASS`: a wrong legal position is the common case
#: for a low rating, and the retrieval-vs-reasoning split is decided at
#: triage (by re-checking the stored pool), not guessed from a checkbox.
REASON_TO_FAILURE_CLASS: dict[str, str] = {
    "irrelevant_retrieval": "context_assembly",
    "hallucinated_content": "ungrounded",
    "missing_citation": "citationless",
}

DEFAULT_FAILURE_CLASS = "model_wrong"

_WS_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_query(query: str) -> str:
    """Lowercase, de-punctuated, whitespace-collapsed form used for hashing."""
    text = str(query or "").strip().lower()
    text = _NONWORD_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def query_hash(query: str) -> str:
    """SHA-256 hex digest of the normalized query (dedup key)."""
    return hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()


def _merge_detail(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge a new detail payload into a target's running detail bag.

    Keeps the worst rating seen (``min_rating``), the last rating/comment,
    the union of failure reasons (capped), and a short tail of
    ``query_log_ids`` for drill-down.
    """
    detail: dict[str, Any] = dict(existing or {})

    detail["ratings"] = int(detail.get("ratings", 0)) + 1
    new_rating = incoming.get("rating")
    if new_rating is not None:
        prev_min = detail.get("min_rating")
        detail["min_rating"] = new_rating if prev_min is None else min(prev_min, new_rating)
        detail["last_rating"] = new_rating
    if incoming.get("comment"):
        detail["last_comment"] = str(incoming["comment"])

    reasons = set(detail.get("failure_reasons") or [])
    reasons.update(incoming.get("failure_reasons") or [])
    detail["failure_reasons"] = sorted(reasons)[:10]

    log_ids = list(detail.get("query_log_ids") or [])
    if incoming.get("query_log_id"):
        log_ids.append(str(incoming["query_log_id"]))
        detail["query_log_ids"] = log_ids[-5:]

    for key in ("failure_class", "source", "pipeline", "query_type"):
        if incoming.get(key):
            detail[key] = incoming[key]
    return detail


def enqueue_research_target(
    query: str,
    *,
    failure_class: str,
    query_type: str | None = None,
    source: str = "human_rating",
    detail: dict[str, Any] | None = None,
    query_log_id: str | None = None,
) -> tuple[RAGResearchTarget, bool]:
    """Open a research target for *query*, or refresh it if one exists.

    Args:
        query: The raw question text.
        failure_class: Lane the target belongs to (see module docstring).
        query_type: Classified query type when known.
        source: What opened the target — ``human_rating`` / ``detector`` /
            ``eval``.
        detail: Free-form JSON payload merged into the target's detail bag.
        query_log_id: ``RAGQueryLog`` id of the failing answer, when logged.

    Returns:
        ``(target, created)`` — ``created`` is ``False`` when an existing
        target was found and its ``occurrences`` incremented.

    Raises:
        Exception: on DB failure (the caller decides how to degrade).
    """
    digest = query_hash(query)
    target = db.session.query(RAGResearchTarget).filter_by(query_hash=digest).first()
    incoming = {
        "failure_class": failure_class,
        "source": source,
        "query_type": query_type,
        "query_log_id": query_log_id,
        **(detail or {}),
    }

    if target is None:
        target = RAGResearchTarget(
            query_hash=digest,
            query=str(query).strip(),
            query_type=query_type,
            failure_class=failure_class,
            source=source,
            detail=_merge_detail(None, incoming),
            occurrences=1,
            status="open",
        )
        db.session.add(target)
        created = True
    else:
        # Re-open a resolved/rejected target if the same question fails
        # again — a regression is a new research item, not a closed one.
        target.occurrences = int(target.occurrences or 0) + 1
        target.detail = _merge_detail(target.detail, incoming)
        if target.status in ("resolved", "rejected"):
            target.status = "open"
            target.resolution = None
        created = False

    db.session.commit()
    return target, created


def enqueue_from_feedback(
    feedback: Any,
    *,
    max_rating: int | None = None,
) -> tuple[RAGResearchTarget | None, str]:
    """Queue a :class:`RAGAnswerFeedback` row as a research target.

    A rating at or below *max_rating* opens/refreshes the target; anything
    better is stored for trend analysis only and returns ``(None,
    "rating_above_threshold")``.

    Args:
        feedback: The feedback row (duck-typed: needs ``query``, ``rating``,
            and optionally ``query_type``, ``failure_reasons``, ``comment``,
            ``query_log_id``, ``pipeline``).
        max_rating: Override the configured threshold
            (``cfg.feedback_enqueue_max_rating``).

    Returns:
        ``(target, outcome)`` where outcome is ``queued``,
        ``rating_above_threshold``, or ``feedback_enqueue_failed`` (best
        effort — a queue failure never fails the rating itself).
    """
    from app.shared.config import cfg

    threshold = cfg.feedback_enqueue_max_rating if max_rating is None else max_rating

    rating = getattr(feedback, "rating", None)
    if rating is None or int(rating) > int(threshold):
        return None, "rating_above_threshold"

    reasons = [r for r in (getattr(feedback, "failure_reasons", None) or []) if r in REASON_TO_FAILURE_CLASS]
    failure_class = REASON_TO_FAILURE_CLASS.get(reasons[0], DEFAULT_FAILURE_CLASS) if reasons else DEFAULT_FAILURE_CLASS

    try:
        target, _created = enqueue_research_target(
            feedback.query,
            failure_class=failure_class,
            query_type=getattr(feedback, "query_type", None),
            source="human_rating",
            detail={
                "rating": int(rating),
                "comment": getattr(feedback, "comment", None),
                "failure_reasons": list(getattr(feedback, "failure_reasons", None) or []),
                "pipeline": getattr(feedback, "pipeline", None),
            },
            query_log_id=getattr(feedback, "query_log_id", None),
        )
    except Exception:  # pragma: no cover - defensive: queue failure is non-fatal
        return None, "feedback_enqueue_failed"
    return target, "queued"


def open_targets(limit: int = 50) -> list[RAGResearchTarget]:
    """Open research targets, most-frequent first (orchestrator input).

    Failed/high-signal first: the highest ``occurrences`` wins, ties broken
    by age so a long-standing single failure is not starved.
    """
    targets: list[RAGResearchTarget] = (
        db.session
        .query(RAGResearchTarget)
        .filter_by(status="open")
        .order_by(RAGResearchTarget.occurrences.desc(), RAGResearchTarget.created_at.asc())
        .limit(limit)
        .all()
    )
    return targets
