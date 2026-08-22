"""Retrieval composition root — the ONE place the retrieval stack is built.

Historically every consumer hand-assembled classifier → dense → sparse →
ensemble reranker → hybrid inline, and the evaluation harnesses each kept a
private copy that drifted (the multi-domain bug: the sparse store silently
resolved to ``fssai_legal_768`` for every domain because one copy missed the
collection-aware constructor). This module concentrates all construction
decisions so the wiring is tested once and cannot drift.

Interface::

    from app.rag.retrieval.factory import build_hybrid_retriever

    retriever = build_hybrid_retriever(collection_name="env_legal_768")

Components are also exposed individually (:func:`build_dense_retriever`,
:func:`build_sparse_retriever`, :func:`build_reranker`) for harnesses that
need a single arm. All flags resolve through the config seam (``cfg``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_dense_retriever(collection_name: str | None = None):
    """Collection-aware dense retriever over Qdrant."""
    from app.rag.retrieval import DenseRetriever

    return DenseRetriever(collection_name=collection_name or "")


def build_sparse_retriever(collection_name: str | None = None):
    """Sparse retriever honouring the caller's collection name.

    The sparse store MUST honour ``collection_name`` (multi-domain fix,
    2026-08-14, exposed by the live ensemble re-measure): before this, a bare
    ``QdrantStore()`` resolved to ``RAG_QDRANT_COLLECTION`` (fssai_legal_768)
    even for env/commercial/animal/wb_state questions, so sparse + identifier
    arms searched the wrong collection and fused foreign chunks into the pool.
    ``None`` keeps the configured default for the single-domain path.
    """
    from app.rag.qdrant_client import QdrantStore
    from app.rag.retrieval import SparseRetriever
    from app.rag.sparse_embedding import SparseEmbeddingService
    from app.shared.config import cfg

    return SparseRetriever(
        corpus={},
        store=QdrantStore(collection_name=collection_name or None),
        embedder=SparseEmbeddingService(),
        server_bm25=cfg.qdrant_bm25,
    )


def build_reranker():
    """Pipeline reranker honouring RAG_ENSEMBLE_RERANK / RAG_RERANKER_MODEL.

    Returns an :class:`EnsembleReranker` when the ensemble flag is on
    (default), else the plain :class:`Reranker`. Both honour
    ``RAG_RERANKER_MODEL`` (Flask config, else env) so the fine-tuned legal CE
    is a drop-in.

    Remote CE hosting (docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md Part B):
    when ``RAG_RERANKER_ENDPOINT`` is set, the CE head is scored via a TEI
    ``/rerank`` HTTP endpoint instead of a local torch model — injected as the
    ``encoder`` so the sec_act features and all scoring logic are unchanged.
    The remote client lazily builds the local CE as its fallback when
    ``RAG_RERANKER_REMOTE_FALLBACK`` is on (default), degrading
    remote → local → sec_act features-only.
    """
    # Import through the package re-export so tests that patch
    # ``app.rag.retrieval.Reranker`` (e.g. the pipeline wiring tests) keep
    # working for the flag-off path.
    from app.rag.retrieval import EnsembleReranker, Reranker
    from app.shared.config import cfg

    model_name = cfg.reranker_model
    if cfg.ensemble_rerank:
        kwargs: dict = {
            "model_name": model_name,
            "ce_head": cfg.ensemble_ce_head,
            "ce_weight": cfg.ensemble_ce_weight,
        }
        if cfg.reranker_endpoint:
            from app.rag.retrieval.remote_reranker import RemoteRerankClient

            kwargs["encoder"] = RemoteRerankClient(
                endpoint=cfg.reranker_endpoint,
                token=cfg.reranker_token or None,
                timeout=cfg.reranker_timeout,
                local_model=model_name if cfg.remote_rerank_fallback else None,
                mode=cfg.reranker_mode,
            )
        return EnsembleReranker(**kwargs)
    return Reranker(model_name=model_name)


def build_hybrid_retriever(collection_name: str | None = None):
    """Assemble the full stack: dense + sparse (+ BM25) + reranker → hybrid.

    RAG_QDRANT_BM25: Qdrant computes the BM25 vector in-cluster
    (``Qdrant/bm25``) — no local fastembed at query time. Verified live
    2026-08-16 against the provisioned cluster; free on the free tier.
    """
    from app.rag.retrieval import HybridRetriever

    return HybridRetriever(
        dense=build_dense_retriever(collection_name),
        sparse=build_sparse_retriever(collection_name),
        reranker=build_reranker(),
    )


__all__ = [
    "build_dense_retriever",
    "build_hybrid_retriever",
    "build_reranker",
    "build_sparse_retriever",
]
