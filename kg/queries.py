"""Legal Knowledge Graph — Cypher retrieval queries.

Reusable Cypher queries that the RAG application calls to get *structured
legal evidence* from the graph.  Each function returns JSON-serialisable
Python dicts/lists — never raw Neo4j records — so the query layer can be
called from Flask routes or Celery tasks without driver knowledge.

These queries form the ``graph-RAG interface`` described in the task spec:

    Query → domain classification → entity extraction →
    vector retrieval → graph expansion → evidence filtering → LLM context

The KG answers: "What law?", "Which provision?", "Why is it relevant?",
"Which authority?", "What obligation/prohibition?", "What other domains
interact?", "What's the source?", "Was it applicable then?"
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LegalKGQueries:
    """Cypher retrieval queries for the legal Knowledge Graph.

    Args:
        driver: Optional pre-built Neo4j driver (injected for tests).
        database: Neo4j database name (default from NEO4J_DATABASE env).
    """

    def __init__(self, driver: Any | None = None, database: str | None = None):
        self._driver = driver
        self._database = database

    def _get_driver(self) -> Any:
        if self._driver is None:
            from app.services.neo4j_graph import _get_driver

            self._driver = _get_driver()
        return self._driver

    def _execute(self, cypher: str, params: dict | None = None) -> list[dict]:
        database = self._database or os.environ.get("NEO4J_DATABASE", "neo4j")
        result = self._get_driver().execute_query(cypher, parameters_=params or {}, database_=database)
        return [dict(r) for r in result.records]

    # ------------------------------------------------------------------ #
    # 1. get_provision(provision_id)
    # ------------------------------------------------------------------ #

    def get_provision(self, provision_id: str) -> dict[str, Any] | None:
        """Retrieve a provision + its full provenance chain.

        Returns Instrument → Provision → Chunk → Document → Source.
        """
        results = self._execute(
            """
            MATCH (p:LegalProvision {provision_id: $pid})
            OPTIONAL MATCH (i)-[:CONTAINS]->(p)
            WHERE i.issuing_authority IS NOT NULL OR i.instrument_type IS NOT NULL
            OPTIONAL MATCH (p)-[:SUPPORTED_BY]->(ch:Chunk)
            OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(doc:Document)
            OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            OPTIONAL MATCH (p)-[:APPLIES_TO|IMPOSES_DUTY|CREATES_OFFENCE|PRESCRIBES_PENALTY|REQUIRES|GRANTS_POWER_TO|ENFORCED_BY]->(c)
            OPTIONAL MATCH (p)-[:BELONGS_TO]->(jur:Jurisdiction)
            RETURN
                p.provision_id AS provision_id,
                p.provision_number AS provision_number,
                p.title AS title,
                p.provision_text AS text,
                p.status AS status,
                p.effective_from AS effective_from,
                p.effective_to AS effective_to,
                p.confidence AS confidence,
                p.source AS source,
                i.instrument_id AS instrument_id,
                i.title AS instrument_title,
                i.short_title AS instrument_short_title,
                i.instrument_type AS instrument_type,
                i.legal_domain AS instrument_domain,
                i.jurisdiction AS instrument_jurisdiction,
                i.issuing_authority AS authority,
                i.enactment_date AS enactment_date,
                i.effective_date AS instrument_effective_date,
                i.status AS instrument_status,
                i.source_url AS source_url,
                i.last_verified AS last_verified,
                d.domain_name AS legal_domain,
                ch.chunk_id AS chunk_id,
                ch.chunk_text AS chunk_text,
                ch.qdrant_point_id AS qdrant_point_id,
                doc.document_id AS document_id,
                doc.title AS document_title,
                doc.source_uri AS document_uri,
                collect(DISTINCT c.name) AS concepts,
                collect(DISTINCT c.concept_id) AS concept_ids
            LIMIT 1
            """,
            {"pid": provision_id},
        )
        if not results:
            return None
        row = results[0]
        # Unwrap single-value fields
        return {
            "provision_id": _unwrap(row["provision_id"]),
            "provision_number": _unwrap(row["provision_number"]),
            "title": _unwrap(row["title"]),
            "text": _unwrap(row["text"]),
            "status": _unwrap(row["status"]),
            "effective_from": _unwrap(row["effective_from"]),
            "effective_to": _unwrap(row["effective_to"]),
            "confidence": _unwrap(row["confidence"]),
            "source": _unwrap(row["source"]),
            "instrument": {
                "instrument_id": _unwrap(row["instrument_id"]),
                "title": _unwrap(row["instrument_title"]),
                "short_title": _unwrap(row["instrument_short_title"]),
                "type": _unwrap(row["instrument_type"]),
                "legal_domain": _unwrap(row["instrument_domain"]),
                "jurisdiction": _unwrap(row["instrument_jurisdiction"]),
                "authority": _unwrap(row["authority"]),
                "enactment_date": _unwrap(row["enactment_date"]),
                "effective_date": _unwrap(row["instrument_effective_date"]),
                "status": _unwrap(row["instrument_status"]),
                "source_url": _unwrap(row["source_url"]),
                "last_verified": _unwrap(row["last_verified"]),
            },
            "legal_domain": _unwrap(row["legal_domain"]),
            "concepts": [c for c in (_unwrap(row["concepts"]) or []) if c],
            "concept_ids": [c for c in (_unwrap(row["concept_ids"]) or []) if c],
            "provenance": {
                "chunk_id": _unwrap(row["chunk_id"]),
                "chunk_text": _unwrap(row["chunk_text"]),
                "qdrant_point_id": _unwrap(row["qdrant_point_id"]),
                "document_id": _unwrap(row["document_id"]),
                "document_title": _unwrap(row["document_title"]),
                "document_uri": _unwrap(row["document_uri"]),
            },
        }

    # ------------------------------------------------------------------ #
    # 2. get_instrument(instrument_id)
    # ------------------------------------------------------------------ #

    def get_instrument(self, instrument_id: str) -> dict[str, Any] | None:
        """Retrieve an instrument + its provisions + authorities."""
        results = self._execute(
            """
            MATCH (i {instrument_id: $iid})
            OPTIONAL MATCH (i)-[:CONTAINS]->(p:LegalProvision)
            OPTIONAL MATCH (i)-[:ISSUED_BY]->(a:Authority)
            OPTIONAL MATCH (i)-[:APPLIES_TO_JURISDICTION]->(j:Jurisdiction)
            OPTIONAL MATCH (i)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            OPTIONAL MATCH (i)-[:RELATED_TO|AMENDS|REPEALS|REPLACES]->(related)
            RETURN
                i.instrument_id AS instrument_id,
                i.title AS title,
                i.short_title AS short_title,
                i.instrument_type AS type,
                i.legal_domain AS domain,
                i.jurisdiction AS jurisdiction,
                i.issuing_authority AS authority_id,
                i.enactment_date AS enactment_date,
                i.effective_date AS effective_date,
                i.status AS status,
                i.version AS version,
                i.source_url AS source_url,
                a.name AS authority_name,
                j.jurisdiction_id AS juris_id,
                d.domain_name AS legal_domain,
                collect(DISTINCT p.provision_id) AS provisions,
                collect(DISTINCT p.provision_number) AS section_numbers,
                collect(DISTINCT related.instrument_id) AS related_instruments
            """,
            {"iid": instrument_id},
        )
        if not results:
            return None
        row = results[0]
        return {
            "instrument_id": _unwrap(row["instrument_id"]),
            "title": _unwrap(row["title"]),
            "short_title": _unwrap(row["short_title"]),
            "type": _unwrap(row["type"]),
            "legal_domain": _unwrap(row["legal_domain"]) or _unwrap(row["domain"]),
            "jurisdiction": _unwrap(row["jurisdiction"]),
            "authority": _unwrap(row["authority_name"]),
            "enactment_date": _unwrap(row["enactment_date"]),
            "effective_date": _unwrap(row["effective_date"]),
            "status": _unwrap(row["status"]),
            "version": _unwrap(row["version"]),
            "source_url": _unwrap(row["source_url"]),
            "provisions": [p for p in (_unwrap(row["provisions"]) or []) if p],
            "section_numbers": [s for s in (_unwrap(row["section_numbers"]) or []) if s],
            "related_instruments": [r for r in (_unwrap(row["related_instruments"]) or []) if r],
        }

    # ------------------------------------------------------------------ #
    # 3. get_related_provisions(provision_id)
    # ------------------------------------------------------------------ #

    def get_related_provisions(self, provision_id: str) -> list[dict[str, Any]]:
        """Get provisions related to this one (cross-domain + intra-domain).

        Includes CROSS_REFERENCES, INTERACTS_WITH, COMPLEMENTS, DEPENDS_ON,
        OVERLAPS_WITH, and CONTAINS/HAS_SUBSECTION hierarchy edges.
        """
        results = self._execute(
            """
            MATCH (p:LegalProvision {provision_id: $pid})-[r]->(other:LegalProvision)
            WHERE type(r) IN [
                'CROSS_REFERENCES', 'INTERACTS_WITH', 'COMPLEMENTS',
                'DEPENDS_ON', 'OVERLAPS_WITH', 'HAS_SUBSECTION',
                'HAS_CLAUSE', 'HAS_SCHEDULE'
            ]
            MATCH (other)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            OPTIONAL MATCH (other)-[:SUPPORTED_BY]->(ch:Chunk)
            OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(doc:Document)
            RETURN
                type(r) AS relationship_type,
                r.evidence AS evidence,
                r.confidence AS confidence,
                other.provision_id AS target_id,
                other.provision_number AS target_number,
                other.title AS target_title,
                d.domain_name AS target_domain,
                doc.source_uri AS source_uri
            ORDER BY confidence DESC, relationship_type ASC
            """,
            {"pid": provision_id},
        )
        return [_row_to_related_provision(row) for row in results]

    # ------------------------------------------------------------------ #
    # 4. get_cross_domain_laws(concept_name)
    # ------------------------------------------------------------------ #

    def get_cross_domain_laws(self, concept: str) -> list[dict[str, Any]]:
        """Get all legal provisions across all domains that apply to a concept.

        Example: concept='Slaughterhouse' returns provisions from
        FOOD_SAFETY, ANIMAL_SLAUGHTER, ENVIRONMENT_POLLUTION, MUNICIPAL.

        (Fixed 2026-08-12: the ``ORDER BY`` referenced ``d.priority`` /
        ``i.instrument_id`` after an aggregation ``RETURN``, which put them
        out of scope and raised a Cypher error; both are now returned as
        aliases.  Also fixed the ``GRANST_POWER_TO`` typo.)
        """
        results = self._execute(
            """
            MATCH (c:LegalConcept {name: $concept})<-[:APPLIES_TO|RELATES_TO|REQUIRES]-(p:LegalProvision)
            MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            MATCH (i)-[:CONTAINS]->(p)
            OPTIONAL MATCH (p)-[:GRANTS_POWER_TO|ENFORCED_BY]->(a:Authority)
            OPTIONAL MATCH (p)-[:SUPPORTED_BY]->(ch:Chunk)
            OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(doc:Document)
            RETURN
                p.provision_id AS provision_id,
                p.provision_number AS provision_number,
                p.title AS title,
                d.domain_name AS domain,
                d.priority AS domain_priority,
                i.title AS instrument_title,
                i.instrument_id AS instrument_id,
                collect(DISTINCT a.name) AS authorities,
                doc.source_uri AS source_uri,
                ch.chunk_text AS chunk_text,
                doc.title AS document_title
            ORDER BY domain_priority ASC, instrument_id ASC
            """,
            {"concept": concept},
        )
        return [_row_to_cross_domain_result(row) for row in results]

    # ------------------------------------------------------------------ #
    # 5. get_applicable_laws(business_activity)
    # ------------------------------------------------------------------ #

    def get_applicable_laws(self, business_activity: str) -> dict[str, Any]:
        """Get all laws relevant to a business activity.

        Returns structured evidence grouped by domain.
        """
        results = self._execute(
            """
            MATCH (ba:BusinessActivity {activity_id: $activity})
            // Find provisions that apply to this activity directly
            OPTIONAL MATCH (p:LegalProvision)-[:APPLIES_TO]->(ba)
            // Also find provisions that apply to concepts related to this activity
            OPTIONAL MATCH (ba)-[:INVOLVES]->(c:LegalConcept)<-[:APPLIES_TO]-(p2:LegalProvision)
            WITH collect(DISTINCT p) + collect(DISTINCT p2) AS all_provisions
            UNWIND all_provisions AS prov
            WITH DISTINCT prov WHERE prov IS NOT NULL
            MATCH (prov)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            MATCH (i)-[:CONTAINS]->(prov)
            OPTIONAL MATCH (prov)-[:ENFORCED_BY|GRANTS_POWER_TO]->(a:Authority)
            OPTIONAL MATCH (prov)-[:SUPPORTED_BY]->(ch:Chunk)
            OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(doc:Document)
            RETURN
                d.domain_name AS domain,
                d.priority AS domain_priority,
                i.instrument_id AS instrument_id,
                i.title AS instrument_title,
                prov.provision_id AS provision_id,
                prov.provision_number AS provision_number,
                prov.title AS provision_title,
                collect(DISTINCT a.name) AS authorities,
                doc.source_uri AS source_uri
            ORDER BY domain_priority ASC
            """,
            {"activity": business_activity},
        )

        # Group by domain
        grouped: dict[str, list[dict]] = {}
        for row in results:
            domain = _unwrap(row["domain"])
            if not domain:
                continue
            grouped.setdefault(domain, []).append({
                "instrument_id": _unwrap(row["instrument_id"]),
                "instrument_title": _unwrap(row["instrument_title"]),
                "provision_id": _unwrap(row["provision_id"]),
                "provision_number": _unwrap(row["provision_number"]),
                "provision_title": _unwrap(row["provision_title"]),
                "authorities": [a for a in (_unwrap(row["authorities"]) or []) if a],
                "source_uri": _unwrap(row["source_uri"]),
            })

        return {"business_activity": business_activity, "domains": grouped}

    # ------------------------------------------------------------------ #
    # 6. get_authorities(provision_id)
    # ------------------------------------------------------------------ #

    def get_authorities(self, provision_id: str) -> list[dict[str, Any]]:
        """Get authorities linked to a provision (enforced by / grants power to)."""
        results = self._execute(
            """
            MATCH (p:LegalProvision {provision_id: $pid})
            MATCH (p)-[r:ENFORCED_BY|GRANTS_POWER_TO|REQUIRES_AUTHORIZATION_FROM]->(a:Authority)
            RETURN
                a.authority_id AS authority_id,
                a.name AS name,
                a.short_name AS short_name,
                a.jurisdiction AS jurisdiction,
                a.type AS type,
                type(r) AS relationship_type,
                r.evidence AS evidence,
                r.confidence AS confidence
            ORDER BY confidence DESC
            """,
            {"pid": provision_id},
        )
        return [
            {
                "authority_id": _unwrap(row["authority_id"]),
                "name": _unwrap(row["name"]),
                "short_name": _unwrap(row["short_name"]),
                "jurisdiction": _unwrap(row["jurisdiction"]),
                "type": _unwrap(row["type"]),
                "relationship_type": _unwrap(row["relationship_type"]),
                "evidence": _unwrap(row["evidence"]),
                "confidence": _unwrap(row["confidence"]),
            }
            for row in results
        ]

    # ------------------------------------------------------------------ #
    # 7. get_enforcement_powers(provision_id)
    # ------------------------------------------------------------------ #

    def get_enforcement_powers(self, provision_id: str) -> dict[str, Any]:
        """Get offences, penalties, notices, and procedures linked to a provision."""
        results = self._execute(
            """
            MATCH (p:LegalProvision {provision_id: $pid})
            OPTIONAL MATCH (p)-[:CREATES_OFFENCE]->(o:Offence)
            OPTIONAL MATCH (o)-[:HAS_PENALTY]->(pen:Penalty)
            OPTIONAL MATCH (p)-[:PRESCRIBES_PENALTY]->(pen2:Penalty)
            OPTIONAL MATCH (p)-[:TRIGGERS_NOTICE]->(n:Notice)
            OPTIONAL MATCH (p)-[:PRESCRIBES]->(proc:Procedure)
            OPTIONAL MATCH (p)-[:IMPOSES_DUTY]->(ob:Obligation)
            OPTIONAL MATCH (p)-[:PROHIBITS]->(pr:Prohibition)
            OPTIONAL MATCH (p)-[:GRANTS_PERMISSION]->(perm:Permission)
            OPTIONAL MATCH (p)-[:REQUIRES]->(req:Permission)
            RETURN
                collect(DISTINCT o.offence_id) AS offences,
                collect(DISTINCT o.title) AS offence_titles,
                collect(DISTINCT pen.penalty_id) AS penalties,
                collect(DISTINCT pen.title) AS penalty_titles,
                collect(DISTINCT pen2.penalty_id) AS direct_penalties,
                collect(DISTINCT n.notice_id) AS notices,
                collect(DISTINCT proc.procedure_id) AS procedures,
                collect(DISTINCT ob.obligation_id) AS obligations,
                collect(DISTINCT pr.prohibition_id) AS prohibitions,
                collect(DISTINCT perm.permission_id) AS permissions,
                collect(DISTINCT req.permission_id) AS required_permits
            LIMIT 1
            """,
            {"pid": provision_id},
        )
        if not results:
            return {
                "offences": [],
                "penalties": [],
                "notices": [],
                "procedures": [],
                "obligations": [],
                "prohibitions": [],
                "permissions": [],
                "required_permits": [],
            }
        row = results[0]
        return {
            "offences": [_unwrap_list(row["offences"])],
            "offence_titles": _unwrap_list(row["offence_titles"]),
            "penalties": _unwrap_list(row["penalties"]),
            "penalty_titles": _unwrap_list(row["penalty_titles"]),
            "direct_penalties": _unwrap_list(row["direct_penalties"]),
            "notices": _unwrap_list(row["notices"]),
            "procedures": _unwrap_list(row["procedures"]),
            "obligations": _unwrap_list(row["obligations"]),
            "prohibitions": _unwrap_list(row["prohibitions"]),
            "permissions": _unwrap_list(row["permissions"]),
            "required_permits": _unwrap_list(row["required_permits"]),
        }

    # ------------------------------------------------------------------ #
    # 8. get_source_evidence(provision_id)
    # ------------------------------------------------------------------ #

    def get_source_evidence(self, provision_id: str) -> dict[str, Any] | None:
        """Trace provenance: Provision → Chunk → Document → Source."""
        results = self._execute(
            """
            MATCH (p:LegalProvision {provision_id: $pid})
            OPTIONAL MATCH (p)-[:SUPPORTED_BY]->(ch:Chunk)
            OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(doc:Document)
            OPTIONAL MATCH (p)-[:SOURCE_OF]->(doc2:Document)
            OPTIONAL MATCH (p)<-[:CONTAINS]-(i)
            OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            RETURN
                p.provision_id AS provision_id,
                p.provision_number AS provision_number,
                p.title AS provision_title,
                p.confidence AS confidence,
                p.source AS source_type,
                ch.chunk_id AS chunk_id,
                ch.chunk_text AS chunk_text,
                ch.qdrant_point_id AS qdrant_point_id,
                doc.document_id AS doc_id,
                doc.title AS doc_title,
                doc.source_uri AS doc_uri,
                doc.document_type AS doc_type,
                doc.legal_domain AS doc_domain,
                i.instrument_id AS instrument_id,
                i.title AS instrument_title,
                d.domain_name AS legal_domain
            LIMIT 1
            """,
            {"pid": provision_id},
        )
        if not results:
            return None
        row = results[0]
        return {
            "provision_id": _unwrap(row["provision_id"]),
            "provision_number": _unwrap(row["provision_number"]),
            "provision_title": _unwrap(row["provision_title"]),
            "confidence": _unwrap(row["confidence"]),
            "source_type": _unwrap(row["source_type"]),
            "chunk": {
                "chunk_id": _unwrap(row["chunk_id"]),
                "chunk_text": _unwrap(row["chunk_text"]),
                "qdrant_point_id": _unwrap(row["qdrant_point_id"]),
            },
            "document": {
                "document_id": _unwrap(row["doc_id"]),
                "title": _unwrap(row["doc_title"]),
                "source_uri": _unwrap(row["doc_uri"]),
                "document_type": _unwrap(row["doc_type"]),
                "legal_domain": _unwrap(row["doc_domain"]),
            },
            "instrument": {
                "instrument_id": _unwrap(row["instrument_id"]),
                "title": _unwrap(row["instrument_title"]),
            },
            "legal_domain": _unwrap(row["legal_domain"]),
        }

    # ------------------------------------------------------------------ #
    # 9. get_current_provisions(concept)
    # ------------------------------------------------------------------ #

    def get_current_provisions(self, concept: str) -> list[dict[str, Any]]:
        """Get only current (non-repealed) provisions related to a concept."""
        results = self._execute(
            """
            MATCH (c:LegalConcept {name: $concept})<-[:APPLIES_TO|RELATES_TO|REQUIRES]-(p:LegalProvision)
            WHERE p.status = 'current'
            OPTIONAL MATCH (p)<-[:CONTAINS]-(i)
            OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            OPTIONAL MATCH (p)-[:SUPPORTED_BY]->(ch:Chunk)
            OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(doc:Document)
            RETURN
                p.provision_id AS provision_id,
                p.provision_number AS provision_number,
                p.title AS title,
                p.effective_from AS effective_from,
                p.confidence AS confidence,
                i.instrument_id AS instrument_id,
                i.title AS instrument_title,
                d.domain_name AS legal_domain,
                doc.source_uri AS source_uri
            ORDER BY d.priority ASC
            """,
            {"concept": concept},
        )
        return [
            {
                "provision_id": _unwrap(row["provision_id"]),
                "provision_number": _unwrap(row["provision_number"]),
                "title": _unwrap(row["title"]),
                "effective_from": _unwrap(row["effective_from"]),
                "confidence": _unwrap(row["confidence"]),
                "instrument_id": _unwrap(row["instrument_id"]),
                "instrument_title": _unwrap(row["instrument_title"]),
                "legal_domain": _unwrap(row["legal_domain"]),
                "source_uri": _unwrap(row["source_uri"]),
            }
            for row in results
        ]

    # ------------------------------------------------------------------ #
    # 10. get_domain_provisions(domain)
    # ------------------------------------------------------------------ #

    def get_domain_provisions(self, domain: str) -> list[dict[str, Any]]:
        """Get all provisions in a legal domain."""
        results = self._execute(
            """
            MATCH (d:LegalDomain {domain_name: $domain})
            MATCH (p:LegalProvision)-[:BELONGS_TO_DOMAIN]->(d)
            MATCH (i)-[:CONTAINS]->(p)
            OPTIONAL MATCH (p)-[:SUPPORTED_BY]->(ch:Chunk)
            OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(doc:Document)
            RETURN
                p.provision_id AS provision_id,
                p.provision_number AS provision_number,
                p.title AS title,
                p.status AS status,
                p.effective_from AS effective_from,
                p.confidence AS confidence,
                i.instrument_id AS instrument_id,
                i.title AS instrument_title,
                i.authority AS authority,
                doc.source_uri AS source_uri
            ORDER BY p.provision_number ASC
            """,
            {"domain": domain},
        )
        return [
            {
                "provision_id": _unwrap(row["provision_id"]),
                "provision_number": _unwrap(row["provision_number"]),
                "title": _unwrap(row["title"]),
                "status": _unwrap(row["status"]),
                "effective_from": _unwrap(row["effective_from"]),
                "confidence": _unwrap(row["confidence"]),
                "instrument_id": _unwrap(row["instrument_id"]),
                "instrument_title": _unwrap(row["instrument_title"]),
                "authority": _unwrap(row["authority"]),
                "source_uri": _unwrap(row["source_uri"]),
            }
            for row in results
        ]

    # ------------------------------------------------------------------ #
    # Full-text provenance search (for LLM evidence tracing)
    # ------------------------------------------------------------------ #

    def get_provenance_chain(self, provision_id: str) -> dict[str, Any]:
        """Full provenance chain: Provision → Chunk → Document → Source.

        Returns the complete trace so an LLM can answer "why does the graph
        believe this?"
        """
        return self.get_provision(provision_id) or {}

    def get_all_domains(self) -> list[dict[str, Any]]:
        """List all legal domains in the graph."""
        results = self._execute(
            """
            MATCH (d:LegalDomain)
            RETURN d.domain_name AS domain_name,
                   d.description AS description,
                   d.jurisdiction AS jurisdiction,
                   d.priority AS priority
            ORDER BY d.priority ASC
            """
        )
        return [
            {
                "domain_name": _unwrap(row["domain_name"]),
                "description": _unwrap(row["description"]),
                "jurisdiction": _unwrap(row["jurisdiction"]),
                "priority": _unwrap(row["priority"]),
            }
            for row in results
        ]

    def get_instruments_by_domain(self, domain: str | None = None) -> list[dict[str, Any]]:
        """List all instruments, optionally filtered by domain."""
        if domain:
            cypher = """
                MATCH (d:LegalDomain {domain_name: $domain})<-[:BELONGS_TO_DOMAIN]-(i)
                RETURN i.instrument_id AS instrument_id,
                       i.title AS title,
                       i.short_title AS short_title,
                       i.instrument_type AS type,
                       d.domain_name AS domain,
                       i.status AS status,
                       i.enactment_date AS enactment_date
                ORDER BY i.enactment_date ASC
                """
            params = {"domain": domain}
        else:
            cypher = """
                MATCH (i)
                WHERE i:Act OR i:Rule OR i:Regulation OR i:Notification
                MATCH (i)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
                RETURN i.instrument_id AS instrument_id,
                       i.title AS title,
                       i.short_title AS short_title,
                       i.instrument_type AS type,
                       d.domain_name AS domain,
                       i.status AS status,
                       i.enactment_date AS enactment_date
                ORDER BY d.priority ASC, i.enactment_date ASC
                """
            params = {}
        results = self._execute(cypher, params)
        return [
            {
                "instrument_id": _unwrap(row["instrument_id"]),
                "title": _unwrap(row["title"]),
                "short_title": _unwrap(row["short_title"]),
                "type": _unwrap(row["type"]),
                "domain": _unwrap(row["domain"]),
                "status": _unwrap(row["status"]),
                "enactment_date": _unwrap(row["enactment_date"]),
            }
            for row in results
        ]

    def search_provisions(
        self,
        text: str,
        domain: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Full-text search within provision texts + titles."""
        # Escape special Cypher string characters
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")

        domain_filter = ""
        if domain:
            domain_filter = f"AND (p)-[:BELONGS_TO_DOMAIN]->(:LegalDomain {{domain_name: '{domain}'}})"

        results = self._execute(
            f"""
            MATCH (p:LegalProvision)
            WHERE (toLower(p.title) CONTAINS toLower($text) OR toLower(p.provision_text) CONTAINS toLower($text))
            {domain_filter}
            MATCH (i)-[:CONTAINS]->(p)
            OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            OPTIONAL MATCH (p)-[:SUPPORTED_BY]->(ch:Chunk)
            OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(doc:Document)
            RETURN
                p.provision_id AS provision_id,
                p.provision_number AS provision_number,
                p.title AS title,
                p.provision_text AS snippet,
                i.title AS instrument_title,
                i.instrument_id AS instrument_id,
                d.domain_name AS legal_domain,
                doc.source_uri AS source_uri
            LIMIT $limit
            """,
            {"text": escaped, "limit": limit},
        )
        return [
            {
                "provision_id": _unwrap(row["provision_id"]),
                "provision_number": _unwrap(row["provision_number"]),
                "title": _unwrap(row["title"]),
                "snippet": _unwrap(row["snippet"]),
                "instrument_title": _unwrap(row["instrument_title"]),
                "instrument_id": _unwrap(row["instrument_id"]),
                "legal_domain": _unwrap(row["legal_domain"]),
                "source_uri": _unwrap(row["source_uri"]),
            }
            for row in results
        ]


