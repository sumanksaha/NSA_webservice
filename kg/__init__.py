"""Multi-domain legal Knowledge Graph package for the NSA Webservice project.

Provides:
- ``LegalKGIngestionEngine`` — ingests legal instruments, provisions, and
  relationships into Neo4j (Phase 3 pilot).
- ``LegalKGQueries`` — Cypher retrieval queries forming the graph-RAG interface.
- ``KGValidator`` — structural + legal validation of the graph.
- ``setup_legal_kg_schema`` / ``clear_legal_kg`` — schema management.

The legal KG is a SEPARATE namespace from the existing case-file KG
(Case/FBO/Inspector labels) — they coexist peacefully on the same Neo4j
instance.
"""

from kg.schema import setup_legal_kg_schema, clear_legal_kg
from kg.ingestion import LegalKGIngestionEngine
from kg.corpus_ingestion import KGCorpusIngestionEngine
from kg.queries import LegalKGQueries, build_llm_retrieval_contract
from kg.validation import KGValidator
from kg.enrichment import LegalSemanticEnricher
from kg.hybrid import KGContextExpander
from kg.payload_identity import QdrantPayloadStamper
from kg.domain_manifest import (
    DOMAINS,
    JURISDICTIONS,
    AUTHORITIES,
    CONCEPTS,
    PILOT_INSTRUMENTS,
    CROSS_DOMAIN_RELATIONSHIPS,
    PROVISION_CONCEPT_MAP,
    PROVISION_STUBS,
)

__all__ = [
    "setup_legal_kg_schema",
    "clear_legal_kg",
    "LegalKGIngestionEngine",
    "KGCorpusIngestionEngine",
    "LegalKGQueries",
    "KGValidator",
    "LegalSemanticEnricher",
    "KGContextExpander",
    "QdrantPayloadStamper",
    "build_llm_retrieval_contract",
    "DOMAINS",
    "JURISDICTIONS",
    "AUTHORITIES",
    "CONCEPTS",
    "PILOT_INSTRUMENTS",
    "CROSS_DOMAIN_RELATIONSHIPS",
    "PROVISION_CONCEPT_MAP",
    "PROVISION_STUBS",
]
