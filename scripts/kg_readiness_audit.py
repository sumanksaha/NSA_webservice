"""KG Readiness Audit — non-destructive measurements.

Read-only audit of the Neo4j legal knowledge graph (and Qdrant, when
reachable) feeding the KG Readiness Scorecard.  NO writes are executed:
only ``MATCH/RETURN``, ``SHOW``, and ``CALL db.*`` read procedures.

Usage::

    python scripts/kg_readiness_audit.py --out reports/kg_readiness_measurements.json

Outputs a JSON document with all measurements for the 10-dimension
readiness scorecard, plus sample-based validation rows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Load .env so credentials resolve outside the Flask context
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # pragma: no cover
    pass


def _iso(v: Any) -> Any:
    """Convert Neo4j temporal types to ISO strings for JSON serialisation."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    return v


class Audit:
    def __init__(self) -> None:
        self.driver = None
        self.qdrant = None

    # ------------------------------------------------------------------ #
    # Neo4j plumbing
    # ------------------------------------------------------------------ #

    def connect_neo4j(self) -> bool:
        if not (os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_USERNAME") and os.environ.get("NEO4J_PASSWORD")):
            return False
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
        )
        return True

    def dbname(self) -> str:
        return os.environ.get("NEO4J_DATABASE", "neo4j")

    def run(self, cypher: str, params: dict | None = None, max_retries: int = 2) -> list[dict]:
        """Read-only query executor."""
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                with self.driver.session(database=self.dbname()) as s:
                    result = s.run(cypher, parameters_=params or {})
                    return [dict(r) for r in result]
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < max_retries:
                    continue
        return [{"__error__": str(last_exc)}]

    def _count(self, cypher: str) -> int:
        """Scalar count for ``RETURN count(*) AS c`` queries (-1 on error)."""
        rows = self.run(cypher)
        if not rows or "__error__" in rows[0] or "c" not in rows[0]:
            return -1
        try:
            return int(rows[0]["c"])
        except (TypeError, ValueError):
            return -1

    def _ok(self, rows: list[dict]) -> bool:
        return bool(rows) and "__error__" not in rows[0]

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #

    def probe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"database": {}}
        comps = self.run("CALL dbms.components() YIELD name, versions RETURN name, versions")
        out["database"]["components"] = comps
        dbs = self.run("SHOW DATABASES YIELD name, currentStatus RETURN name, currentStatus")
        out["database"]["databases"] = dbs
        cons = self.run(
            "SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties, type RETURN name, labelsOrTypes, properties, type"
        )
        out["constraints"] = [
            {
                "name": c.get("name"),
                "labels": c.get("labelsOrTypes"),
                "props": c.get("properties"),
                "type": c.get("type"),
            }
            for c in cons
            if self._ok([c])
        ]
        idx = self.run(
            "SHOW INDEXES YIELD name, labelsOrTypes, properties, type, state RETURN name, labelsOrTypes, properties, type, state"
        )
        out["indexes"] = [
            {
                "name": i.get("name"),
                "labels": i.get("labelsOrTypes"),
                "props": i.get("properties"),
                "type": i.get("type"),
                "state": i.get("state"),
            }
            for i in idx
            if self._ok([i])
        ]
        out["labels"] = self.run("CALL db.labels() YIELD label RETURN collect(label) AS labels")[0].get("labels", [])
        out["relationship_types"] = self.run(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS rt"
        )[0].get("rt", [])
        out["total_nodes"] = self._count("MATCH (n) RETURN count(n) AS c")
        out["total_relationships"] = self._count("MATCH ()-[r]->() RETURN count(r) AS c")
        return out

    # ------------------------------------------------------------------ #
    # Graph statistics
    # ------------------------------------------------------------------ #

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["nodes_by_label"] = self.run(
            "CALL db.labels() YIELD label WITH label MATCH (n) WHERE any(l IN labels(n) WHERE l = label) "
            "RETURN label, count(n) AS c ORDER BY c DESC"
        )
        out["rels_by_type"] = self.run(
            "CALL db.relationshipTypes() YIELD relationshipType WITH relationshipType AS rt "
            "MATCH ()-[r]->() WHERE type(r) = rt RETURN rt AS rel_type, count(r) AS c ORDER BY c DESC"
        )

        legal_labels = [
            "Act",
            "Rule",
            "Regulation",
            "Notification",
            "Order",
            "Circular",
            "Guideline",
            "Judgment",
            "LegalProvision",
            "Section",
            "Subsection",
            "Clause",
            "Schedule",
            "RuleProvision",
            "RegulationProvision",
            "Authority",
            "LegalDomain",
            "LegalConcept",
            "Jurisdiction",
            "Chunk",
            "Document",
            "Source",
            "Offence",
            "Penalty",
            "Obligation",
            "Prohibition",
            "Permission",
            "Power",
            "Duty",
            "Procedure",
            "FoodBusiness",
            "BusinessActivity",
            "Premises",
        ]
        counts: dict[str, int] = {}
        for lab in legal_labels:
            counts[lab] = self._count(f"MATCH (n:{lab}) RETURN count(n) AS c")
        out["legal_label_counts"] = counts

        out["domains"] = self.run(
            "MATCH (d:LegalDomain) OPTIONAL MATCH (d)<-[:BELONGS_TO_DOMAIN]-(n) "
            "RETURN d.domain_name AS domain, d.priority AS priority, d.jurisdiction AS jurisdiction, "
            "count(DISTINCT n) AS nodes ORDER BY d.priority"
        )
        out["instruments_by_domain"] = self.run(
            "MATCH (i) WHERE i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment "
            "MATCH (i)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain) "
            "RETURN d.domain_name AS domain, count(i) AS instruments, min(d.priority) AS priority "
            "ORDER BY priority"
        )
        out["provisions_by_domain"] = self.run(
            "MATCH (p:LegalProvision)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain) "
            "RETURN d.domain_name AS domain, count(DISTINCT p) AS provisions, min(d.priority) AS priority "
            "ORDER BY priority"
        )
        out["chunks_by_domain"] = self.run(
            "MATCH (ch:Chunk) OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(doc:Document) "
            "WITH ch, coalesce(doc.legal_domain, ch.legal_domain, 'UNKNOWN') AS dom "
            "RETURN dom AS domain, count(ch) AS chunks ORDER BY chunks DESC"
        )
        out["documents_by_domain"] = self.run(
            "MATCH (d:Document) RETURN coalesce(d.legal_domain, 'UNKNOWN') AS domain, count(d) AS c ORDER BY c DESC"
        )
        out["instruments_by_type"] = self.run(
            "MATCH (i) WHERE i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment "
            "WITH labels(i) AS ls UNWIND ls AS l WITH l WHERE l IN "
            "['Act','Rule','Regulation','Notification','Order','Circular','Guideline','Judgment'] "
            "RETURN l AS instrument_type, count(*) AS c ORDER BY c DESC"
        )
        out["instruments_by_jurisdiction"] = self.run(
            "MATCH (i) WHERE i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment "
            "RETURN coalesce(i.jurisdiction, 'UNKNOWN') AS jurisdiction, count(i) AS c ORDER BY c DESC"
        )
        return out

    # ------------------------------------------------------------------ #
    # Dimension probes
    # ------------------------------------------------------------------ #

    def provenance(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["total_provisions"] = self._count("MATCH (p:LegalProvision) RETURN count(p) AS c")
        out["provisions_with_source_marker"] = self._count(
            "MATCH (p:LegalProvision) WHERE p.source IS NOT NULL RETURN count(p) AS c"
        )
        out["provisions_with_supported_by_chunk"] = self._count(
            "MATCH (p:LegalProvision)-[:SUPPORTED_BY]->(ch:Chunk) RETURN count(DISTINCT p) AS c"
        )
        out["chunks_with_document_id"] = self._count(
            "MATCH (ch:Chunk) WHERE ch.document_id IS NOT NULL AND ch.document_id <> '' RETURN count(ch) AS c"
        )
        out["chunks_with_qdrant_point_id"] = self._count(
            "MATCH (ch:Chunk) WHERE ch.qdrant_point_id IS NOT NULL AND ch.qdrant_point_id <> '' RETURN count(ch) AS c"
        )
        out["documents_total"] = self._count("MATCH (d:Document) RETURN count(d) AS c")
        out["documents_with_uri"] = self._count(
            "MATCH (d:Document) WHERE d.source_uri IS NOT NULL AND d.source_uri <> '' RETURN count(d) AS c"
        )
        out["sources_total"] = self._count("MATCH (s:Source) RETURN count(s) AS c")
        out["chunks_total"] = self._count("MATCH (ch:Chunk) RETURN count(ch) AS c")
        out["provisions_with_evidence_rel"] = self._count(
            "MATCH (p:LegalProvision)-[r:SUPPORTED_BY]->() WHERE r.evidence IS NOT NULL OR r.evidence_type IS NOT NULL "
            "RETURN count(DISTINCT p) AS c"
        )
        out["documents"] = self.run(
            "MATCH (d:Document) RETURN d.document_id AS document_id, d.title AS title, "
            "coalesce(d.source_uri, d.source_type, '') AS source, d.legal_domain AS legal_domain "
            "ORDER BY d.document_id LIMIT 60"
        )
        return out

    def temporal(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["provision_status_distribution"] = self.run(
            "MATCH (p:LegalProvision) RETURN coalesce(p.status, 'MISSING') AS status, count(p) AS c ORDER BY c DESC"
        )
        out["instrument_status_distribution"] = self.run(
            "MATCH (i) WHERE (i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment) "
            "RETURN coalesce(i.status, 'MISSING') AS status, count(i) AS c ORDER BY c DESC"
        )
        out["provisions_with_effective_from"] = self._count(
            "MATCH (p:LegalProvision) WHERE p.effective_from IS NOT NULL RETURN count(p) AS c"
        )
        out["provisions_with_effective_to"] = self._count(
            "MATCH (p:LegalProvision) WHERE p.effective_to IS NOT NULL RETURN count(p) AS c"
        )
        out["provisions_with_version"] = self._count(
            "MATCH (p:LegalProvision) WHERE p.version IS NOT NULL RETURN count(p) AS c"
        )
        out["instruments_with_repeal_info"] = self._count(
            "MATCH (i) WHERE (i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment) AND (i.repeal_date IS NOT NULL OR i.repealed_by IS NOT NULL) "
            "RETURN count(i) AS c"
        )
        out["amends_repeals_edges"] = self.run(
            "MATCH ()-[r:AMENDS|REPEALS|REPLACES|MADE_UNDER]->() RETURN type(r) AS rel, count(r) AS c ORDER BY c DESC"
        )
        out["non_current_instruments"] = self.run(
            "MATCH (i) WHERE (i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment) AND coalesce(i.status,'') <> 'current' "
            "RETURN i.instrument_id AS instrument_id, i.title AS title, coalesce(i.status,'MISSING') AS status LIMIT 20"
        )
        return out

    def semantics(self) -> dict[str, Any]:
        semantic_types = [
            "APPLIES_TO",
            "RELATES_TO",
            "REQUIRES",
            "IMPOSES_DUTY",
            "CREATES_OBLIGATION",
            "CREATES_PROHIBITION",
            "PROHIBITS",
            "GRANTS_PERMISSION",
            "GRANTS_POWER_TO",
            "CREATES_OFFENCE",
            "PRESCRIBES_PENALTY",
            "HAS_PENALTY",
            "PRESCRIBES",
            "ENFORCED_BY",
            "REQUIRES_AUTHORIZATION_FROM",
            "TRIGGERS_NOTICE",
            "EXEMPTS",
        ]
        rows: list[dict] = []
        for t in semantic_types:
            r = self.run(f"MATCH ()-[r:{t}]->() RETURN count(r) AS c")
            rows.append({"rel": t, "count": r[0]["c"] if self._ok(r) else -1})
        out: dict[str, Any] = {"semantic_edge_counts": rows}
        out["provision_concept_edges"] = self.run(
            "MATCH (p:LegalProvision)-[r]->(c:LegalConcept) RETURN type(r) AS rel, count(r) AS c ORDER BY c DESC"
        )
        out["provision_authority_edges"] = self.run(
            "MATCH (p:LegalProvision)-[r]->(a:Authority) RETURN type(r) AS rel, count(r) AS c ORDER BY c DESC"
        )
        out["concepts_total"] = self._count("MATCH (c:LegalConcept) RETURN count(c) AS c")
        out["concept_edge_coverage"] = self.run(
            "MATCH (p:LegalProvision) OPTIONAL MATCH (p)-[r]->(c:LegalConcept) "
            "RETURN count(DISTINCT p) AS provisions, "
            "count(DISTINCT CASE WHEN c IS NOT NULL THEN p END) AS with_concepts"
        )
        out["authorities_total"] = self._count("MATCH (a:Authority) RETURN count(a) AS c")
        out["provisions_with_authority_edge"] = self._count(
            "MATCH (p:LegalProvision)-[:ENFORCED_BY|GRANTS_POWER_TO|REQUIRES_AUTHORIZATION_FROM]->(:Authority) "
            "RETURN count(DISTINCT p) AS c"
        )
        out["semantic_edge_sample"] = self.run(
            "MATCH (p:LegalProvision)-[r]->(c) WHERE type(r) IN ['APPLIES_TO','IMPOSES_DUTY','CREATES_OFFENCE',"
            "'GRANTS_POWER_TO','ENFORCED_BY','REQUIRES','CREATES_PROHIBITION','PRESCRIBES_PENALTY','PROHIBITS'] "
            "RETURN p.provision_id AS src, type(r) AS rel, coalesce(labels(c)[0], '?') AS tgt_label, "
            "coalesce(c.name, c.concept_id, c.authority_id, '?') AS tgt, "
            "left(coalesce(r.evidence, ''), 120) AS evidence, r.confidence AS confidence "
            "LIMIT 40"
        )
        return out

    def entity_resolution(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["duplicate_instrument_titles"] = self.run(
            "MATCH (i) WHERE i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment "
            "WITH coalesce(i.canonical_name, i.title) AS title, count(*) AS cnt WHERE cnt > 1 "
            "RETURN title, cnt ORDER BY cnt DESC LIMIT 30"
        )
        out["duplicate_provision_keys"] = self.run(
            "MATCH (p:LegalProvision) WHERE p.instrument_id IS NOT NULL AND p.provision_number IS NOT NULL "
            "WITH p.instrument_id AS inst, p.provision_number AS pnum, count(*) AS cnt WHERE cnt > 1 "
            "RETURN inst, pnum, cnt ORDER BY cnt DESC LIMIT 30"
        )
        out["provision_id_collisions"] = self.run(
            "MATCH (p:LegalProvision) WITH p.provision_id AS pid, count(*) AS cnt WHERE cnt > 1 "
            "RETURN pid, cnt LIMIT 20"
        )
        out["stub_instruments"] = self._count(
            "MATCH (i) WHERE (i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment) AND coalesce(i.source_type, '') = 'stub' RETURN count(i) AS c"
        )
        out["instruments_without_source"] = self._count(
            "MATCH (i) WHERE (i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment) AND (i.source_url IS NULL OR i.source_url = '') RETURN count(i) AS c"
        )
        out["canonical_name_missing"] = self._count(
            "MATCH (i) WHERE (i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment) AND i.canonical_name IS NULL RETURN count(i) AS c"
        )
        out["instruments_manual_stub_type"] = self.run(
            "MATCH (i) WHERE (i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment) RETURN i.instrument_id AS instrument_id, "
            "coalesce(i.instrument_type,'?') AS itype, coalesce(i.legal_domain,'?') AS domain, "
            "coalesce(i.source_url,'') AS source_url ORDER BY i.instrument_id"
        )
        return out

    def cross_domain(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["instrument_interdomain_edges"] = self.run(
            "MATCH (a)-[r:RELATED_TO|COMPLEMENTS|AMENDS|REPEALS|REPLACES|MADE_UNDER|DEPENDS_ON]->(b) "
            "WHERE (a:Act OR a:Rule OR a:Regulation OR a:Notification OR a:Order OR a:Circular "
            "OR a:Guideline OR a:Judgment) AND (b:Act OR b:Rule OR b:Regulation OR b:Notification "
            "OR b:Order OR b:Circular OR b:Guideline OR b:Judgment) "
            "AND a.legal_domain <> b.legal_domain "
            "RETURN type(r) AS rel, count(r) AS c, a.legal_domain AS from_domain, b.legal_domain AS to_domain "
            "ORDER BY c DESC LIMIT 40"
        )
        out["provision_interdomain_edges"] = self.run(
            "MATCH (p:LegalProvision)-[r]->(q:LegalProvision) "
            "WHERE p.legal_domain <> q.legal_domain "
            "RETURN type(r) AS rel, count(r) AS c, p.legal_domain AS from_domain, q.legal_domain AS to_domain "
            "ORDER BY c DESC LIMIT 40"
        )
        out["provision_interdomain_edges_all"] = self.run(
            "MATCH (p:LegalProvision)-[r]->(q:LegalProvision) RETURN type(r) AS rel, count(r) AS c ORDER BY c DESC"
        )
        out["cross_domain_edges_with_evidence"] = self._count(
            "MATCH (p:LegalProvision)-[r]->(q:LegalProvision) WHERE p.legal_domain <> q.legal_domain "
            "AND r.evidence IS NOT NULL RETURN count(r) AS c"
        )
        out["cross_domain_edges_total"] = self._count(
            "MATCH (p:LegalProvision)-[r]->(q:LegalProvision) WHERE p.legal_domain <> q.legal_domain "
            "RETURN count(r) AS c"
        )
        out["shared_concepts_by_domain_count"] = self._count(
            "MATCH (c:LegalConcept)-[:RELEVANT_IN]->(d:LegalDomain) WITH c, count(d) AS nd WHERE nd > 1 "
            "RETURN count(c) AS c"
        )
        return out

    def structural(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["orphan_provisions"] = self._count(
            "MATCH (p:LegalProvision) WHERE NOT (p)<-[:CONTAINS]-(:Act) AND NOT (p)<-[:CONTAINS]-(:Rule) "
            "AND NOT (p)<-[:CONTAINS]-(:Regulation) AND NOT (p)<-[:CONTAINS]-(:Notification) "
            "RETURN count(p) AS c"
        )
        out["provisions_without_domain"] = self._count(
            "MATCH (p:LegalProvision) WHERE NOT (p)-[:BELONGS_TO_DOMAIN]->(:LegalDomain) RETURN count(p) AS c"
        )
        out["instruments_without_domain"] = self._count(
            "MATCH (i) WHERE (i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment) AND NOT (i)-[:BELONGS_TO_DOMAIN]->(:LegalDomain) "
            "RETURN count(i) AS c"
        )
        out["chunks_without_document"] = self._count(
            "MATCH (ch:Chunk) WHERE NOT (ch)<-[:HAS_CHUNK]-(:Document) RETURN count(ch) AS c"
        )
        out["documents_without_chunks"] = self._count(
            "MATCH (d:Document) WHERE NOT (d)-[:HAS_CHUNK]->(:Chunk) RETURN count(d) AS c"
        )
        out["chunks_without_provision_link"] = self._count(
            "MATCH (ch:Chunk) WHERE NOT (ch)<-[:SUPPORTED_BY]-(:LegalProvision) RETURN count(ch) AS c"
        )
        out["generic_relationship_edges"] = self._count("MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS c")
        out["legal_concepts_orphaned"] = self._count(
            "MATCH (c:LegalConcept) WHERE NOT (c)<-[:APPLIES_TO|RELATES_TO|REQUIRES|RELEVANT_IN|IMPOSES_DUTY|"
            "CREATES_OFFENCE|CREATES_PROHIBITION|GRANTS_PERMISSION|PRESCRIBES_PENALTY|GRANTS_POWER_TO|ENFORCED_BY]-() "
            "RETURN count(c) AS c"
        )
        out["nodes_without_domain_prop"] = self._count(
            "MATCH (n) WHERE (n:Act OR n:Rule OR n:Regulation OR n:Notification OR n:Order OR n:Circular "
            "OR n:Guideline OR n:Judgment OR n:LegalProvision OR n:Document) "
            "AND n.legal_domain IS NULL RETURN count(n) AS c"
        )
        out["provisions_missing_text"] = self._count(
            "MATCH (p:LegalProvision) WHERE p.provision_text IS NULL OR size(p.provision_text) = 0 RETURN count(p) AS c"
        )
        out["provisions_title_only_text"] = self._count(
            "MATCH (p:LegalProvision) WHERE size(coalesce(p.provision_text, '')) < 40 RETURN count(p) AS c"
        )
        return out

    # ------------------------------------------------------------------ #
    # Samples
    # ------------------------------------------------------------------ #

    def samples(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["instruments_sample"] = self.run(
            "MATCH (i) WHERE i:Act OR i:Rule OR i:Regulation OR i:Notification OR i:Order OR i:Circular "
            "OR i:Guideline OR i:Judgment "
            "RETURN i.instrument_id AS instrument_id, i.title AS title, i.instrument_type AS type, "
            "coalesce(i.legal_domain,'MISSING') AS domain, coalesce(i.jurisdiction,'MISSING') AS jurisdiction, "
            "coalesce(i.status,'MISSING') AS status, coalesce(i.source_type,'MISSING') AS source_type, "
            "coalesce(i.source_url,'') AS source_url ORDER BY i.instrument_id LIMIT 30"
        )
        out["provisions_sample"] = self.run(
            "MATCH (p:LegalProvision) "
            "RETURN p.provision_id AS provision_id, p.provision_number AS provision_number, "
            "left(coalesce(p.title,''),60) AS title, coalesce(p.instrument_id,'MISSING') AS instrument_id, "
            "coalesce(p.legal_domain,'MISSING') AS legal_domain, coalesce(p.status,'MISSING') AS status, "
            "coalesce(p.source,'MISSING') AS source, "
            "CASE WHEN p.provision_text IS NOT NULL AND size(p.provision_text) > 0 THEN 'text' ELSE 'NO_TEXT' END AS has_text "
            "ORDER BY rand() LIMIT 50"
        )
        out["relationship_sample"] = self.run(
            "MATCH (n)-[r]->(m) WHERE (n:LegalProvision OR n:Act OR n:Rule OR n:Regulation OR n:Notification "
            "OR n:Authority OR n:LegalConcept OR n:LegalDomain OR n:Chunk OR n:Document) "
            "RETURN coalesce(n.provision_id, n.instrument_id, n.name, labels(n)[0]) AS src, "
            "type(r) AS rel, coalesce(m.provision_id, m.instrument_id, m.name, labels(m)[0]) AS tgt, "
            "left(coalesce(r.evidence,''),80) AS evidence, r.confidence AS confidence "
            "ORDER BY rand() LIMIT 50"
        )
        out["cross_domain_relationship_sample"] = self.run(
            "MATCH (p:LegalProvision)-[r]->(q:LegalProvision) WHERE p.legal_domain <> q.legal_domain "
            "RETURN p.provision_id AS src, p.legal_domain AS src_domain, type(r) AS rel, "
            "q.provision_id AS tgt, q.legal_domain AS tgt_domain, "
            "left(coalesce(r.evidence,''),100) AS evidence, r.confidence AS confidence "
            "ORDER BY rand() LIMIT 25"
        )
        out["authority_relationship_sample"] = self.run(
            "MATCH (p:LegalProvision)-[r:ENFORCED_BY|GRANTS_POWER_TO|REQUIRES_AUTHORIZATION_FROM]->(a:Authority) "
            "RETURN p.provision_id AS provision, type(r) AS rel, a.name AS authority, "
            "left(coalesce(r.evidence,''),100) AS evidence, r.confidence AS confidence "
            "ORDER BY rand() LIMIT 25"
        )
        out["provenance_chain_sample"] = self.run(
            "MATCH (p:LegalProvision)-[r:SUPPORTED_BY]->(ch:Chunk) "
            "OPTIONAL MATCH (ch)<-[:HAS_CHUNK]-(d:Document) "
            "OPTIONAL MATCH (i)-[:CONTAINS]->(p) "
            "RETURN p.provision_id AS provision, p.provision_number AS pnum, i.instrument_id AS instrument, "
            "ch.chunk_id AS chunk, ch.qdrant_point_id AS qdrant_point_id, "
            "CASE WHEN d IS NULL THEN 'NO_DOCUMENT' ELSE coalesce(d.document_id,'NO_ID') END AS document, "
            "CASE WHEN p.provision_text IS NOT NULL THEN left(p.provision_text,80) ELSE 'NO_TEXT' END AS text_snip "
            "ORDER BY rand() LIMIT 25"
        )
        return out

    # ------------------------------------------------------------------ #
    # Retrieval tests (read-only)
    # ------------------------------------------------------------------ #

    def retrieval_tests(self) -> dict[str, Any]:
        tests: dict[str, Any] = {}

        def run_test(name: str, cypher: str, params: dict | None = None, limit: int = 12) -> None:
            # Count query: strip everything from the first RETURN clause
            head = cypher.split(" RETURN ", 1)[0]
            count_cypher = head + " RETURN count(*) AS c"
            tests[name] = {
                "hit_count": self._count(count_cypher),
                "rows": self.run(cypher + f" LIMIT {limit}", params),
            }

        run_test(
            "A_food_business",
            "MATCH (c:LegalConcept) WHERE toLower(c.name) CONTAINS 'food business' "
            "MATCH (p:LegalProvision)-[:APPLIES_TO|RELATES_TO|REQUIRES|IMPOSES_DUTY|CREATES_OFFENCE]->(c) "
            "OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain) "
            "OPTIONAL MATCH (i)-[:CONTAINS]->(p) "
            "RETURN p.provision_id AS provision_id, p.provision_number AS pnum, "
            "coalesce(d.domain_name, p.legal_domain, '?') AS domain, coalesce(i.instrument_id,'?') AS instrument "
            "ORDER BY p.provision_id",
        )
        run_test(
            "B_slaughterhouse",
            "MATCH (c:LegalConcept {name: 'Slaughterhouse'})<-[:APPLIES_TO|RELATES_TO|REQUIRES]-(p:LegalProvision) "
            "OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain) "
            "OPTIONAL MATCH (i)-[:CONTAINS]->(p) "
            "RETURN p.provision_id AS provision_id, p.provision_number AS pnum, "
            "coalesce(d.domain_name, p.legal_domain, '?') AS domain, coalesce(i.instrument_id,'?') AS instrument "
            "ORDER BY p.provision_id",
        )
        run_test(
            "C_wastewater_env",
            "MATCH (c:LegalConcept) WHERE toLower(c.name) IN ['wastewater','effluent'] "
            "MATCH (p:LegalProvision)-[:APPLIES_TO|RELATES_TO|REQUIRES]->(c) "
            "OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain) "
            "OPTIONAL MATCH (i)-[:CONTAINS]->(p) "
            "RETURN p.provision_id AS provision_id, p.provision_number AS pnum, "
            "coalesce(d.domain_name, p.legal_domain, '?') AS domain, coalesce(i.instrument_id,'?') AS instrument "
            "ORDER BY p.provision_id",
        )
        run_test(
            "D_municipal_food",
            "MATCH (p:LegalProvision)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain {domain_name: 'MUNICIPAL'}) "
            "OPTIONAL MATCH (i)-[:CONTAINS]->(p) "
            "RETURN p.provision_id AS provision_id, p.provision_number AS pnum, coalesce(i.instrument_id,'?') AS instrument "
            "ORDER BY p.provision_id",
        )
        run_test(
            "E_enforcement_power",
            "MATCH (p:LegalProvision)-[r:GRANTS_POWER_TO|ENFORCED_BY]->(a:Authority) "
            "OPTIONAL MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain) "
            "OPTIONAL MATCH (i)-[:CONTAINS]->(p) "
            "RETURN p.provision_id AS provision_id, p.provision_number AS pnum, type(r) AS rel, "
            "a.name AS authority, coalesce(d.domain_name, p.legal_domain, '?') AS domain, "
            "coalesce(i.instrument_id,'?') AS instrument "
            "ORDER BY p.provision_id",
        )
        run_test(
            "F_slaughterhouse_as_food_business",
            "MATCH (c:LegalConcept {name: 'Slaughterhouse'})<-[:APPLIES_TO|RELATES_TO|REQUIRES]-(p:LegalProvision) "
            "MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain) "
            "OPTIONAL MATCH (i)-[:CONTAINS]->(p) "
            "WITH p, d, i "
            "WHERE (d.domain_name = 'ANIMAL_SLAUGHTER') "
            "   OR exists((p)-[:RELATED_TO|CROSS_REFERENCES|INTERACTS_WITH|COMPLEMENTS]->(:LegalProvision)-[:BELONGS_TO_DOMAIN]->(:LegalDomain {domain_name: 'FOOD_SAFETY'})) "
            "   OR exists((p)-[:APPLIES_TO]->(:LegalConcept {name: 'FoodBusiness'})) "
            "RETURN p.provision_id AS provision_id, p.provision_number AS pnum, d.domain_name AS domain, "
            "coalesce(i.instrument_id,'?') AS instrument "
            "ORDER BY p.provision_id",
        )
        return tests

    # ------------------------------------------------------------------ #
    # Qdrant (read-only) — optional
    # ------------------------------------------------------------------ #

    def qdrant_probe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"reachable": False}
        try:
            from qdrant_client import QdrantClient

            url = os.environ.get("RAG_QDRANT_URL", "")
            key = os.environ.get("RAG_QDRANT_API_KEY", "") or None
            if not url:
                return out
            client = QdrantClient(url=url, api_key=key)
            collections = client.get_collections().collections
            out["reachable"] = True
            out["collections"] = [
                {
                    "name": c.name,
                    "points": client.count(collection_name=c.name, exact=True).count,
                }
                for c in collections
            ]
        except Exception as exc:
            out["error"] = str(exc)
        return out

    # ------------------------------------------------------------------ #
    # Main
    # ------------------------------------------------------------------ #

    def run_all(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "audited_at": datetime.now().isoformat(timespec="seconds"),
            "mode": "READ_ONLY",
            "neo4j_connected": False,
            "qdrant": {},
        }
        if not self.connect_neo4j():
            report["error"] = "Neo4j not configured"
            return report
        report["neo4j_connected"] = True
        try:
            report["probe"] = self.probe()
            report["stats"] = self.stats()
            report["provenance"] = self.provenance()
            report["temporal"] = self.temporal()
            report["semantics"] = self.semantics()
            report["entity_resolution"] = self.entity_resolution()
            report["cross_domain"] = self.cross_domain()
            report["structural"] = self.structural()
            report["samples"] = self.samples()
            report["retrieval_tests"] = self.retrieval_tests()
        finally:
            if self.driver:
                self.driver.close()
        report["qdrant"] = self.qdrant_probe()
        return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Non-destructive KG readiness audit")
    ap.add_argument("--out", default="reports/kg_readiness_measurements.json")
    args = ap.parse_args()

    audit = Audit()
    report = audit.run_all()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=_iso), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