# --------------------------------------------------------------------------- #
# LLM retrieval contract — structured JSON output
# --------------------------------------------------------------------------- #


def build_llm_retrieval_contract(
    query: str,
    kg_queries: LegalKGQueries,
) -> dict[str, Any]:
    """Build the structured JSON evidence package for an LLM.

    Implements the retrieval contract from §23 of the task spec:
    structured graph evidence, not a raw node dump.
    """
    # Step 1: classify query domain
    domain = _classify_query_domain(query)

    # Step 2: extract entity/concept mentions from query
    concepts = _extract_concept_mentions(query)

    # Step 3: entity extraction — find provisions matching concepts
    provisions: list[dict] = []
    for concept in concepts:
        provisions.extend(kg_queries.get_cross_domain_laws(concept))

    # Step 4: if no concepts matched, fall back to full-text search
    if not provisions:
        provisions = kg_queries.search_provisions(query, domain=domain, limit=10)

    # Step 5: enrich each provision with full evidence
    enriched: list[dict] = []
    for prov in provisions[:10]:
        pid = prov.get("provision_id")
        if pid:
            full = kg_queries.get_provision(pid)
            if full:
                enriched.append(full)

    # Step 6: extract relationship structure
    relationships: list[dict] = []
    for prov in enriched:
        related = kg_queries.get_related_provisions(prov["provision_id"])
        for r in related[:5]:
            relationships.append(r)

    # Step 7: get authorities
    authorities: list[dict] = []
    for prov in enriched:
        auths = kg_queries.get_authorities(prov["provision_id"])
        for a in auths:
            if a not in authorities:
                authorities.append(a)

    # Step 8: collect source evidence
    source_evidence: list[dict] = []
    for prov in enriched:
        sev = kg_queries.get_source_evidence(prov["provision_id"])
        if sev:
            source_evidence.append(sev)

    # Step 9: temporal status
    temporal_status = [
        {
            "provision_id": p["provision_id"],
            "status": p.get("status"),
            "effective_from": p.get("effective_from"),
            "effective_to": p.get("effective_to"),
            "confidence": p.get("confidence"),
        }
        for p in enriched
    ]

    # Step 10: domain summary
    domains = list(set(p.get("legal_domain") for p in enriched if p.get("legal_domain")))

    return {
        "query": query,
        "entities": concepts,
        "legal_domains": domains,
        "provisions": enriched,
        "relationships": relationships,
        "authorities": authorities,
        "enforcement": [],
        "temporal_status": temporal_status,
        "source_evidence": source_evidence,
        "retrieval_strategy": {
            "query_domain": domain,
            "vector_search_used": True,
            "graph_expansion_used": True,
            "cross_domain_traversal": len(domains) > 1,
        },
    }


