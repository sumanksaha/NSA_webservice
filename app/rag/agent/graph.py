"""LangGraph agent graph (M3 + M4 + M5).

The graph orchestrates the existing RAG services into a self-correcting
pipeline::

    classify ──► retrieve ──► generate ──► verify ──► finalize ──► END
                  ▲                            │
                  └──── expand_query ◄─────────┘   (groundedness < 0.7, retries < max_retries)

M5 (checkpointing + human-in-the-loop):

* ``build_graph(hitl=True)`` inserts a ``review`` node between ``verify``
  and the conditional edge.  ``review`` calls :func:`langgraph.types.interrupt`
  so the graph pauses for a human decision; a resumed ``approved`` value
  routes to ``finalize``, a rejection routes back to ``expand_query``
  (re-generate with a rewritten query).
* ``run_agent(..., thread_id=...)`` invokes with a checkpointer so the
  paused graph state can be resumed by thread id.
* Checkpointer selection: ``RAG_AGENT_CHECKPOINTER`` = ``memory`` (default,
  ``MemorySaver`` — dev/tests, no DB) or ``postgres`` (``PostgresSaver`` —
  prod; requires ``langgraph-checkpoint-postgres`` + psycopg).

All nodes are synchronous (aligns with Flask + Celery).  ``langgraph`` is
imported **only here**, lazily — the legacy pipeline and the rest of the
app never import it (plan §5.1).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from app.rag.agent.nodes import GROUNDEDNESS_THRESHOLD
from app.rag.agent.state import RAGState

logger = logging.getLogger(__name__)


def _evidence_enabled() -> bool:
    """Resolve the ENABLE_EVIDENCE_SELECTOR flag (Flask config, else env)."""
    try:
        from flask import current_app, has_app_context

        if has_app_context() and "ENABLE_EVIDENCE_SELECTOR" in current_app.config:
            return bool(current_app.config["ENABLE_EVIDENCE_SELECTOR"])
    except Exception:  # noqa: BLE001 - fall through to env
        pass
    return os.environ.get("ENABLE_EVIDENCE_SELECTOR", "false").lower() == "true"


def route_after_verify(state: RAGState) -> str:
    """Conditional edge: retry (expand → retrieve) or finalize.

    Retries while the response is not grounded enough AND the retry budget
    is not exhausted (``retry_count < max_retries``, default 2).
    """
    groundedness = float(state.get("groundedness", 0.0))
    retry_count = int(state.get("retry_count", 0))
    max_retries = int(state.get("max_retries", 2))
    if groundedness < GROUNDEDNESS_THRESHOLD and retry_count < max_retries:
        return "expand_query"
    return "finalize"


def route_after_review(state: RAGState) -> str:
    """Conditional edge from the M5 review node.

    ``approved`` (set by the human resume value) → finalize; otherwise
    re-generate via the expand-and-retry loop.
    """
    if state.get("approved"):
        return "finalize"
    return "expand_query"


def review_node(state: RAGState) -> dict[str, Any]:
    """M5 human-in-the-loop gate (only present when ``hitl=True``).

    Pauses the graph with :func:`langgraph.types.interrupt`, surfacing the
    generated answer + groundedness for a human decision.  The resume value
    (``{"approved": bool}`` or a bare bool) lands on the state as
    ``approved``; the conditional ``route_after_review`` edge then decides.
    """
    from langgraph.types import interrupt

    decision = interrupt(
        {
            "message": "Review the grounded answer before release.",
            "query": state.get("query", ""),
            "answer": state.get("answer", ""),
            "groundedness": state.get("groundedness", 0.0),
            "hallucination_detected": state.get("hallucination_detected", False),
            "retry_count": state.get("retry_count", 0),
        }
    )
    if isinstance(decision, dict):
        approved = bool(decision.get("approved", True))
    else:
        approved = bool(decision)
    return {"approved": approved}


def _checkpointer_kind() -> str:
    """Resolve ``RAG_AGENT_CHECKPOINTER`` (Flask config, else env)."""
    try:
        from flask import current_app, has_app_context

        if has_app_context() and "RAG_AGENT_CHECKPOINTER" in current_app.config:
            return str(current_app.config["RAG_AGENT_CHECKPOINTER"]).lower()
    except Exception:  # noqa: BLE001 - fall through to env
        pass
    return os.environ.get("RAG_AGENT_CHECKPOINTER", "memory").lower()


#: In-process MemorySaver singleton — shared across requests so a paused
#: (interrupted) thread can be resumed by a later HTTP call.
_memory_saver: Any | None = None


def _build_checkpointer(kind: str | None = None) -> Any | None:
    """Build the checkpointer for ``kind`` (memory | postgres | none).

    * ``memory``  — :class:`langgraph.checkpoint.memory.MemorySaver`
      (default; no DB, in-process only — dev/tests).
    * ``postgres`` — :class:`langgraph.checkpoint.postgres.PostgresSaver`
      against ``DATABASE_URL``; requires ``langgraph-checkpoint-postgres``
      + ``psycopg`` (psycopg-binary provides libpq).  Creates the
      checkpoint tables on first use.  Best-effort: a missing dep / bad
      DSN degrades to ``None`` (no checkpointing) rather than raising.
    * ``none`` — no checkpointer (no resume support).
    """
    kind = (kind or _checkpointer_kind()).lower()
    if kind in ("none", ""):
        return None
    if kind == "postgres":
        try:
            import psycopg
            from langgraph.checkpoint.postgres import PostgresSaver

            dsn = os.environ.get("DATABASE_URL") or ""
            if not dsn:
                logger.warning("RAG_AGENT_CHECKPOINTER=postgres but DATABASE_URL unset — no checkpointing")
                return None
            # Normalise for psycopg (accepts postgres:// and postgresql://).
            conn = psycopg.connect(dsn)
            saver = PostgresSaver(conn)
            saver.setup()  # idempotent CREATE TABLE IF NOT EXISTS
            return saver
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning("PostgresSaver unavailable — no checkpointing (%s)", exc)
            return None
    try:
        from langgraph.checkpoint.memory import MemorySaver

        # Singleton: the in-process saver must be shared across requests so a
        # paused (interrupted) thread can be resumed by a later HTTP call.
        global _memory_saver
        if _memory_saver is None:
            _memory_saver = MemorySaver()
        return _memory_saver
    except Exception:  # noqa: BLE001 - best-effort
        return None


def build_graph(
    hitl: bool = False,
    checkpointer: Any | None = None,
) -> Any:
    """Build and compile the agent ``StateGraph``.

    Args:
        hitl: Insert the M5 ``review`` (human-in-the-loop) node between
            ``verify`` and the conditional edge.
        checkpointer: A LangGraph checkpointer (e.g. ``MemorySaver`` /
            ``PostgresSaver``) to enable thread resume; ``None`` disables
            checkpointing.

    Returns the compiled graph; callers ``.invoke(state)`` it.
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError(
            "The LangGraph agent pipeline requires 'langgraph'. "
            "Install it (pip install langgraph) to use /api/rag/query/agent."
        ) from exc

    from app.rag.agent import nodes

    builder: StateGraph = StateGraph(RAGState)

    builder.add_node("classify", nodes.classify_node)
    builder.add_node("retrieve", nodes.retrieve_node)
    builder.add_node("generate", nodes.generate_node)
    builder.add_node("verify", nodes.verify_node)
    builder.add_node("expand_query", nodes.expand_query_node)
    builder.add_node("finalize", nodes.finalize_node)

    builder.add_edge(START, "classify")
    builder.add_edge("classify", "retrieve")

    # Optional evidence node between retrieve and generate (feature-flagged).
    if _evidence_enabled():
        builder.add_node("evidence", nodes.evidence_node)
        builder.add_edge("retrieve", "evidence")
        builder.add_edge("evidence", "generate")
    else:
        builder.add_edge("retrieve", "generate")

    builder.add_edge("generate", "verify")

    if hitl:
        # M5: human-in-the-loop gate.  review interrupts; approved → finalize,
        # rejected → expand_query (re-generate with a rewritten query).
        builder.add_node("review", review_node)
        builder.add_edge("verify", "review")
        builder.add_conditional_edges(
            "review",
            route_after_review,
            {"expand_query": "expand_query", "finalize": "finalize"},
        )
    else:
        builder.add_conditional_edges(
            "verify",
            route_after_verify,
            {"expand_query": "expand_query", "finalize": "finalize"},
        )

    builder.add_edge("expand_query", "retrieve")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


