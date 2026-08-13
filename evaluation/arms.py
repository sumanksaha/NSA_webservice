"""Retrieval arms A–F (protocol §4) — frozen production components only.

Every arm imports and drives the production retrievers unchanged
(``DenseRetriever``, ``SparseRetriever``, ``HybridRetriever``, ``Reranker``,
``KGContextExpander``, ``kg.queries``).  No production file is modified.

Arms:
    A  dense only                      (Qdrant dense, per-question collection)
    B  sparse / BM25 only              (Qdrant BM25 sparse, per-question collection)
    C  dense + sparse hybrid           (HybridRetriever, no reranker)
    D  KG retrieval                    (graph-RAG contract: query -> provisions)
    E  dense + sparse + KG             (hybrid chunks + KG expansion of chunk ids)
    F  dense + sparse + KG + reranker  (rerank hybrid pool, then KG expansion)

Retrievers are cached per collection so models/TLS are not re-initialised
per question (a material speed-up: SentenceTransformer load ~2s, Qdrant TLS
handshake ~1-2s).  Results are JSON-serialisable for caching.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Per-collection retriever caches (valid for the whole process lifetime)
# --------------------------------------------------------------------------- #
_dense_cache: dict[str, Any] = {}
_sparse_cache: dict[str, Any] = {}
_reranker_cache: dict[str, Any] = {}


def _dense(collection: str) -> Any:
    if collection not in _dense_cache:
        from app.rag.retrieval import DenseRetriever

        _dense_cache[collection] = DenseRetriever(collection_name=collection)
    return _dense_cache[collection]


def _sparse(collection: str) -> Any:
    if collection not in _sparse_cache:
        from app.rag.qdrant_client import QdrantStore
        from app.rag.retrieval import SparseRetriever
        from app.rag.sparse_embedding import SparseEmbeddingService

        _sparse_cache[collection] = SparseRetriever(
            corpus={},
            store=QdrantStore(collection_name=collection),
            embedder=SparseEmbeddingService(),
        )
    return _sparse_cache[collection]


def _reranker() -> Any:
    if not _reranker_cache:
        from app.rag.retrieval import Reranker

        _reranker_cache["x"] = Reranker()
    return _reranker_cache["x"]


# --------------------------------------------------------------------------- #
# KG helpers
# --------------------------------------------------------------------------- #
def _kg_provision_public(p: dict[str, Any]) -> dict[str, Any]:
    """Project a KG provision dict to the cacheable fields metrics need."""
    instrument_title = (
        p.get("instrument_title")
        or (p.get("instrument") or {}).get("title")
        or ""
    )
    return {
        "provision_id": p.get("provision_id"),
        "provision_number": p.get("provision_number"),
        "title": p.get("title") or "",
        "instrument_title": instrument_title,
        "legal_domain": p.get("legal_domain") or p.get("domain") or "",
        "status": p.get("status") or "",
    }


def kg_contract_provisions(query: str, database: str | None = None) -> list[dict[str, Any]]:
    """Arm D — the production graph-RAG retrieval contract (query -> provisions).

    **Fixed 2026-08-12:** the production ``kg.queries`` Cypher bug
    (``ORDER BY d.priority`` / ``i.instrument_id`` after an aggregation
    RETURN in ``get_cross_domain_laws`` / ``get_applicable_laws`` —
    out-of-scope variables — plus the ``GRANST_POWER_TO`` typo) has been
    fixed in ``kg/queries.py``, so KG concept traversal now runs.

    This implements the same retrieval steps as
    ``build_llm_retrieval_contract`` steps 1–4 (concept traversal via
    ``get_cross_domain_laws``, falling back to the production full-text
    provision search when no concept matches) but *skips* the per-provision
    enrichment (steps 5–9: get_provision / related / authorities / source).
    The retrieval metric only consumes provision identity fields
    (instrument_title / provision_number), so enrichment would not change
    the measured recall / MRR / nDCG — only the wall-clock time.
    """
    from kg.queries import LegalKGQueries, provisions_for_query

    queries = LegalKGQueries(database=database)
    try:
        provisions = provisions_for_query(query, queries, limit=10)
    except Exception as exc:  # noqa: BLE001 - KG is best-effort by design
        logger.warning("kg_contract_provisions: failed (%s) — returning empty", exc)
        provisions = []
    return [_kg_provision_public(p) for p in provisions]


def kg_expand_chunks(chunk_ids: list[str]) -> list[dict[str, Any]]:
    """Arm E/F — expand Qdrant chunk ids through the Neo4j legal KG."""
    from kg.hybrid import KGContextExpander

    expansion = KGContextExpander().expand_chunks(chunk_ids)
    return [_kg_provision_public(p) for p in expansion.get("provisions", [])]


# --------------------------------------------------------------------------- #
# Arms
# --------------------------------------------------------------------------- #
def arm_a_dense(question: dict[str, Any], top_k: int) -> dict[str, Any]:
    collection = question["collections"][0]
    result = _dense(collection).search(question["question"], top_k=top_k)
    return {
        "chunk_ids": [c.chunk_id for c in result.chunks],
        "kg_provisions": [],
        "kg_source": None,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "retriever": "dense",
    }


def arm_b_sparse(question: dict[str, Any], top_k: int) -> dict[str, Any]:
    collection = question["collections"][0]
    result = _sparse(collection).retrieve(question["question"], top_k=top_k, threshold=0.0)
    return {
        "chunk_ids": [c.chunk_id for c in result.chunks],
        "kg_provisions": [],
        "kg_source": None,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "retriever": "sparse",
    }


def _hybrid(collection: str, with_reranker: bool):
    from app.rag.retrieval import HybridRetriever

    return HybridRetriever(
        dense=_dense(collection),
        sparse=_sparse(collection),
        reranker=_reranker() if with_reranker else None,
    )


def arm_c_hybrid(question: dict[str, Any], top_k: int) -> dict[str, Any]:
    collection = question["collections"][0]
    result = _hybrid(collection, with_reranker=False).retrieve(question["question"], top_k=top_k)
    return {
        "chunk_ids": [c.chunk_id for c in result.chunks],
        "kg_provisions": [],
        "kg_source": None,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "retriever": "hybrid",
    }


def arm_d_kg(question: dict[str, Any], top_k: int) -> dict[str, Any]:
    start = time.monotonic()
    provisions = kg_contract_provisions(question["question"])
    return {
        "chunk_ids": [],
        "kg_provisions": provisions[: max(top_k, 10)],
        "kg_source": "contract",
        "latency_ms": int((time.monotonic() - start) * 1000),
        "error": None,
        "retriever": "kg",
    }


def arm_e_dense_sparse_kg(question: dict[str, Any], top_k: int) -> dict[str, Any]:
    collection = question["collections"][0]
    result = _hybrid(collection, with_reranker=False).retrieve(question["question"], top_k=top_k)
    chunk_ids = [c.chunk_id for c in result.chunks]
    try:
        kg_provisions = kg_expand_chunks(chunk_ids)
    except Exception as exc:  # noqa: BLE001 - KG is best-effort by design
        logger.warning("arm_e kg expansion failed for %s: %s", question["question_id"], exc)
        kg_provisions = []
    return {
        "chunk_ids": chunk_ids,
        "kg_provisions": kg_provisions,
        "kg_source": "expansion",
        "latency_ms": result.latency_ms,
        "error": result.error,
        "retriever": "hybrid",
    }


def arm_f_dense_sparse_kg_rerank(
    question: dict[str, Any],
    top_k: int,
    candidate_k: int,
    final_k: int,
) -> dict[str, Any]:
    collection = question["collections"][0]
    result = _hybrid(collection, with_reranker=True).retrieve(question["question"], top_k=candidate_k)
    chunk_ids = [c.chunk_id for c in result.chunks[:final_k]]
    try:
        kg_provisions = kg_expand_chunks(chunk_ids)
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning("arm_f kg expansion failed for %s: %s", question["question_id"], exc)
        kg_provisions = []
    return {
        "chunk_ids": chunk_ids,
        "kg_provisions": kg_provisions,
        "kg_source": "expansion",
        "latency_ms": result.latency_ms,
        "error": result.error,
        "retriever": "hybrid_rerank",
    }


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def build_arm_runner(
    arm: str,
    top_k: int = 20,
    candidate_k: int = 50,
    final_k: int = 20,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return the per-question runner for the named arm."""
    if arm == "A_dense":
        return lambda q: arm_a_dense(q, top_k)
    if arm == "B_sparse":
        return lambda q: arm_b_sparse(q, top_k)
    if arm == "C_dense_sparse":
        return lambda q: arm_c_hybrid(q, top_k)
    if arm == "D_kg_retrieval":
        return lambda q: arm_d_kg(q, top_k)
    if arm == "E_dense_sparse_kg":
        return lambda q: arm_e_dense_sparse_kg(q, top_k)
    if arm == "F_dense_sparse_kg_rerank":
        return lambda q: arm_f_dense_sparse_kg_rerank(q, top_k, candidate_k, final_k)
    raise ValueError(f"unknown arm: {arm}")