def provisions_for_query(
    query: str,
    kg_queries: LegalKGQueries,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Retrieve candidate provisions for a query (concept traversal + fallback).

    Implements the retrieval steps of :func:`build_llm_retrieval_contract` —
    concept traversal via ``get_cross_domain_laws``, falling back to the
    production full-text provision search when no concept matches — without
    the per-provision enrichment (get_provision / related / authorities /
    source evidence).  This is the fast path used by the evaluation harness
    (ARM D) and any consumer that only needs the provision candidate set.
    """
    provisions: list[dict[str, Any]] = []
    for concept in _extract_concept_mentions(query):
        provisions.extend(kg_queries.get_cross_domain_laws(concept))
    if not provisions:
        provisions = kg_queries.search_provisions(query, domain=_classify_query_domain(query), limit=limit)
    return provisions[:limit]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

import os

_CONCEPT_KEYWORDS: dict[str, list[str]] = {
    "FoodBusiness": ["food business", "fbo", "food business operator"],
    "Slaughterhouse": ["slaughter", "slaughterhouse", "meat", "abattoir"],
    "Wastewater": ["waste water", "wastewater", "effluent"],
    "SolidWaste": ["solid waste", "garbage", "refuse"],
    "Licence": ["licence", "license", "permit"],
    "TradeLicence": ["trade licence", "trade license", "business licence"],
    "Premises": ["premises", "premise", "location"],
    "Sanitation": ["sanitation", "cleanliness", "hygiene"],
    "Nuisance": ["nuisance"],
    "Pollution": ["pollution", "pollutant", "emission"],
    "ConsentToOperate": ["consent", "consent to operate"],
    "Inspection": ["inspection", "inspect", "examine"],
    "Sampling": ["sample", "sampling"],
    "AnimalSlaughter": ["animal slaughter", "animal welfare", "slaughter"],
    "AnimalWelfare": ["animal welfare", "animal cruelty"],
    "FoodAdulteration": ["adulter", "misbrand", "substandard"],
    "Contract": ["contract", "agreement"],
    "ConsumerProtection": ["consumer", "consumer protection"],
}


def _classify_query_domain(query: str) -> str | None:
    """Classify which domain a query is primarily about (simple keyword match)."""
    q = query.lower()
    if any(kw in q for kw in ["fssai", "food safety", "food business", "fbo", "licence food", "food licence"]):
        return "FOOD_SAFETY"
    if any(kw in q for kw in ["slaughter", "animal", "meat", "abattoir"]):
        return "ANIMAL_SLAUGHTER"
    if any(kw in q for kw in ["environment", "pollution", "water", "air", "waste", "plastic", "consent"]):
        return "ENVIRONMENT_POLLUTION"
    if any(kw in q for kw in ["municipal", "kmc", "kolkata", "trade licence", "trade licence"]):
        return "MUNICIPAL"
    if any(kw in q for kw in ["public health", "sanitation", "consumer"]):
        return "PUBLIC_HEALTH"
    if any(kw in q for kw in ["contract", "sale of goods", "partnership"]):
        return "BUSINESS_CIVIL"
    if any(kw in q for kw in ["land", "premises", "rent", "tenancy"]):
        return "LAND_PREMISES"
    return None


def _extract_concept_mentions(query: str) -> list[str]:
    """Extract legal concept names from a query string."""
    q = query.lower()
    found: list[str] = []
    for concept, keywords in _CONCEPT_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            found.append(concept)
    return found


def _unwrap(value: Any) -> Any:
    """Unwrap a Neo4j value to a Python primitive."""
    if value is None:
        return None
    # Handle Neo4j Date/Time objects
    if hasattr(value, "to_native"):
        return value.to_native()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


def _unwrap_list(value: Any) -> list[Any]:
    """Unwrap a list of Neo4j values."""
    if value is None:
        return []
    if isinstance(value, list):
        return [_unwrap(v) for v in value]
    return [_unwrap(value)]


def _row_to_related_provision(row: dict) -> dict[str, Any]:
    return {
        "relationship_type": _unwrap(row.get("relationship_type")),
        "target_id": _unwrap(row.get("target_id")),
        "target_number": _unwrap(row.get("target_number")),
        "target_title": _unwrap(row.get("target_title")),
        "target_domain": _unwrap(row.get("target_domain")),
        "evidence": _unwrap(row.get("evidence")),
        "confidence": _unwrap(row.get("confidence")),
        "source_uri": _unwrap(row.get("source_uri")),
    }


def _row_to_cross_domain_result(row: dict) -> dict[str, Any]:
    return {
        "provision_id": _unwrap(row.get("provision_id")),
        "provision_number": _unwrap(row.get("provision_number")),
        "title": _unwrap(row.get("title")),
        "domain": _unwrap(row.get("domain")),
        "instrument_id": _unwrap(row.get("instrument_id")),
        "instrument_title": _unwrap(row.get("instrument_title")),
        "authorities": [a for a in (_unwrap(row.get("authorities")) or []) if a],
        "source_uri": _unwrap(row.get("source_uri")),
        "chunk_text": _unwrap(row.get("chunk_text")),
        "document_title": _unwrap(row.get("document_title")),
    }


# End of queries.py
