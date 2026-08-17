"""RAG retrieval sub-package — Phase 1 deliverable.

Exports the retrieval-layer components so callers can do::

    from app.rag.retrieval import HybridRetriever, QueryClassifier, RetrievalLogger

Also exports the parallel legal-structure and evidence layer (legal identity,
hierarchy, reference extraction, reference graph, temporal validity, provision
versions, evidence selector, evidence metrics).  These are independently
feature-flagged and do NOT modify the CE reranking experiment.
"""

from app.rag.retrieval.dense_retriever import DenseRetriever
from app.rag.retrieval.evidence_metrics import (
    EvidenceBatchResult,
    EvidenceMetricResult,
    evaluate_evidence_batch,
    evaluate_evidence_set,
    evidence_coverage_at_k,
    evidence_set_f1,
    evidence_set_precision,
    evidence_set_recall,
)
from app.rag.retrieval.evidence_selector import (
    EvidenceItem,
    EvidenceSet,
    select_evidence_set,
)
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.legal_hierarchy import (
    SectionRelationship,
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
    section_base,
    sibling,
    subsection_relationship,
)

# Parallel legal-structure and evidence layer (feature-flagged, does not
# affect CE reranking experiment)
from app.rag.retrieval.legal_identity import LegalIdentity, parse_legal_identity
from app.rag.retrieval.logger import RetrievalAuditLog, RetrievalLogger
from app.rag.retrieval.provision_versions import (
    ProvisionVersion,
    VersionFamily,
    build_provision_family_id,
    extract_provision_version,
    group_versions,
)
from app.rag.retrieval.query_classifier import (
    AuthorityQueryParser,
    CaseLawQueryParser,
    JurisdictionQueryParser,
    QueryClassifier,
    QueryParser,
    QueryType,
    SectionQueryParser,
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
from app.rag.retrieval.remote_reranker import RemoteRerankClient
from app.rag.retrieval.reranker import EnsembleReranker, Reranker
from app.rag.retrieval.sparse_retriever import SparseRetriever
from app.rag.retrieval.temporal_validity import (
    VALIDITY_INVALID,
    VALIDITY_UNKNOWN,
    VALIDITY_VALID,
    ValidityResult,
    is_valid,
    temporal_validity_score,
)

__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "EDGE_REFS",
    "VALIDITY_INVALID",
    "VALIDITY_UNKNOWN",
    "VALIDITY_VALID",
    "AuthorityQueryParser",
    "CaseLawQueryParser",
    "DenseRetriever",
    "EnsembleReranker",
    "EvidenceBatchResult",
    "EvidenceItem",
    "EvidenceMetricResult",
    "EvidenceSet",
    "HybridRetriever",
    "JurisdictionQueryParser",
    # Legal structure & evidence layer
    "LegalIdentity",
    "ProvisionVersion",
    "QueryClassifier",
    "QueryParser",
    "QueryType",
    "Reference",
    "ReferenceEdge",
    "ReferenceGraph",
    "RemoteRerankClient",
    "Reranker",
    "RetrievalAuditLog",
    "RetrievalLogger",
    "SectionQueryParser",
    "SectionRelationship",
    "SparseRetriever",
    "ValidityResult",
    "VersionFamily",
    "adjacent_section",
    "build_provision_family_id",
    "compare_identities",
    "evaluate_evidence_batch",
    "evaluate_evidence_set",
    "evidence_coverage_at_k",
    "evidence_set_f1",
    "evidence_set_precision",
    "evidence_set_recall",
    "exact_section_match",
    "expand_candidates",
    "expand_references",
    "extract_provision_version",
    "extract_references",
    "get_reference_graph",
    "group_versions",
    "hierarchy_depth",
    "hierarchy_proximity",
    "high_confidence_refs",
    "is_valid",
    "parent_child",
    "parse_legal_identity",
    "parse_section_chain",
    "same_act",
    "same_chapter",
    "same_section_family",
    "section_base",
    "select_evidence_set",
    "sibling",
    "subsection_relationship",
    "temporal_validity_score",
]
