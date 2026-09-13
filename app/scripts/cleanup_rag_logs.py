"""3.7 — RAGQueryLog retention / archival policy.

Deletes log entries older than RAG_LOG_RETENTION_DAYS (default 90).
Best-effort: never raises, logs all actions.
Dispatched via QStash (``cleanup_rag_query_logs`` in TASK_REGISTRY,
weekly schedule in ScheduledJobs) or run manually: ``python -m ...``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.extensions import db
from app.models.rag import RAGQueryLog
from app.shared.config import cfg

logger = logging.getLogger(__name__)

RETENTION_DAYS = getattr(cfg, "rag_log_retention_days", 90)


def cleanup_rag_query_logs(days: int = RETENTION_DAYS) -> dict[str, int]:
    """Delete RAGQueryLog rows older than *days* (default from config).

    Returns:
        {"deleted": int, "retained": int, "days": days}
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    try:
        result = db.session.query(RAGQueryLog).filter(RAGQueryLog.created_at < cutoff).delete(synchronize_session=False)
        db.session.commit()
        logger.info("RAGQueryLog cleanup: %d rows > %d days deleted (cutoff=%s)", result, days, cutoff.isoformat())
        retained = db.session.query(RAGQueryLog).count()
        return {"deleted": result, "retained": retained, "days": days}
    except Exception as exc:
        db.session.rollback()
        logger.error("RAGQueryLog cleanup failed: %s", exc)
        # Return ints only as per type hint
        return {"deleted": 0, "retained": -1, "days": days}


if __name__ == "__main__":
    result = cleanup_rag_query_logs()
    print(result)  # noqa: T201 — CLI entry point prints its result
