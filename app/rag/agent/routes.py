"""HTTP endpoint for the LangGraph agent pipeline (M3).

``POST /api/rag/query/agent`` runs the self-correcting agent graph
(classify → retrieve → generate → verify → expand-and-retry).

Behavior is controlled by ``RAG_USE_AGENT_PIPELINE`` (default false):

* **true** — run the LangGraph agent (``app.rag.agent.graph.run_agent``).
* **false** — delegate to the legacy ``query()`` route, so the endpoint
  exists and behaves identically to ``/api/rag/query`` (zero behaviour
  change until the flag flips — plan §8 rollout).

The existing ``/api/rag/query`` route is unchanged.
"""

from __future__ import annotations

import logging

from flask import current_app, jsonify, request

from app.rag import rag_bp

logger = logging.getLogger(__name__)


def _rag_enabled() -> bool:
    """Whether the RAG module is enabled (``RAG_ENABLED`` config)."""
    return bool(current_app.config.get("RAG_ENABLED", True))


def _use_agent_pipeline() -> bool:
    """Whether the agent route runs the LangGraph graph (default false)."""
    return bool(current_app.config.get("RAG_USE_AGENT_PIPELINE", False))


@rag_bp.route("/query/agent", methods=["POST"])
def query_agent():
    """Full RAG pipeline as a LangGraph agent (opt-in via the flag).

    Request JSON: same as ``/api/rag/query`` — ``query`` (required),
    ``top_k`` (default 10), ``collection_name``, ``filters``.

    Response JSON: a ``RAGResponse``-schema dict (identical shape to the
    legacy route) with an extra ``pipeline: "agent"`` marker and an
    ``agent`` block (``retry_count``, ``expanded_query``, ``audit_trail``)
    when the graph runs.
    """
    if not _rag_enabled():
        return jsonify({"error": "RAG is disabled."}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    query_str = payload.get("query")
    if not query_str or not isinstance(query_str, str) or not query_str.strip():
        return jsonify({"error": "query must be a non-empty string."}), 400

    top_k = payload.get("top_k", 10)
    if not isinstance(top_k, int) or top_k < 1:
        return jsonify({"error": "top_k must be a positive integer."}), 400

    # Flag off → identical behaviour to the legacy pipeline.
    if not _use_agent_pipeline():
        from app.rag.routes import query

        return query()

    try:
        from app.rag.agent.graph import run_agent
        from app.rag.agent.state import initial_state

        state = initial_state(
            query_str,
            top_k=top_k,
            collection_name=payload.get("collection_name"),
            filters=payload.get("filters"),
        )
        result = run_agent(state)
    except ImportError as exc:
        # langgraph not installed — surface as 503 like the disabled case.
        logger.warning("query_agent: %s", exc)
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        logger.error("RAG agent query failed: %s", exc)
        return jsonify({"error": f"RAG agent query failed: {exc}"}), 500

    return jsonify(result)


# End of agent/routes.py
