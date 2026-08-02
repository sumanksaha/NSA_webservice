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
from importlib import import_module

from celery import Celery

logger = logging.getLogger(__name__)

# Task modules that must be registered.  The bill/case-file PDF tasks are
# imported lazily inside route handlers, so they are NOT registered by merely
# importing the `app` package.  We register them explicitly in make_celery()
# below (relying on Celery's ``include=``/``finalize()`` alone is unreliable:
# the lazy ``celery.task(...)`` machinery is consumed during app-factory boot,
# so a later finalize() import silently fails to register them).
TASK_MODULES = [
    "app.bill_generator.tasks",
    "app.case_file_generator.tasks",
    "app.inspection.tasks",
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
        broker_url=app.config["REDIS_URL"],
        result_backend=app.config["REDIS_URL"],
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
    # Each module is imported defensively: a single failing module must not
    # disable the whole task queue (matches the codebase's lazy-import /
    # graceful-degradation philosophy).
    for module in TASK_MODULES:
        try:
            import_module(module)
        except ImportError as exc:
            logger.warning("Celery task module %s failed to import: %s", module, exc)

    return celery
