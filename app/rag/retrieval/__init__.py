"""RAG retrieval sub-package — Phase 1 deliverable.

Exports the retrieval-layer components so callers can do::

    from app.rag.retrieval import HybridRetriever, QueryClassifier, RetrievalLogger

Also exports the parallel legal-structure and evidence layer (legal identity,
hierarchy, reference extraction, reference graph, temporal validity, provision
versions, evidence selector, evidence metrics).  These are independently
feature-flagged and do NOT modify the CE reranking experiment.
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
from app.rag.retrieval.remote_reranker import RemoteRerankClient
from app.rag.retrieval.reranker import EnsembleReranker, Reranker
from app.rag.retrieval.sparse_retriever import SparseRetriever

# Parallel legal-structure and evidence layer (feature-flagged, does not
# affect CE reranking experiment)
from app.rag.retrieval.legal_identity import LegalIdentity, parse_legal_identity
from app.rag.retrieval.legal_hierarchy import (
    SectionRelationship,
    _legal_hierarchy_enabled,
    adjacent_section,
    compare_identities,
    exact_section_match,
    hierarchy_depth,
    hierarchy_proximity,
    parent_child,
    parse_section_chain,
    same_act,
    same_chapter,
    same_section_family,
    sibling,
    subsection_relationship,
    section_base,
)
from app.rag.retrieval.reference_extractor import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    Reference,
    extract_references,
    high_confidence_refs,
)
from app.rag.retrieval.reference_graph import (
    EDGE_REFS,
    ReferenceEdge,
    ReferenceGraph,
    expand_candidates,
    expand_references,
    get_reference_graph,
)
from app.rag.retrieval.temporal_validity import (
    VALIDITY_INVALID,
    VALIDITY_UNKNOWN,
    VALIDITY_VALID,
    ValidityResult,
    is_valid,
    temporal_validity_score,
)
from app.rag.retrieval.provision_versions import (
    ProvisionVersion,
    VersionFamily,
    build_provision_family_id,
    extract_provision_version,
    group_versions,
)
from app.rag.retrieval.evidence_selector import (
    EvidenceItem,
    EvidenceSet,
    select_evidence_set,
)
from app.rag.retrieval.evidence_metrics import (
    EvidenceMetricResult,
    EvidenceBatchResult,
    evidence_set_recall,
    evidence_set_precision,
    evidence_set_f1,
    evidence_coverage_at_k,
    evaluate_evidence_set,
    evaluate_evidence_batch,
)

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
    "RemoteRerankClient",
    # Legal structure & evidence layer
    "LegalIdentity",
    "parse_legal_identity",
    "SectionRelationship",
    "parse_section_chain",
    "section_base",
    "hierarchy_depth",
    "hierarchy_proximity",
    "exact_section_match",
    "same_act",
    "same_chapter",
    "same_section_family",
    "parent_child",
    "sibling",
    "adjacent_section",
    "subsection_relationship",
    "compare_identities",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_LOW",
    "Reference",
    "extract_references",
    "high_confidence_refs",
    "EDGE_REFS",
    "ReferenceEdge",
    "ReferenceGraph",
    "expand_references",
    "expand_candidates",
    "get_reference_graph",
    "VALIDITY_VALID",
    "VALIDITY_INVALID",
    "VALIDITY_UNKNOWN",
    "ValidityResult",
    "is_valid",
    "temporal_validity_score",
    "ProvisionVersion",
    "VersionFamily",
    "build_provision_family_id",
    "extract_provision_version",
    "group_versions",
    "EvidenceItem",
    "EvidenceSet",
    "select_evidence_set",
    "EvidenceMetricResult",
    "EvidenceBatchResult",
    "evidence_set_recall",
    "evidence_set_precision",
    "evidence_set_f1",
    "evidence_coverage_at_k",
    "evaluate_evidence_set",
    "evaluate_evidence_batch",
]
