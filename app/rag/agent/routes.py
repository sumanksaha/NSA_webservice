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

from flask import jsonify, request

from app.rag import rag_bp
from app.shared.config import cfg

logger = logging.getLogger(__name__)


def _rag_enabled() -> bool:
    """Whether the RAG module is enabled (``RAG_ENABLED`` config)."""
    return cfg.rag_enabled


def _use_agent_pipeline() -> bool:
    """Whether the agent route runs the LangGraph graph (default false)."""
    return cfg.use_agent_pipeline


def _use_hitl() -> bool:
    """Whether the agent graph includes the M5 human-in-the-loop review node.

    Default false — the graph runs end-to-end with the groundedness retry
    loop only.  When true, ``POST /api/rag/query/agent`` pauses at the
    review interrupt and returns 202 with a thread_id for
    ``POST /api/rag/query/agent/resume``.
    """
    return cfg.agent_hitl


#: Warn once per process — the durability situation only changes on restart.
_hitl_durability_warned = False


def _warn_hitl_durability() -> bool:
    """Warn once when HITL runs on the non-durable memory checkpointer.

    Returns whether the configured checkpointer is durable so callers can
    surface it in the 202 ``awaiting_review`` payload (gap #5): paused
    threads under ``MemorySaver`` are lost whenever the process restarts.
    """
    global _hitl_durability_warned
    from app.rag.agent.graph import checkpointer_is_durable

    durable = checkpointer_is_durable()
    if not durable and not _hitl_durability_warned:
        logger.warning(
            "RAG_AGENT_HITL is enabled with the in-memory checkpointer — "
            "paused threads are LOST on process restart. Set "
            "RAG_AGENT_CHECKPOINTER=postgres for production HITL."
        )
        _hitl_durability_warned = True
    return durable


@rag_bp.route("/query/agent", methods=["POST"])
def query_agent():
    """Full RAG pipeline as a LangGraph agent (opt-in via the flag).

    Request JSON: same as ``/api/rag/query`` — ``query`` (required),
    ``top_k`` (default 10), ``collection_name``, ``filters`` — plus an
    optional ``use_agent`` boolean that overrides the
    ``RAG_USE_AGENT_PIPELINE`` config flag for this single request (the
    UI's "Use agent pipeline" checkbox).

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

    requested_agent = payload.get("use_agent")
    if requested_agent is not None and not isinstance(requested_agent, bool):
        return jsonify({"error": "use_agent must be a boolean."}), 400

    # Per-request override wins over the RAG_USE_AGENT_PIPELINE flag so the
    # UI's "Use agent pipeline" checkbox controls the pipeline per query.
    use_agent = _use_agent_pipeline() if requested_agent is None else requested_agent

    # Flag off → identical behaviour to the legacy pipeline.
    if not use_agent:
        from app.rag.routes import query

        return query()

    thread_id = payload.get("thread_id") if _use_hitl() else None
    if thread_id is not None and (not isinstance(thread_id, str) or not thread_id.strip()):
        return jsonify({"error": "thread_id must be a non-empty string."}), 400

    try:
        from app.rag.agent.graph import run_agent
        from app.rag.agent.state import initial_state

        state = initial_state(
            query_str,
            top_k=top_k,
            collection_name=payload.get("collection_name"),
            filters=payload.get("filters"),
        )
        result = run_agent(state, thread_id=thread_id, hitl=_use_hitl())
    except ImportError as exc:
        # langgraph not installed — surface as 503 like the disabled case.
        logger.warning("query_agent: %s", exc)
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        logger.error("RAG agent query failed: %s", exc)
        return jsonify({"error": f"RAG agent query failed: {exc}"}), 500

    # M5 human-in-the-loop: the graph paused at the review interrupt.
    if _use_hitl() and "__interrupt__" in result:
        durable = _warn_hitl_durability()
        interrupts = result["__interrupt__"]
        review = interrupts[0].value if interrupts else {}
        return (
            jsonify({
                "status": "awaiting_review",
                "thread_id": thread_id,
                "review": review,
                "durable": durable,
                "hint": "POST /api/rag/query/agent/resume with {thread_id, approved}.",
            }),
            202,
        )

    # Completed run — return the ``RAGResponse``-schema dict.
    return jsonify(result.get("response") or {})


@rag_bp.route("/query/agent/resume", methods=["POST"])
def query_agent_resume():
    """Resume a paused M5 human-in-the-loop run (2026-08-16, M5).

    Request JSON:
        thread_id (str, required): The thread id from the 202 response.
        approved (bool, default true): Human decision on the reviewed answer.

    Response JSON: the final ``RAGResponse``-schema dict (``pipeline:
    "agent"``) or another 202 ``awaiting_review`` if the graph pauses again.
    """
    if not _rag_enabled():
        return jsonify({"error": "RAG is disabled."}), 503
    if not _use_hitl():
        return jsonify({"error": "RAG_AGENT_HITL is false — no review flow to resume."}), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    thread_id = payload.get("thread_id")
    if not thread_id or not isinstance(thread_id, str) or not thread_id.strip():
        return jsonify({"error": "thread_id must be a non-empty string."}), 400

    approved = payload.get("approved", True)
    if not isinstance(approved, bool):
        return jsonify({"error": "approved must be a boolean."}), 400

    try:
        from app.rag.agent.graph import resume_agent

        result = resume_agent(thread_id, approved=approved, hitl=True)
    except ValueError as exc:
        logger.warning("query_agent_resume: %s", exc)
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("RAG agent resume failed: %s", exc)
        return jsonify({"error": f"RAG agent resume failed: {exc}"}), 500

    if "__interrupt__" in result:
        durable = _warn_hitl_durability()
        interrupts = result["__interrupt__"]
        review = interrupts[0].value if interrupts else {}
        return (
            jsonify({
                "status": "awaiting_review",
                "thread_id": thread_id,
                "review": review,
                "durable": durable,
            }),
            202,
        )

    return jsonify(result.get("response") or {})


# End of agent/routes.py
