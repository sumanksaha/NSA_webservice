"""Graph nodes — thin adapters over the existing RAG services (M3).

Each node is a plain function ``(state: RAGState) -> partial RAGState``.
They reuse the production pipeline entry points (``run_retrieval_pipeline``
/ ``run_generation_pipeline``) so the agent path and the legacy path share
exactly the same retrieval, reranking, KG-fusion, generation and
verification code — the graph only adds orchestration around them.

Imports inside the functions keep the agent package lazy: the legacy
pipeline never imports LangGraph or this module.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Groundedness below this triggers the expand-and-retry loop (plan §5.3).
GROUNDEDNESS_THRESHOLD = 0.7


def _query_for_retrieval(state: dict[str, Any]) -> str:
    """The query to retrieve with — the expanded query when one exists."""
    return state.get("expanded_query") or state.get("query") or ""


def classify_node(state: dict[str, Any]) -> dict[str, Any]:
    """Classify the query into a legal query type.

    Wraps :class:`QueryClassifier`; a failure degrades to ``"general"``
    so the graph never stalls on classification.
    """
    start = time.monotonic()
    query = state.get("query") or ""
    query_type = "general"
    detail: dict[str, Any] = {"fallback": False}
    try:
        from app.rag.retrieval import QueryClassifier

        query_type = QueryClassifier().classify(query).value
    except Exception as exc:  # noqa: BLE001 - best-effort adapter
        logger.warning("classify_node: classification failed (%s)", exc)
        detail = {"fallback": True, "error": str(exc)}
    return {
        "query_type": query_type,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "classify",
                "latency_ms": int((time.monotonic() - start) * 1000),
                "detail": {"query_type": query_type, **detail},
            },
        ],
    }


def retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
    """Retrieve candidate chunks via the Phase 1 pipeline.

    Calls ``run_retrieval_pipeline`` — which already runs hybrid retrieval
    (dense + Qdrant-side BM25 + identifier arm) and the ensemble reranker
    (sec_act features + remote CE when configured).  The returned chunks
    are plain dicts (``RetrievedChunk.to_dict()``), kept JSON-serializable.
    """
    start = time.monotonic()
    from app.rag.tasks import run_retrieval_pipeline

    result = run_retrieval_pipeline(
        query=_query_for_retrieval(state),
        top_k=state.get("top_k", 10),
        collection_name=state.get("collection_name"),
        filters=state.get("filters"),
    )
    return {
        "chunks": result.get("chunks", []),
        "query_type": result.get("query_type") or state.get("query_type", "general"),
        "retrieval_latency_ms": result.get("retrieval_latency_ms", 0),
        "log_id": result.get("log_id"),
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "retrieve",
                "latency_ms": int((time.monotonic() - start) * 1000),
                "detail": {
                    "chunk_count": len(result.get("chunks", [])),
                    "retrieval_latency_ms": result.get("retrieval_latency_ms", 0),
                    "log_id": result.get("log_id"),
                },
            },
        ],
    }


def evidence_node(state: dict[str, Any]) -> dict[str, Any]:
    """Optionally select a complementary evidence set (feature-flagged).

    Wraps ``select_evidence_set`` behind the ``ENABLE_EVIDENCE_SELECTOR``
    flag — identical to the legacy pipeline's behaviour.  The evidence set
    is attached to the state (``evidence_set``) and merged into the
    response by ``finalize_node``; retrieval chunks themselves are
    unchanged.
    """
    start = time.monotonic()
    from app.rag.retrieval.evidence_selector import (
        _evidence_selector_enabled,
        select_evidence_set,
    )

    evidence_set: dict[str, Any] | None = None
    if _evidence_selector_enabled() and state.get("chunks"):
        try:
            es = select_evidence_set(_query_for_retrieval(state), state["chunks"], max_size=5, min_size=2)
            evidence_set = es.to_dict()
        except Exception as exc:  # noqa: BLE001 - best-effort by design
            logger.warning("evidence_node: evidence selection failed (%s)", exc)
    return {
        "evidence_set": evidence_set,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "evidence",
                "latency_ms": int((time.monotonic() - start) * 1000),
                "detail": {"evidence_set": evidence_set is not None},
            },
        ],
    }


def generate_node(state: dict[str, Any]) -> dict[str, Any]:
    """Generate a grounded answer from the retrieved chunks.

    Calls ``run_generation_pipeline`` with the chunks already in state
    (skips retrieval) so KG fusion, grounding, hallucination detection and
    logging all run exactly as in the legacy path.
    """
    start = time.monotonic()
    from app.rag.tasks import run_generation_pipeline

    result = run_generation_pipeline(
        query=_query_for_retrieval(state),
        chunks=state.get("chunks"),
        query_type=state.get("query_type", ""),
        top_k=state.get("top_k", 10),
        collection_name=state.get("collection_name"),
        filters=state.get("filters"),
    )
    return {
        "answer": result.get("answer", ""),
        "groundedness": result.get("groundedness_score", 0.0),
        "hallucination_detected": result.get("hallucination_detected", False),
        "response": result,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "generate",
                "latency_ms": int((time.monotonic() - start) * 1000),
                "detail": {
                    "groundedness": result.get("groundedness_score", 0.0),
                    "hallucination_detected": result.get("hallucination_detected", False),
                    "answer_length": len(result.get("answer", "")),
                },
            },
        ],
    }


def verify_node(state: dict[str, Any]) -> dict[str, Any]:
    """Assess the generated response's groundedness.

    The actual verification (claim extraction, evidence comparison,
    groundedness scoring) already happened inside ``generate_node`` via
    ``run_generation_pipeline``.  This node records the score on the
    state so the graph's conditional edge can route on it; the threshold
    lives in :data:`GROUNDEDNESS_THRESHOLD`.
    """
    return {
        "groundedness": state.get("groundedness", 0.0),
        "hallucination_detected": state.get("hallucination_detected", False),
    }


def expand_query_node(state: dict[str, Any]) -> dict[str, Any]:
    """Rephrase / expand the query for a grounded retry.

    Reuses :class:`GroundedLLMClient` with a fixed expansion prompt
    (the same client the generation service uses — stub mode makes tests
    network-free).  On failure the original query is kept so the retry
    still proceeds; the retry count is always incremented so the loop
    terminates.
    """
    start = time.monotonic()
    from app.rag.generation.llm_client import GroundedLLMClient

    original = state.get("query") or ""
    expanded = original
    detail: dict[str, Any] = {"changed": False}
    try:
        client = GroundedLLMClient()
        resp = client.call(
            "You are a legal-retrieval query rewriter.",
            (
                "Rewrite the following food-safety legal question to improve "
                "retrieval: keep the statute, section and offence keywords, "
                "and expand abbreviations. Reply with only the rewritten "
                f"query.\n\nOriginal: {original}"
            ),
            temperature=0.0,
            max_tokens=120,
        )
        if resp.success and resp.text.strip():
            expanded = resp.text.strip()
            detail = {"changed": expanded != original}
    except Exception as exc:  # noqa: BLE001 - best-effort adapter
        logger.warning("expand_query_node: query expansion failed (%s)", exc)
        detail = {"changed": False, "error": str(exc)}

    return {
        "expanded_query": expanded,
        "retry_count": state.get("retry_count", 0) + 1,
        "audit_trail": [
            *(state.get("audit_trail") or []),
            {
                "node": "expand_query",
                "latency_ms": int((time.monotonic() - start) * 1000),
                "detail": {"retry": state.get("retry_count", 0) + 1, **detail},
            },
        ],
    }


def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    """Assemble the final ``RAGResponse``-schema result dict.

    Merges the generation result (``state["response"]``) with agent
    metadata: the retry count, the expanded query (if any) and the full
    audit trail.
    """
    response = dict(state.get("response") or {})
    response.setdefault("query", state.get("query", ""))
    response.setdefault("query_type", state.get("query_type", "general"))
    response.setdefault("retrieved_chunks", state.get("chunks", []))
    response["pipeline"] = "agent"
    response["agent"] = {
        "retry_count": state.get("retry_count", 0),
        "expanded_query": state.get("expanded_query"),
        "groundedness": state.get("groundedness", 0.0),
        "hallucination_detected": state.get("hallucination_detected", False),
        "audit_trail": state.get("audit_trail", []),
    }
    return {"response": response}
