"""HTTP endpoint for the LangGraph agent pipeline (M3).

``POST /api/rag/query/agent`` runs the self-correcting agent graph
(classify → retrieve → generate → verify → expand-and-retry).

Behavior is controlled by ``RAG_USE_AGENT_PIPELINE`` (default false):

* **true** — run the LangGraph agent (``app.rag.agent.service`` core).
* **false** — delegate to the legacy ``query()`` route, so the endpoint
  exists and behaves identically to ``/api/rag/query`` (zero behaviour
  change until the flag flips — plan §8 rollout).

The existing ``/api/rag/query`` route is unchanged.

Transport note: this module only parses the Flask request body, owns the
legacy fallback, and renders responses.  The agent request/response
contract (validation, error mapping, 202 ``awaiting_review`` shape) lives
in :mod:`app.rag.agent.service`, shared with the FastAPI gateway.
"""

from __future__ import annotations

from flask import jsonify, request

from app.rag import rag_bp
from app.rag.agent.service import resume_agent_query, run_agent_query
from app.shared.config import cfg


def _rag_enabled() -> bool:
    """Whether the RAG module is enabled (``RAG_ENABLED`` config)."""
    enabled: bool = cfg.rag_enabled
    return enabled


def _use_agent_pipeline() -> bool:
    """Whether the agent route runs the LangGraph graph (default false)."""
    use_agent: bool = cfg.use_agent_pipeline
    return use_agent


def _use_hitl() -> bool:
    """Whether the agent graph includes the M5 human-in-the-loop review node.

    Default false — the graph runs end-to-end with the groundedness retry
    loop only.  When true, ``POST /api/rag/query/agent`` pauses at the
    review interrupt and returns 202 with a thread_id for
    ``POST /api/rag/query/agent/resume``.
    """
    hitl: bool = cfg.agent_hitl
    return hitl


@rag_bp.route("/query/agent", methods=["POST"])
def query_agent():
    """Full RAG pipeline as a LangGraph agent (opt-in via the flag).

    Request JSON: same as ``/api/rag/query`` — ``query`` (required),
    ``top_k`` (default 10), ``collection_name``, ``filters`` — plus an
    optional ``use_agent`` boolean that overrides the
    ``RAG_USE_AGENT_PIPELINE`` config flag for this single request (the
    UI's "Use agent pipeline" checkbox), and the FSO advisory fields
    (ADR-0003): optional ``fso_advisory`` boolean (per-request override of
    ``FSO_ADVISOR_ENABLED``), ``is_repeat_offender`` and ``has_lab_report``
    booleans (default false) feeding the deterministic selector.

    Response JSON: a ``RAGResponse``-schema dict (identical shape to the
    legacy route) with an extra ``pipeline: "agent"`` marker and an
    ``agent`` block (``retry_count``, ``expanded_query``, ``audit_trail``)
    when the graph runs — plus ``fso_act`` (dict or null) when advisory ran.
    """
    if not _rag_enabled():
        return jsonify({"error": "RAG is disabled."}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

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

    requested_advisory = payload.get("fso_advisory")
    if requested_advisory is not None and not isinstance(requested_advisory, bool):
        return jsonify({"error": "fso_advisory must be a boolean."}), 400
    is_repeat_offender = payload.get("is_repeat_offender", False)
    if not isinstance(is_repeat_offender, bool):
        return jsonify({"error": "is_repeat_offender must be a boolean."}), 400
    has_lab_report = payload.get("has_lab_report", False)
    if not isinstance(has_lab_report, bool):
        return jsonify({"error": "has_lab_report must be a boolean."}), 400

    # Agent path — validation, error mapping and the 202 shape live in the
    # shared service core (single contract with the FastAPI gateway).
    status, body = run_agent_query(
        query=payload.get("query"),
        top_k=payload.get("top_k", 10),
        collection_name=payload.get("collection_name"),
        filters=payload.get("filters"),
        thread_id=payload.get("thread_id") if _use_hitl() else None,
        hitl=_use_hitl(),
        resume_hint="POST /api/rag/query/agent/resume with {thread_id, approved}.",
        fso_advisor=requested_advisory,
        is_repeat_offender=is_repeat_offender,
        has_lab_report=has_lab_report,
    )
    return jsonify(body), status


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

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    requested_advisory = payload.get("fso_advisory")
    if requested_advisory is not None and not isinstance(requested_advisory, bool):
        return jsonify({"error": "fso_advisory must be a boolean."}), 400

    status, body = resume_agent_query(
        thread_id=payload.get("thread_id"),
        approved=payload.get("approved", True),
        hitl=_use_hitl(),
        fso_advisor=requested_advisory,
    )
    return jsonify(body), status


# End of agent/routes.py
