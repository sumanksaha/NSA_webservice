"""Shared state schema for the LangGraph agent pipeline (M3).

``RAGState`` is a :class:`typing.TypedDict` describing everything the
graph nodes read and write.  It mirrors the fields of the legacy
``run_generation_pipeline`` result dict so the agent endpoint can
return the same ``RAGResponse``-schema shape to callers.
"""

from __future__ import annotations

from typing import Any, TypedDict


class AuditEntry(TypedDict, total=False):
    """One step in the agent's execution trail."""

    node: str
    latency_ms: int
    detail: dict[str, Any]


class RAGState(TypedDict, total=False):
    """State flowing through the LangGraph agent pipeline.

    All keys optional (``total=False``) so each node returns only the
    slice it updates and the graph compiler can merge partial updates.
    """

    # --- Input ---
    query: str
    top_k: int
    collection_name: str | None
    filters: dict[str, Any] | None

    # --- Classify ---
    query_type: str

    # --- Retrieve ---
    # List of chunk dicts (``RetrievedChunk.to_dict()`` shape) — kept as
    # plain dicts so the state stays JSON-serializable (M5 checkpointing).
    chunks: list[dict[str, Any]]
    retrieval_latency_ms: int
    log_id: str | None
    # Evidence set forwarded from retrieve_node (computed by apply_stages
    # inside run_retrieval_pipeline).  Avoids a redundant select_evidence_set
    # call in evidence_node.
    evidence_set: dict[str, Any] | None

    # --- Generate / verify ---
    answer: str
    groundedness: float
    hallucination_detected: bool
    response: dict[str, Any]

    # --- Retry loop ---
    retry_count: int
    expanded_query: str | None
    max_retries: int

    # --- M5 human-in-the-loop (review node) ---
    approved: bool

    # --- Audit ---
    audit_trail: list[AuditEntry]


def initial_state(
    query: str,
    *,
    top_k: int = 10,
    collection_name: str | None = None,
    filters: dict[str, Any] | None = None,
    max_retries: int = 2,
) -> RAGState:
    """Build the initial ``RAGState`` for a query.

    ``max_retries`` is fixed at 2 (matching the plan's ``retry_count < 2``
    guard) unless overridden — kept on the state so tests can probe the
    conditional edge cheaply.
    """
    return {
        "query": query,
        "top_k": top_k,
        "collection_name": collection_name,
        "filters": filters,
        "query_type": "",
        "chunks": [],
        "retrieval_latency_ms": 0,
        "log_id": None,
        "evidence_set": None,
        "answer": "",
        "groundedness": 0.0,
        "hallucination_detected": False,
        "response": {},
        "retry_count": 0,
        "expanded_query": None,
        "max_retries": max_retries,
        "audit_trail": [],
    }
