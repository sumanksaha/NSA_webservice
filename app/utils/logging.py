"""Structured logging configuration.

Swaps the default stdlib logging configuration for a `structlog`-based setup
that emits structured records: JSON in production (machine-parseable) and
colored human-readable output in development.

Both ``structlog.get_logger()`` callers and existing ``logging.getLogger()``
/``app.logger`` callers are rendered through the same pipeline via
``structlog.stdlib.ProcessorFormatter``, so there are no "plain text" log lines
leaking out of the Flask/SQLAlchemy/Celery internals.

Intentionally defensive: this is called from the app factory, so any failure
is caught by the caller and must never block application boot (matches the
codebase's graceful-degradation philosophy for optional infra like Celery and
WeasyPrint).
"""

import logging
import os
import sys

import structlog

_FALLBACK_LOGGERS = {
    # Noisy third-party loggers that should never drop below WARNING.
    "urllib3",
    "botocore",
    "boto3",
    "google",
    "googleapiclient",
    "pdfminer",
    "weasyprint",
}


def _is_production() -> bool:
    return (
        bool(os.environ.get("RENDER"))
        or os.environ.get("APP_ENV", "").lower() in ("production", "prod")
        or os.environ.get("FLASK_ENV", "").lower() == "production"
    )


def _build_renderer():
    if _is_production():
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer()


def _shared_processors():
    return [
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]


def setup_logging(app=None) -> None:
    """Configure global structured logging.

    Call once near the top of ``create_app()`` so that every subsequent
    ``app.logger.*`` / ``logging.getLogger().*`` call emits structured records.
    """
    is_prod = _is_production()
    level = logging.INFO if is_prod else logging.DEBUG
    renderer = _build_renderer()

    # Default factory for structlog.get_logger() callers.
    structlog.configure(
        processors=[*_shared_processors(), renderer],
        cache_logger_on_first_use=True,
    )

    # Bridge: render *stdlib* log records (Flask/SQLAlchemy/Celery internals)
    # through structlog too, so no plain-text lines escape.
    processor_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(processor_formatter)
    handler.setLevel(level)

    root = logging.getLogger()
    # Avoid duplicate handlers on re-init (e.g. multiple workers).
    root.handlers = [handler]
    root.setLevel(level)

    # Keep noisy third-party loggers quiet unless explicitly debug-requested.
    for name in _FALLBACK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Flask's app.logger has a default handler of its own; let it propagate
    # to the root handler configured above so its records are also structured.
    if app is not None:
        for existing in list(app.logger.handlers):
            app.logger.removeHandler(existing)
        app.logger.propagate = True
