"""FastAPI dependencies: shared config + standalone DB session (Phase 2).

These helpers work *outside* a Flask app context so ``/api/v2/*`` routes can
query the database directly.  The standalone ``Engine`` is bound to the same
``DATABASE_URL`` that ``create_app()`` uses, so both Flask and ASGI share the
same PostgreSQL/SQLite instance.

No Flask app-context push is required — the session factory is bound directly
to the engine, matching the SQLAlchemy 2.0 session pattern.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# --------------------------------------------------------------------------- #
# Standalone engine — mirrors the DATABASE_URL resolution in app/__init__.py.
# --------------------------------------------------------------------------- #
_engine: Any = None
_SessionLocal: sessionmaker | None = None


def _get_session_factory() -> sessionmaker:
    """Lazily create a sessionmaker bound to the same engine as Flask-SQLAlchemy."""
    global _engine, _SessionLocal
    if _SessionLocal is None:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            # Read from create_app config (env + SQLite fallback) to avoid duplication.
            from app import create_app

            app = create_app()
            url = app.config.get("SQLALCHEMY_DATABASE_URI", f"sqlite:///{Path('instance/app.db')}")
        _engine = create_engine(url)
        # Match Flask-SQLAlchemy's default: expire_on_commit=False for read-only use.
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _SessionLocal


# --------------------------------------------------------------------------- #
# Flask app access for /api/v2 handlers that touch Flask-managed singletons
# --------------------------------------------------------------------------- #
_flask_app: Any = None


def set_flask_app(app: Any) -> None:
    """Register the shared Flask app (called once from ``asgi.py`` at startup).

    Dependency inversion: ``app/api/routers.py`` cannot import ``asgi``
    (circular), so the ASGI entry point hands the app over here.
    """
    global _flask_app
    _flask_app = app


def get_flask_app() -> Any:
    """Return the registered Flask app (creating one as a last resort).

    Handlers that need a Flask app context — e.g. anything touching
    ``db.session`` or :func:`app.services.audit.log_audit`, which are bound to
    Flask-SQLAlchemy — should wrap their body in
    ``with get_flask_app().app_context():``.
    """
    global _flask_app
    if _flask_app is None:
        from app import create_app

        _flask_app = create_app()
    return _flask_app


# --------------------------------------------------------------------------- #
# Config flags — runtime env-var resolution for ASGI routes.
# --------------------------------------------------------------------------- #
def get_flag(key: str) -> bool:
    """Read a boolean flag from the runtime environment.

    ASGI routes (``/api/v2/*``) execute as native FastAPI handlers, outside
    any Flask app context in production, so ``cfg.get_bool`` (Pattern A —
    config wins in-context) would read stale values from the config snapshot
    taken at ``create_app()`` time.  Feature flags toggled via
    ``monkeypatch.setenv`` in tests (or dynamically at runtime) must be
    visible immediately, so we read ``os.environ`` directly — matching the
    pre-config-seam behaviour for this function.
    """
    return os.environ.get(key, "false").lower() == "true"


# --------------------------------------------------------------------------- #
# FastAPI dependency: DB session
# --------------------------------------------------------------------------- #
def get_db() -> Iterator[Session]:
    """Yield a standalone SQLAlchemy ``Session`` for FastAPI routes.

    The session is bound to the same ``DATABASE_URL`` as ``flask_sqlalchemy.db``,
    so reads see the same data.  Use as a FastAPI dependency:

    .. code-block:: python

        @app.get("/api/v2/some-endpoint")
        async def endpoint(db: Session = Depends(get_db)):
            ...

    The session is closed automatically on exit.  For write operations,
    ``db.commit()`` / ``db.rollback()`` must be called explicitly.
    """
    session = _get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# FastAPI dependency: RAG pipeline (shared singleton, monkeypatchable)
# --------------------------------------------------------------------------- #
_rag_pipeline: Any = None


def get_rag_pipeline():
    """Return the ResilientRAGPipeline singleton (or a test override).

    Lazily built so FastAPI boots without importing Qdrant/rich models.
    Tests monkeypatch ``get_rag_pipeline`` directly to inject stub pipelines.
    """
    global _rag_pipeline
    if _rag_pipeline is None:
        from app.rag.resilient import ResilientRAGPipeline

        _rag_pipeline = ResilientRAGPipeline()
    return _rag_pipeline
