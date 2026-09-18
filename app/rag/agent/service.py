"""Transport-agnostic core for the LangGraph agent HTTP endpoints (M3/M5).

Single home for the agent request/response contract shared by the Flask
blueprint (``POST /api/rag/query/agent`` + ``/resume`` in
:mod:`app.rag.agent.routes`) and the FastAPI gateway
(``POST /api/v2/rag/query/agent`` + ``/resume`` in ``asgi.py``).

Each entry point takes plain values and returns ``(status_code, payload)``;
transports only parse request bodies, own their legacy (non-agent)
fallbacks, and render the payload (``jsonify`` vs ``JSONResponse``).
Validation messages, error mapping, and the 202 ``awaiting_review`` shape
(including the ``durable`` HITL flag) therefore cannot drift between the
two surfaces.

Deliberately transport-owned (NOT here):

* request body shape / parsing (Flask JSON-dict check vs pydantic models,
  including the v2 ``top_k <= 50`` bound),
* the ``RAG_ENABLED`` gate (the Flask route 503s; the v2 route never did),
* the flag-off legacy fallback (Flask delegates to its ``query()`` view,
  v2 runs the resilient pipeline directly),
* the per-request ``use_agent`` override flag itself (each transport reads
  its own body field; the shared ``"use_agent must be a boolean."`` message
  is produced by the transport).

All ``langgraph`` imports stay lazy inside the functions (via
:mod:`app.rag.agent.graph`), so importing this module never requires the
optional dependency.  Callers that monkeypatch
``app.rag.agent.graph.run_agent`` / ``resume_agent`` (tests) keep working
because the graph functions are imported per call, not bound at module
load.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Warn once per process — the durability situation only changes on restart.
_hitl_durability_warned = False


def _warn_hitl_durability() -> bool:
    """Warn once when HITL runs on the non-durable memory checkpointer.

    Returns whether the configured checkpointer is durable so callers can
    surface it in the 202 ``awaiting_review`` payload: paused threads under
    ``MemorySaver`` are lost whenever the process restarts.
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


def awaiting_review_payload(
    thread_id: str | None,
    review: dict[str, Any],
    durable: bool,
    *,
    hint: str | None = None,
) -> dict[str, Any]:
    """Build the 202 ``awaiting_review`` payload (both transports, both pauses)."""
    payload: dict[str, Any] = {
        "status": "awaiting_review",
        "thread_id": thread_id,
        "review": review,
        "durable": durable,
    }
    if hint is not None:
        payload["hint"] = hint
    return payload


def _interrupt_review(result: dict[str, Any]) -> dict[str, Any]:
    """Extract the review payload from a paused (``__interrupt__``) result."""
    interrupts = result.get("__interrupt__") or []
    return interrupts[0].value if interrupts else {}


def _completed_payload(result: Any) -> dict[str, Any]:
    """Normalize a completed graph result to the ``RAGResponse``-schema dict.

    ``run_agent`` unwraps to the response dict itself while ``resume_agent``
    returns the full state (with a ``"response"`` key) — and test doubles
    use either shape.  Accept both: a ``"response"`` key means full state,
    anything else is already the response payload.
    """
    if isinstance(result, dict) and "response" in result:
        response = result.get("response")
        return response if isinstance(response, dict) else {}
    return result if isinstance(result, dict) else {}


def run_agent_query(
    *,
    query: str,
    top_k: int = 10,
    collection_name: str | None = None,
    filters: Any | None = None,
    thread_id: str | None = None,
    hitl: bool = False,
    resume_hint: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run one agent query; return ``(status_code, payload)``.

    ``(200, RAGResponse-dict)`` on completion, ``(202, awaiting_review)``
    on an M5 pause, ``(400, ...)`` on validation failure, ``(503, ...)``
    when langgraph is missing, ``(500, ...)`` on unexpected failure.
    """
    if not query or not isinstance(query, str) or not query.strip():
        return 400, {"error": "query must be a non-empty string."}
    if not isinstance(top_k, int) or top_k < 1:
        return 400, {"error": "top_k must be a positive integer."}
    if hitl and thread_id is not None and (
        not isinstance(thread_id, str) or not thread_id.strip()
    ):
        return 400, {"error": "thread_id must be a non-empty string."}

    try:
        from app.rag.agent.graph import run_agent
        from app.rag.agent.state import initial_state

        state = initial_state(
            query,
            top_k=top_k,
            collection_name=collection_name,
            filters=filters,
        )
        result = run_agent(state, thread_id=thread_id, hitl=hitl)
    except ImportError as exc:
        # langgraph not installed — surface as 503 like the disabled case.
        logger.warning("run_agent_query: %s", exc)
        return 503, {"error": str(exc)}
    except Exception as exc:
        logger.error("RAG agent query failed: %s", exc)
        return 500, {"error": f"RAG agent query failed: {exc}"}

    # M5 human-in-the-loop: the graph paused at the review interrupt.
    if hitl and "__interrupt__" in result:
        durable = _warn_hitl_durability()
        return 202, awaiting_review_payload(
            thread_id, _interrupt_review(result), durable, hint=resume_hint
        )

    # Completed run — the ``RAGResponse``-schema dict.
    return 200, _completed_payload(result)


def resume_agent_query(
    *,
    thread_id: Any | None,
    approved: Any = True,
    hitl: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Resume a paused M5 run; return ``(status_code, payload)``.

    ``(200, RAGResponse-dict)`` on completion, ``(202, awaiting_review)``
    if the graph pauses again, ``(400, ...)`` on validation failure or when
    HITL is disabled, ``(500, ...)`` on unexpected failure.
    """
    if not hitl:
        return 400, {"error": "RAG_AGENT_HITL is false — no review flow to resume."}
    if not thread_id or not isinstance(thread_id, str) or not thread_id.strip():
        return 400, {"error": "thread_id must be a non-empty string."}
    if not isinstance(approved, bool):
        return 400, {"error": "approved must be a boolean."}

    try:
        from app.rag.agent.graph import resume_agent

        # NOTE: hitl defaults to True (the only resume path is the M5 review
        # gate); it is intentionally not passed so test doubles with a
        # ``(thread_id, approved)`` signature keep working.
        result = resume_agent(thread_id, approved=approved)
    except ValueError as exc:
        logger.warning("resume_agent_query: %s", exc)
        return 400, {"error": str(exc)}
    except Exception as exc:
        logger.error("RAG agent resume failed: %s", exc)
        return 500, {"error": f"RAG agent resume failed: {exc}"}

    if "__interrupt__" in result:
        durable = _warn_hitl_durability()
        return 202, awaiting_review_payload(thread_id, _interrupt_review(result), durable)

    return 200, _completed_payload(result)
