"""Knowledge-graph sync task for Neo4j Aura.

``sync_kg_to_neo4j`` wraps
:func:`app.services.neo4j_graph.push_to_neo4j` so it can be dispatched
via QStash (synchronous inline fallback).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _run_sync_kg_to_neo4j(
    case_type: str | None = None,
    case_id: int | None = None,
) -> dict[str, Any]:
    """Core logic for pushing the knowledge graph to Neo4j Aura.

    Delegates to :func:`app.services.neo4j_graph.push_to_neo4j`.
    """
    from app.services.neo4j_graph import neo4j_configured, push_to_neo4j

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


def sync_kg_to_neo4j(
    case_type: str | None = None,
    case_id: int | None = None,
) -> dict[str, Any]:
    """Task entry point for Neo4j knowledge-graph sync (QStash-dispatched)."""
    return _run_sync_kg_to_neo4j(case_type=case_type, case_id=case_id)
