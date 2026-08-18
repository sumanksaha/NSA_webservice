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

from kg.corpus_ingestion import KGCorpusIngestionEngine
from kg.domain_manifest import (
    AUTHORITIES,
    CONCEPTS,
    CROSS_DOMAIN_RELATIONSHIPS,
    DOMAINS,
    JURISDICTIONS,
    PILOT_INSTRUMENTS,
    PROVISION_CONCEPT_MAP,
    PROVISION_STUBS,
)
from kg.enrichment import LegalSemanticEnricher
from kg.hybrid import KGContextExpander
from kg.ingestion import LegalKGIngestionEngine
from kg.payload_identity import QdrantPayloadStamper
from kg.queries import LegalKGQueries, build_llm_retrieval_contract
from kg.schema import clear_legal_kg, setup_legal_kg_schema
from kg.validation import KGValidator

__all__ = [
    "AUTHORITIES",
    "CONCEPTS",
    "CROSS_DOMAIN_RELATIONSHIPS",
    "DOMAINS",
    "JURISDICTIONS",
    "PILOT_INSTRUMENTS",
    "PROVISION_CONCEPT_MAP",
    "PROVISION_STUBS",
    "KGContextExpander",
    "KGCorpusIngestionEngine",
    "KGValidator",
    "LegalKGIngestionEngine",
    "LegalKGQueries",
    "LegalSemanticEnricher",
    "QdrantPayloadStamper",
    "build_llm_retrieval_contract",
    "clear_legal_kg",
    "setup_legal_kg_schema",
]
