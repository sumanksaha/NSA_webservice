"""QStash publish helper with graceful fallback to synchronous execution.

QStash (Upstash) is a serverless HTTP message queue: instead of a broker +
worker, you POST a message to QStash and QStash POSTs it to your public
webhook endpoint. This lets the app run heavy tasks (PDF generation) without
a persistent background worker — which the Render free tier does not support.

If QStash is not configured (missing env vars) or the publish call fails, this
module falls back to running the task synchronously via Celery's ``.apply()``
so work is never silently lost (the same behavior as before QStash).
"""

import hashlib
import importlib
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Logical task name -> (module path, attribute). Single source of truth used
# by both the publish helper and the webhook endpoint.
TASK_REGISTRY: dict[str, tuple[str, str]] = {
    "generate_bill_pdf": ("app.bill_generator.tasks", "generate_bill_pdf"),
    "generate_case_file_pdf": ("app.case_file_generator.tasks", "generate_case_file_pdf"),
    "run_ocr_extraction": ("app.inspection.tasks", "run_ocr_extraction"),
    "backup_redundant_sheets": ("app.services.backup_coordinator", "run_backup"),
    # Agent A Phase 1 (Day 4): QStash-scheduled daily corpus ingestion.
    "ingest_corpus": ("app.rag.tasks", "ingest_corpus_task"),
    # Phase 14: Neo4j Aura knowledge-graph sync (async via QStash webhook).
    "sync_kg_to_neo4j": ("app.knowledge_graph.tasks", "sync_kg_to_neo4j"),
}

# Path (relative to PUBLIC_BASE_URL) where the webhook accepts QStash deliveries.
WEBHOOK_PATH = "/tasks/run"
# Path where QStash sends failure callbacks after exhausting all retries.
# (DLQ pattern � without this, a permanently-failed message leaves the Redis
# status stuck at "pending" forever with no signal to operators.)
FAILURE_CALLBACK_PATH = "/tasks/failed"

# QStash schedule endpoint for recurring tasks.
# Docs: https://upstash.com/docs/qstash/features/schedules
_SCHEDULE_ENDPOINT = "https://qstash.upstash.io/v2/schedules"

# Redis keys + TTL for the task-status store (frontend polling).
TASK_STATUS_KEY = "qstash:task:{message_id}"
TASK_STATUS_TTL_SECONDS = 24 * 60 * 60  # 24h — plenty for polling after an upload

_redis_client = None


def _get_redis():
    """Lazily build a shared Redis client from REDIS_URL (decode responses as str)."""
    global _redis_client
    if _redis_client is None:
        import redis

        _redis_client = redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
    return _redis_client


