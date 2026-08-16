"""LangGraph agent graph (M3 + M4).

The graph orchestrates the existing RAG services into a self-correcting
pipeline::

    classify ──► retrieve ──► generate ──► verify ──► finalize ──► END
                  ▲                            │
                  └──── expand_query ◄─────────┘   (groundedness < 0.7, retries < max_retries)

* All nodes are synchronous (aligns with Flask + Celery).
* No parallel ``Send`` in v1 — deferred (see the LangGraph eval doc §1.4).
* ``langgraph`` is imported **only here**, lazily — the legacy pipeline
  and the rest of the app never import it (plan §5.1).
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


def build_graph() -> Any:
    """Build and compile the agent ``StateGraph``.

    Returns the compiled graph; callers ``.invoke(state)`` it.  Also
    assigned to the module-level ``agent_graph`` (compiled once at import,
    per the plan) — ``run_agent`` uses that instance.
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
    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {"expand_query": "expand_query", "finalize": "finalize"},
    )
    builder.add_edge("expand_query", "retrieve")
    builder.add_edge("finalize", END)

    return builder.compile()


# Compiled once at import, per the plan §5.2 ("compile()d once at import").
# Lazy: importing this module is the only place langgraph gets imported,
# so the rest of the app is untouched when it is missing.
try:
    agent_graph: Any = build_graph()
except ImportError:  # pragma: no cover - langgraph optional
    agent_graph = None


def run_agent(state: RAGState) -> dict[str, Any]:
    """Invoke the compiled graph on an initial state.

    Returns the final ``response`` dict (``RAGResponse``-schema, with the
    ``pipeline: "agent"`` marker and agent metadata attached).
    """
    if agent_graph is None:
        raise ImportError(
            "The LangGraph agent pipeline is not available (langgraph missing). "
            "Install langgraph to use /api/rag/query/agent."
        )
    final_state = agent_graph.invoke(state)
    return final_state.get("response") or {}


# Re-export for tests / convenience.
route_fn: Callable[[RAGState], str] = route_after_verify
