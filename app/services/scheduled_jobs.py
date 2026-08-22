"""Scheduled background jobs — QStash recurring schedules.

One deep module owning *what* runs on a schedule, separate from *how* it is
published (:func:`app.utils.qstash_client.publish_recurring`). ``create_app()``
calls :func:`register_all` once at startup. Tests enumerate :data:`JOBS` and
call :func:`register_all` with a fake publisher — no factory import, no
network, no import-time side effects.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledJob:
    """One declaratively-defined recurring job.

    Attributes:
        name: Task key in ``app.utils.qstash_client.TASK_REGISTRY``.
        flag_key: Config key enabling the schedule (opt-in boolean).
        cron_key: Config key holding the cron expression (``None`` = fixed).
        default_cron: Schedule used when ``cron_key`` is unset/default.
        description: One-line human description (documentation/introspection).
    """

    name: str
    flag_key: str
    cron_key: str | None
    default_cron: str
    description: str


#: The job registry — single source of truth for startup schedules.
JOBS: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        name="backup_redundant_sheets",
        flag_key="ENABLE_BACKUP_SCHEDULE",
        cron_key=None,
        default_cron="0 2 * * *",  # daily at 02:00 UTC
        description="Daily multi-target backup (Priority 7 redundancy).",
    ),
    ScheduledJob(
        name="ingest_corpus",
        flag_key="RAG_ENABLE_INGESTION_SCHEDULE",
        cron_key="RAG_INGESTION_CRON",
        default_cron="0 3 * * *",  # daily 03:00 UTC
        description="Daily RAG corpus ingestion against RAG_CORPUS_DIR.",
    ),
)


def register_all(app: Any, publisher: Any = None) -> list[dict[str, Any]]:
    """Register every enabled job via *publisher* (default: QStash).

    Returns one dict per enabled job: ``{"job", "status", "result"|"error"}``.
    Disabled jobs are omitted entirely. Best-effort: a publish failure never
    blocks startup — it is logged and reported in the result.
    """
    from app.shared.config import cfg

    if publisher is None:
        from app.utils.qstash_client import publish_recurring as publisher

    results: list[dict[str, Any]] = []
    for job in JOBS:
        if not cfg.get_bool(job.flag_key):
            continue
        cron = cfg.get_str(job.cron_key, job.default_cron) if job.cron_key else job.default_cron
        payload: dict[str, Any] = {}
        if job.name == "ingest_corpus":
            corpus_dir = os.environ.get("RAG_CORPUS_DIR")
            if not corpus_dir:
                logger.info("Ingestion schedule enabled but RAG_CORPUS_DIR unset — skipped")
                continue
            payload = {"corpus_dir": corpus_dir}
        try:
            result = publisher(job.name, schedule=cron, payload=payload)
            results.append({"job": job.name, "status": "registered", "result": result})
            logger.info("Registered %s (%s): %s", job.name, cron, result)
        except Exception as e:
            results.append({"job": job.name, "status": "error", "error": str(e)})
            logger.warning("QStash schedule registration failed for %s: %s", job.name, e)
    return results


__all__ = ["JOBS", "ScheduledJob", "register_all"]
