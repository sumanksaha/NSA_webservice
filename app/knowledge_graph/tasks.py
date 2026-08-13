"""Celery tasks for Neo4j knowledge-graph sync.

``sync_kg_to_neo4j_task`` wraps
:func:`app.services.neo4j_graph.push_to_neo4j` as a Celery task so it can be
dispatched asynchronously via QStash — following the same lazy-import /
graceful-degradation pattern as ``app/rag/tasks.py`` and
``app/food_cell/tasks.py``.

If Celery is not installed or the broker is unavailable, the function can
still be called directly (synchronous fallback via QStash's publish_task).
"""

from __future__ import annotations

import logging
from typing import Any

# Lazy import so the module boots even when Celery isn't installed.
try:
    from celery_app import celery
except ImportError:
    celery = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _run_sync_kg_to_neo4j(
    case_type: str | None = None,
    case_id: int | None = None,
) -> dict[str, Any]:
    """Core logic for pushing the knowledge graph to Neo4j Aura.

    Delegates to :func:`app.services.neo4j_graph.push_to_neo4j`.
    """
    from app.services.neo4j_graph import push_to_neo4j, neo4j_configured

    if not neo4j_configured():
        return {"status": "error", "message": "Neo4j not configured in .env"}

    try:
        summary = push_to_neo4j(case_type=case_type, case_id=case_id)
    except RuntimeError as exc:
        # Fail-closed write guard (NEO4J_ALLOW_WRITE=1) — surface a clean
        # error status instead of a 500 traceback.
        logger.warning("Neo4j sync refused: %s", exc)
        return {"status": "error", "message": str(exc)}
    logger.info(
        "Knowledge graph synced to Neo4j: %d nodes, %d edges",
        summary["nodes"],
        summary["edges"],
    )
    return {"status": "ok", **summary}


if celery is not None:

    @celery.task(bind=True, name="sync_kg_to_neo4j")
    def sync_kg_to_neo4j_task(self, case_type: str | None = None, case_id: int | None = None):
        """Celery task wrapper for Neo4j knowledge-graph sync."""
        return _run_sync_kg_to_neo4j(case_type=case_type, case_id=case_id)

    # Expose as .apply() callable for synchronous fallback
    sync_kg_to_neo4j = sync_kg_to_neo4j_task
else:
    # No Celery — expose as a plain function with .apply() shim
    # pyright: ignore[reportMissingImports]

    class _SyncFunc:
        """Plain-function fallback that mimics a Celery task's .apply()."""

        def __call__(self, case_type: str | None = None, case_id: int | None = None):
            return _run_sync_kg_to_neo4j(case_type=case_type, case_id=case_id)

        def apply(self, kwargs=None):
            result = self(**(kwargs or {}))

            class _Result:
                @property
                def result(self):
                    return result

            return _Result()

    sync_kg_to_neo4j = _SyncFunc()