def store_task_status(
    message_id: str,
    status: str,
    *,
    task_name: str | None = None,
    result: Any = None,
    error: str | None = None,
) -> None:
    """Best-effort: persist task status to Redis under ``TASK_STATUS_KEY``.

    Never raises — status tracking is auxiliary; a Redis hiccup must not
    break task execution or the webhook response.
    """
    try:
        record = {
            "status": status,
            "task": task_name,
            "result": result,
            "error": error,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        _get_redis().set(
            TASK_STATUS_KEY.format(message_id=message_id),
            json.dumps(record, default=str),
            ex=TASK_STATUS_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning("store_task_status failed for %s: %s", message_id, exc)


def get_task_status(message_id: str) -> tuple[bool, dict | None]:
    """Return ``(found, record)`` from the Redis status store.

    ``found`` is False when the key is absent or Redis is unavailable.
    """
    try:
        raw = _get_redis().get(TASK_STATUS_KEY.format(message_id=message_id))
        if raw is None:
            return False, None
        return True, json.loads(raw)
    except Exception as exc:
        logger.warning("get_task_status failed for %s: %s", message_id, exc)
        return False, None


def resolve_task(task_name: str):
    """Import and return the task callable for ``task_name``."""
    module_path, attr = TASK_REGISTRY[task_name]
    module = importlib.import_module(module_path)
    return getattr(module, attr)


def qstash_configured() -> bool:
    """True when every env var required to publish to QStash is present."""
    return bool(
        os.environ.get("QSTASH_TOKEN")
        and os.environ.get("QSTASH_CURRENT_SIGNING_KEY")
        and os.environ.get("QSTASH_NEXT_SIGNING_KEY")
        and os.environ.get("PUBLIC_BASE_URL")
    )


def _webhook_url(task_name: str) -> str:
    base = os.environ["PUBLIC_BASE_URL"].rstrip("/")
    return f"{base}{WEBHOOK_PATH}/{task_name}"


def _failure_callback_url(task_name: str) -> str:
    base = os.environ["PUBLIC_BASE_URL"].rstrip("/")
    return f"{base}{FAILURE_CALLBACK_PATH}/{task_name}"


def make_dedup_key(task_name: str, record_id: int | str, payload: dict) -> str:
    """Build a dedup key scoped to the task + record + payload content.

    Including a short hash of the JSON-serialized payload means a legitimate
    regeneration with *different* data is NOT deduplicated, while identical
    double-submits (the same form POSTed twice) still collapse within QStash's
    dedup window.
    """
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:10]
    return f"{task_name}-{record_id}-{digest}"


def publish_task(task_name: str, payload: dict, *, dedup_key: str | None = None) -> dict[str, Any]:
    """Publish ``payload`` to QStash; fall back to synchronous ``.apply()``.

    Returns a dict with either:
        ``{"mode": "async", "message_id": str}`` — enqueued on QStash, or
        ``{"mode": "sync", "result": Any}`` — executed inline (fallback).

    Async messages are delivered by QStash to
    ``POST <PUBLIC_BASE_URL>/tasks/run/<task_name>`` where the webhook
    blueprint executes them and returns the result.
    """
    if task_name not in TASK_REGISTRY:
        raise ValueError(f"Unknown task: {task_name}")

    if qstash_configured():
        try:
            from qstash import QStash

            client = QStash(token=os.environ["QSTASH_TOKEN"])
            # timeout=120 is QStash's delivery-wait ceiling. The REAL ceiling for
            # the webhook execution is the web service's request timeout (free
            # tier ~30s) — keep task runtime under that or QStash will re-deliver.
            response = client.message.publish_json(
                url=_webhook_url(task_name),
                body=payload,
                retries=3,
                timeout=120,
                deduplication_id=dedup_key,
                failure_callback=_failure_callback_url(task_name),
            )
            if isinstance(response, list):
                raise TypeError(f"QStash returned a batch list, expected single message: {response}")
            logger.info(
                "Published task %s to QStash (message_id=%s)",
                task_name,
                response.message_id,
            )
            store_task_status(response.message_id, "pending", task_name=task_name)
            return {
                "mode": "async",
                "message_id": response.message_id,
                "deduplicated": response.deduplicated,
            }
        except Exception as exc:
            logger.warning("QStash publish failed for %s (%s) — falling back to sync", task_name, exc)

    # Synchronous fallback — matches pre-QStash behavior.
    logger.warning(
        "QStash not fully configured (QSTASH_TOKEN / QSTASH_CURRENT_SIGNING_KEY / "
        "QSTASH_NEXT_SIGNING_KEY / PUBLIC_BASE_URL) — executing %s synchronously",
        task_name,
    )
    result = resolve_task(task_name).apply(kwargs=payload).result
    return {"mode": "sync", "result": result}


def publish_recurring(
    task_name: str,
    schedule: str,
    *,
    payload: dict | None = None,
    dedup_key: str | None = None,
) -> dict[str, Any]:
    """Register a recurring (scheduled) task on QStash.

    Unlike ``publish_task``, this does NOT fall back to synchronous execution —
    scheduled tasks only run on QStash's scheduler, which requires a paid
    QStash plan.  If QStash is not configured, a warning is logged and the
    function returns ``{"mode": "disabled"}``.

    Args:
        task_name: Key in ``TASK_REGISTRY``.
        schedule: Cron-like expression (e.g. ``"0 2 * * *"`` for daily 02:00 UTC).
        payload: Optional payload to send to the webhook on each run.
        dedup_key: Optional deduplication key.

    Returns:
        ``{"mode": "scheduled", "schedule_id": str}`` on success,
        ``{"mode": "disabled"}`` if QStash is not configured.
    """
    if task_name not in TASK_REGISTRY:
        raise ValueError(f"Unknown task: {task_name}")

    if not qstash_configured():
        logger.warning(
            "QStash not configured — recurring task %s not scheduled. "
            "Set QSTASH_TOKEN / QSTASH_CURRENT_SIGNING_KEY / QSTASH_NEXT_SIGNING_KEY "
            "/ PUBLIC_BASE_URL env vars to enable.",
            task_name,
        )
        return {"mode": "disabled"}

    try:
        import httpx

        base = os.environ["PUBLIC_BASE_URL"].strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = f"https://{base}"
        webhook_url = f"{base}{WEBHOOK_PATH}/{task_name}"
        failure_url = f"{base}{FAILURE_CALLBACK_PATH}/{task_name}"

        headers = {
            "Authorization": f"Bearer {os.environ['QSTASH_TOKEN']}",
            "Content-Type": "application/json",
        }
        body = {
            "destination": webhook_url,
            "cron": schedule,
            "body": json.dumps(payload or {}),
            "method": "POST",
            "failureCallback": failure_url,
        }
        resp = httpx.post(
            _SCHEDULE_ENDPOINT,
            headers=headers,
            json=body,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "mode": "scheduled",
                "schedule_id": data.get("scheduleId"),
            }
        logger.warning(
            "QStash schedule creation failed (status %d): %s",
            resp.status_code,
            resp.text[:200],
        )
        return {"mode": "disabled"}
    except Exception as exc:
        logger.warning("QStash recurring schedule failed for %s (%s)", task_name, exc)
        return {"mode": "disabled"}