# Compiled once at import, per the plan §5.2 ("compile()d once at import").
# Lazy: importing this module is the only place langgraph gets imported,
# so the rest of the app is untouched when it is missing.  The default
# graph carries no checkpointer (zero overhead for the non-resume path);
# ``run_agent`` rebuilds with a checkpointer only when a thread_id is given.
try:
    agent_graph: Any = build_graph(hitl=False)
    agent_graph_hitl: Any = build_graph(hitl=True)
except ImportError:  # pragma: no cover - langgraph optional
    agent_graph = None
    agent_graph_hitl = None


def run_agent(
    state: RAGState,
    *,
    thread_id: str | None = None,
    hitl: bool = False,
    checkpointer: Any | None = None,
) -> dict[str, Any]:
    """Invoke the agent graph on an initial state.

    Args:
        state: Initial ``RAGState``.
        thread_id: When set, invoke under a checkpointer with this thread
            id so a paused (interrupted) run can be resumed later.  The
            checkpointer comes from *checkpointer* if given, else from the
            ``RAG_AGENT_CHECKPOINTER`` config (memory default).
        hitl: Use the M5 human-in-the-loop variant (review interrupt).
        checkpointer: Optional explicit checkpointer (bypasses config).

    Returns:
        The final ``RAGState`` dict.  When the graph pauses at the M5
        review interrupt, the returned dict carries the ``__interrupt__``
        key (LangGraph convention) instead of a final ``response`` — the
        caller should detect it and surface the review request.
    """
    graph = agent_graph
    if hitl:
        graph = agent_graph_hitl
    if graph is None:
        raise ImportError(
            "The LangGraph agent pipeline is not available (langgraph missing). "
            "Install langgraph to use /api/rag/query/agent."
        )

    if thread_id:
        # Rebuild with a checkpointer so resume works across requests.
        cp = checkpointer if checkpointer is not None else _build_checkpointer()
        graph = build_graph(hitl=hitl, checkpointer=cp)
        result = graph.invoke(
            state,
            config={"configurable": {"thread_id": thread_id}},
        )
    else:
        result = graph.invoke(state)

    # Contract (M3): a completed run returns the ``RAGResponse``-schema
    # dict.  A paused M5 run returns the raw state carrying ``__interrupt__``
    # so the caller can surface the review request.
    if "__interrupt__" in result:
        return result
    return result.get("response") or {}


def resume_agent(
    thread_id: str,
    *,
    approved: bool = True,
    hitl: bool = True,
    checkpointer: Any | None = None,
) -> dict[str, Any]:
    """Resume a paused M5 run by thread id.

    Re-invokes the graph under the same thread id with a
    ``Command(resume=...)`` carrying the human decision.  Returns the final
    state (``response`` set) or the next ``__interrupt__`` if it pauses again.
    """
    from langgraph.types import Command

    cp = checkpointer if checkpointer is not None else _build_checkpointer()
    if cp is None:
        raise ValueError("Resume requires a checkpointer (RAG_AGENT_CHECKPOINTER=memory|postgres).")
    graph = build_graph(hitl=hitl, checkpointer=cp)
    return graph.invoke(
        Command(resume={"approved": approved}),
        config={"configurable": {"thread_id": thread_id}},
    )


# Re-export for tests / convenience.
route_fn: Callable[[RAGState], str] = route_after_verify
