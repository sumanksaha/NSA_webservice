"""Celery integration for the application.

Exports two things:
- ``celery`` — a bare Celery instance that can be safely imported by
  task modules without triggering the Flask app factory (avoids circular
  imports).  It gets its broker/backend defaults at import time but is
  fully configured later via ``make_celery(app)``.
- ``make_celery(app)`` — called from the Flask app factory to override
  the broker/backend with the app's ``REDIS_URL``, merge Flask config,
  and install a ``ContextTask`` that wraps task execution inside an app
  context.
"""

import logging
import ssl
from importlib import import_module
from urllib.parse import urlparse, urlunparse

from celery import Celery

logger = logging.getLogger(__name__)


def _normalize_redis_url(url: str) -> str:
    """Ensure ``rediss://`` URLs carry ``ssl_cert_reqs`` for Celery.

    Celery's Redis result backend (unlike ``redis.from_url()`` used by
    ``qstash_client._get_redis``) *requires* the ``ssl_cert_reqs`` query
    parameter on ``rediss://`` URLs.  Without it, ``.apply()`` and any
    result-backend operation raise::

        ValueError: A rediss:// URL must have parameter ssl_cert_reqs
        ...

    This crashes every sync-fallback invocation and every QStash webhook
    delivery (which both use ``Task.run()`` via ``_run_task_inline`` — though
    the run() fix means the result backend is no longer touched by those paths,
    Celery workers and ``celery -A celery_app beat`` still need a valid URL).
    """
    if not url or not url.startswith("rediss://"):
        return url
    parsed = urlparse(url)
    if "ssl_cert_reqs" in parsed.query:
        return url
    sep = "&" if parsed.query else ""
    new_query = f"{parsed.query}{sep}ssl_cert_reqs={ssl.CERT_REQUIRED}"
    return urlunparse(parsed._replace(query=new_query))


# Celery-only task modules — no QStash webhook equivalent (e.g. beat-scheduled
# jobs like the daily DB snapshot). Modules holding QStash-dispatchable tasks
# are NOT listed here: they are derived inside make_celery() from
# ``app.utils.qstash_client.TASK_REGISTRY`` (the single source of truth), so a
# new QStash task registers its Celery module automatically and the two lists
# cannot drift.
CELERY_ONLY_TASK_MODULES = [
    "app.food_cell.tasks",
    "app.ai_assistant.tasks",
    "app.utils.backup",
]

# Standalone instance for task decoration — safe to import anywhere.
# Broker/backend will be overridden by make_celery(app) at runtime.
celery = Celery(
    "nsa_webservice",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
)


def make_celery(app):
    """Configure the global ``celery`` instance from the Flask app.

    Sets broker and backend to ``app.config['REDIS_URL']``, merges Flask
    app config into Celery config, and installs a ``ContextTask`` subclass
    that wraps task execution inside a Flask app context so all tasks can
    safely access ``db.session``, ``current_app``, and other extensions.
    """
    celery.conf.update(
        broker_url=_normalize_redis_url(app.config["REDIS_URL"]),
        result_backend=_normalize_redis_url(app.config["REDIS_URL"]),
    )

    # Merge Flask app config into Celery config (Celery ignores unknown keys)
    celery.conf.update(app.config)

    # Celery-specific settings
    celery.conf.update(
        result_expires=3600,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
    )

    # Phase 16: daily full-database snapshot at midnight UTC. The handler
    # lives in app/utils/backup.py; snapshots land in instance/backups/.
    from celery.schedules import crontab

    celery.conf.beat_schedule = {
        "daily-db-snapshot": {
            "task": "app.utils.backup.create_daily_db_snapshot_task",
            "schedule": crontab(hour=0, minute=0),
        },
    }

    class ContextTask(celery.Task):
        """Task subclass that wraps call in a Flask app context."""

        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask

    # Register every task module now so a ``celery -A app.celery worker``
    # process (which boots via this factory) knows about all tasks.  The
    # bill/case-file PDF tasks are only imported lazily inside route handlers,
    # so without this explicit import the worker would silently never run them.
    # QStash-dispatchable modules are derived from TASK_REGISTRY (single source
    # of truth) — adding a QStash task registers it with both transports; each
    # module is imported defensively: a single failing module must not disable
    # the whole task queue (matches the codebase's lazy-import /
    # graceful-degradation philosophy).
    from app.utils.qstash_client import TASK_REGISTRY

    task_modules = sorted({module for module, _attr in TASK_REGISTRY.values()} | set(CELERY_ONLY_TASK_MODULES))
    for module in task_modules:
        try:
            import_module(module)
        except ImportError as exc:
            logger.warning("Celery task module %s failed to import: %s", module, exc)

    return celery
