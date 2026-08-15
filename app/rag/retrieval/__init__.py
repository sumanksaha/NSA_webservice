"""RAG retrieval sub-package — Phase 1 deliverable.

Exports the retrieval-layer components so callers can do::

    from app.rag.retrieval import HybridRetriever, QueryClassifier, RetrievalLogger
"""

from app.rag.retrieval.dense_retriever import DenseRetriever
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.logger import RetrievalAuditLog, RetrievalLogger
from app.rag.retrieval.query_classifier import (
    AuthorityQueryParser,
    CaseLawQueryParser,
    JurisdictionQueryParser,
    QueryClassifier,
    QueryParser,
    QueryType,
    SectionQueryParser,
)
from app.rag.retrieval.reranker import EnsembleReranker, Reranker
from app.rag.retrieval.sparse_retriever import SparseRetriever

__all__ = [
    "DenseRetriever",
    "HybridRetriever",
    "Reranker",
    "EnsembleReranker",
    "SparseRetriever",
    "QueryClassifier",
    "QueryType",
    "QueryParser",
    "SectionQueryParser",
    "AuthorityQueryParser",
    "CaseLawQueryParser",
    "JurisdictionQueryParser",
    "RetrievalLogger",
    "RetrievalAuditLog",
]
