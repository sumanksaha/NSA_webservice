"""Legal Knowledge Graph ingestion engine (Phase 3 — pilot).

Builds the multi-domain legal KG in Neo4j from two sources:

1. **Existing FSSAI corpus** (``LegalDocument`` / ``LegalChunk`` tables in the
   local DB) — section headers and key provisions are extracted from the
   already-chunked FSSAI corpus (29 documents, 12,K+ chunks).

2. **Domain manifest** (``kg/domain_manifest.py``) — structured instrument
   stubs for non-FSSAI domains (ANIMAL_SLAUGHTER, ENVIRONMENT_POLLUTION,
   MUNICIPAL, PUBLIC_HEALTH, BUSINESS_CIVIL, LAND_PREMISES).

The engine is **memory-bounded**: it processes one document at a time,
batch-writes in transactions of 1000 nodes, and never loads the full
corpus into RAM simultaneously.

Every legal node carries provenance back to its source Chunk → Document →
Source chain, so the LLM can always trace a claim to evidence.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.rag.legal_sections import sections_for_act, is_known_section_for_act
from kg.domain_manifest import (
    CROSS_DOMAIN_RELATIONSHIPS,
    DOMAINS,
    PROVISION_STUBS,
    PROVISION_CONCEPT_MAP,
    PILOT_INSTRUMENTS,
    JURISDICTIONS,
    AUTHORITIES,
    CONCEPTS,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000  # Neo4j transaction batch size — stays within 8 GB RAM

# Section header regexes (case-insensitive)
_SECTION_HEADER_RE = re.compile(
    r"^\s*(?:section|sec\.?|§)\s*(\d{1,4}[A-Za-z]?)\b\s*(?:[:\-—.]|\s*$)", re.IGNORECASE
)
_SUBSECTION_RE = re.compile(r"^\s*(\(\d+\))\s")  # "(1)" at start


# --------------------------------------------------------------------------- #
# Ingestion Engine
# --------------------------------------------------------------------------- #


class LegalKGIngestionEngine:
    """Ingest documents + provisions + relationships into the legal KG.

    Args:
        driver: Optional pre-built Neo4j driver (injected for tests).
        database: Neo4j database name (default from NEO4J_DATABASE env).
    """

    def __init__(
        self,
        driver: Any | None = None,
        database: str | None = None,
    ) -> None:
        self._driver = driver
        self._database = database or os.environ.get("NEO4J_DATABASE", "neo4j")
        self._own_driver = False

    def _get_driver(self) -> Any:
        """Lazily build the Neo4j driver from env vars."""
        if self._driver is None:
            from app.services.neo4j_graph import _get_driver

            self._driver = _get_driver()
            self._own_driver = True
        return self._driver

    def _execute(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Run a Cypher statement and return records."""
        driver = self._get_driver()
        result = driver.execute_query(cypher, parameters_=params or {}, database_=self._database)
        return [dict(r) for r in result.records]

    def _execute_write(self, cypher: str, params: dict | None = None) -> Any:
        """Run a write Cypher statement and return the summary."""
        driver = self._get_driver()
        return driver.execute_query(cypher, parameters_=params or {}, database_=self._database)

    # ------------------------------------------------------------------ #
    # Step 1: Load controlled vocabularies (domains, jurisdictions,
    #         authorities, concepts)
    # ------------------------------------------------------------------ #

    def load_vocabularies(self) -> dict[str, int]:
        """Insert all LegalDomain, Jurisdiction, Authority, LegalConcept nodes.

        Uses MERGE on the unique constraint so it's idempotent.  Returns
        counts of nodes created.
        """
        stats: dict[str, int] = {"domains": 0, "jurisdictions": 0, "authorities": 0, "concepts": 0}

        # LegalDomain nodes
        for domain in DOMAINS.values():
            result = self._execute_write(
                """
                MERGE (d:LegalDomain {domain_name: $name})
                ON CREATE SET d.description = $desc, d.jurisdiction = $jur, d.priority = $prio
                ON MATCH SET d.description = $desc, d.jurisdiction = $jur, d.priority = $prio
                RETURN count(*) AS c
                """,
                {"name": domain.domain_name, "desc": domain.description, "jur": domain.jurisdiction, "prio": domain.priority},
            )
            stats["domains"] += 1

        # Jurisdiction nodes
        for j in JURISDICTIONS.values():
            self._execute_write(
                """
                MERGE (j:Jurisdiction {jurisdiction_id: $jid})
                ON CREATE SET j.name = $name, j.level = $level
                ON MATCH SET j.name = $name, j.level = $level
                """,
                {"jid": j.jurisdiction_id, "name": j.name, "level": j.level},
            )
            stats["jurisdictions"] += 1

        # Authority nodes
        for a in AUTHORITIES.values():
            self._execute_write(
                """
                MERGE (a:Authority {authority_id: $aid})
                ON CREATE SET a.name = $name, a.short_name = $short,
                            a.jurisdiction = $jur, a.type = $atype
                ON MATCH SET a.name = $name, a.short_name = $short,
                                    a.jurisdiction = $jur, a.type = $atype
                """,
                {"aid": a.authority_id, "name": a.name, "short": a.short_name, "jur": a.jurisdiction, "atype": a.authority_type},
            )
            stats["authorities"] += 1

        # LegalConcept nodes
        for c in CONCEPTS.values():
            domains_list = list(c.domains)
            result = self._execute_write(
                """
                MERGE (c:LegalConcept {concept_id: $cid})
                ON CREATE SET c.name = $name, c.description = $desc, c.domains = $domains
                ON MATCH SET c.name = $name, c.description = $desc, c.domains = $domains
                """,
                {"cid": c.concept_id, "name": c.name, "desc": c.description, "domains": domains_list},
            )
            stats["concepts"] += 1

        # Link concepts to domains
        for c in CONCEPTS.values():
            for dom in c.domains:
                self._execute_write(
                    """
                    MATCH (c:LegalConcept {concept_id: $cid})
                    MATCH (d:LegalDomain {domain_name: $dom})
                    MERGE (c)-[:RELEVANT_IN]->(d)
                    """,
                    {"cid": c.concept_id, "dom": dom},
                )

        logger.info(
            "KG vocabularies loaded: %d domains, %d jurisdictions, %d authorities, %d concepts",
            stats["domains"], stats["jurisdictions"], stats["authorities"], stats["concepts"],
        )
        return stats

    # ------------------------------------------------------------------ #
    # Step 2: Load instruments (the outer layer of the graph)
    # ------------------------------------------------------------------ #

    def load_instruments(self) -> dict[str, int]:
        """Insert all pilot instruments as Act/Rule/Regulation/Notification nodes.

        Returns counts by type.
        """
        stats: dict[str, int] = {}
        for inst in PILOT_INSTRUMENTS:
            label = self._instrument_label(inst.instrument_type)
            result = self._execute_write(
                f"""
                MERGE (i:{label} {{instrument_id: $iid}})
                ON CREATE SET
                    i.title = $title,
                    i.short_title = $short,
                    i.instrument_type = $itype,
                    i.legal_domain = $domain,
                    i.jurisdiction = $juris,
                    i.issuing_authority = $auth,
                    i.enactment_date = $enact,
                    i.effective_date = $eff,
                    i.repeal_date = null,
                    i.status = $status,
                    i.version = $version,
                    i.source_url = $src,
                    i.source_type = $stype,
                    i.official_source = $auth,
                    i.last_verified = $verified,
                    i.canonical_name = $canonical
                ON MATCH SET
                    i.title = $title,
                    i.legal_domain = $domain,
                    i.status = $status,
                    i.effective_date = $eff,
                    i.enactment_date = $enact
                RETURN count(*) AS c
                """,
                {
                    "iid": inst.instrument_id,
                    "title": inst.title,
                    "short": inst.short_title,
                    "itype": inst.instrument_type,
                    "domain": inst.legal_domain,
                    "juris": inst.jurisdiction,
                    "auth": inst.issuing_authority,
                    "enact": inst.enactment_date,
                    "eff": inst.effective_date,
                    "status": inst.status,
                    "version": "1.0",
                    "src": inst.source_uri,
                    "stype": inst.source_type,
                    "verified": _iso_now(),
                    "canonical": _normalise_name(inst.title),
                },
            )
            # Link instrument to its authority
            self._execute_write(
                f"""
                MATCH (i:{label} {{instrument_id: $iid}})
                MATCH (a:Authority {{authority_id: $auth}})
                MERGE (i)-[:ISSUED_BY]->(a)
                """,
                {"iid": inst.instrument_id, "auth": inst.issuing_authority},
            )
            # Link instrument to its jurisdiction
            self._execute_write(
                f"""
                MATCH (i:{label} {{instrument_id: $iid}})
                MATCH (j:Jurisdiction {{jurisdiction_id: $juris}})
                MERGE (i)-[:APPLIES_TO_JURISDICTION]->(j)
                """,
                {"iid": inst.instrument_id, "juris": inst.jurisdiction},
            )
            # Link instrument to its domain
            self._execute_write(
                f"""
                MATCH (i:{label} {{instrument_id: $iid}})
                MATCH (d:LegalDomain {{domain_name: $dom}})
                MERGE (i)-[:BELONGS_TO_DOMAIN]->(d)
                """,
                {"iid": inst.instrument_id, "dom": inst.legal_domain},
            )
            stats[inst.legal_domain] = stats.get(inst.legal_domain, 0) + 1

        # Wire inter-instrument relationships (RELATED_TO, AMENDS, etc.)
        for inst in PILOT_INSTRUMENTS:
            for rel_type, target_id, evidence in inst.relationships:
                target_label = self._instrument_label_for_id(target_id)
                if target_label is None:
                    # Target instrument not in pilot set — create a stub node
                    target_label = "Act"
                    self._execute_write(
                        f"""
                        MERGE (t:{target_label} {{instrument_id: $tid}})
                        ON CREATE SET t.title = $title, t.short_title = $title,
                                    t.instrument_type = 'stub',
                                    t.legal_domain = 'UNKNOWN',
                                    t.status = 'unknown',
                                    t.source_type = 'manual'
                        """,
                        {"tid": target_id, "title": target_id},
                    )
                self._execute_write(
                    f"""
                    MATCH (src:{self._instrument_label(inst.instrument_type)} {{instrument_id: $sid}})
                    MATCH (tgt:{target_label} {{instrument_id: $tid}})
                    MERGE (src)-[r:{rel_type}]->(tgt)
                    ON CREATE SET r.evidence = $evidence, r.confidence = 0.9, r.evidence_type = 'source'
                    ON MATCH SET r.evidence = $evidence
                    """,
                    {"sid": inst.instrument_id, "tid": target_id, "evidence": evidence},
                )

        logger.info("KG instruments loaded: %s", stats)
        return stats

    def _instrument_label(self, instrument_type: str) -> str:
        """Map instrument_type to Neo4j label."""
        mapping = {
            "act": "Act",
            "rule": "Rule",
            "regulation": "Regulation",
            "notification": "Notification",
            "order": "Order",
            "circular": "Circular",
            "guideline": "Guideline",
            "judgment": "Judgment",
        }
        return mapping.get(instrument_type, "Act")

    def _instrument_label_for_id(self, instrument_id: str) -> str | None:
        """Find the label for an instrument_id from the pilot set."""
        for inst in PILOT_INSTRUMENTS:
            if inst.instrument_id == instrument_id:
                return self._instrument_label(inst.instrument_type)
        return None

    # ------------------------------------------------------------------ #
    # Step 3: Load FSSAI provisions from existing DB chunks
    # ------------------------------------------------------------------ #

    def load_fssai_provisions(self) -> dict[str, int]:
        """Extract Section nodes from the existing LegalChunk table.

        Reads from the local DB (app.models.LegalChunk) and creates
        LegalProvision / Section nodes in Neo4j for each unique section
        found in the FSS Act chunks.  Each provision is linked back to a
        Chunk node via SUPPORTED_BY for provenance.

        Returns counts.
        """
        from app import create_app
        from app.models import LegalDocument, LegalChunk
        from app.extensions import db

        app = create_app()
        stats: dict[str, int] = {"provisions_created": 0, "provisions_updated": 0, "chunks_linked": 0}

        with app.app_context():
            # Get the FSS Act document
            fss_doc = db.session.execute(
                db.select(LegalDocument).filter(LegalDocument.source_uri.like("%Food_Safety%Act_2006%"))
            ).scalar_one_or_none()
            if fss_doc is None:
                logger.warning("FSS Act document not found in DB — skipping FSSAI provision load")
                return stats

            doc_id = fss_doc.id

            # Get all unique sections from chunks
            sec_values = db.session.execute(
                db.select(LegalChunk.section_number)
                .filter(
                    LegalChunk.document_id == doc_id,
                    LegalChunk.section_number.isnot(None),
                    LegalChunk.section_number != "0",
                )
                .distinct()
            ).scalars().all()

            section_numbers = sorted(set(s for s in sec_values if s), key=lambda x: (len(x), x))

            # Batch insert provisions
            batch: list[dict] = []
            for sec_num in section_numbers:
                provision_id = f"FSS_ACT_2006_SEC_{sec_num}"
                # Check if chunk text looks like a section header
                header_chunk = db.session.execute(
                    db.select(LegalChunk).filter(
                        LegalChunk.document_id == doc_id,
                        LegalChunk.section_number == sec_num,
                        LegalChunk.hierarchy_level == 1,
                    ).limit(1)
                ).scalar_one_or_none()

                section_text = header_chunk.text[:2000] if header_chunk and header_chunk.text else ""
                section_title = _extract_section_title(header_chunk.text) if header_chunk and header_chunk.text else None

                batch.append({
                    "provision_id": provision_id,
                    "provision_number": sec_num,
                    "title": section_title or f"Section {sec_num}",
                    "instrument_id": "FSS_ACT_2006",
                    "text": section_text,
                    "chunk_id": header_chunk.id if header_chunk else None,
                    "chunk_text": header_chunk.text[:500] if header_chunk and header_chunk.text else "",
                })

                if len(batch) >= BATCH_SIZE:
                    created, updated, linked = self._write_provisions_batch(batch, "FSS_ACT_2006")
                    stats["provisions_created"] += created
                    stats["provisions_updated"] += updated
                    stats["chunks_linked"] += linked
                    batch = []

            if batch:
                created, updated, linked = self._write_provisions_batch(batch, "FSS_ACT_2006")
                stats["provisions_created"] += created
                stats["provisions_updated"] += updated
                stats["chunks_linked"] += linked

            # Also link ALL chunks to their section provisions (full provenance)
            all_chunks = db.session.execute(
                db.select(LegalChunk).filter(
                    LegalChunk.document_id == doc_id,
                    LegalChunk.section_number.isnot(None),
                    LegalChunk.section_number != "0",
                )
            ).scalars().all()

            chunk_batch = []
            for chunk in all_chunks:
                sec = chunk.section_number
                if not sec or sec == "0":
                    continue
                provision_id = f"FSS_ACT_2006_SEC_{sec}"
                chunk_batch.append({
                    "chunk_id": chunk.id,  # This is the LegalChunk UUID
                    "qdrant_point_id": chunk.qdrant_point_id,
                    "provision_id": provision_id,
                    "chunk_text": (chunk.text or "")[:500],
                    "chunk_index": chunk.chunk_index,
                    "document_id": doc_id,
                })

                if len(chunk_batch) >= BATCH_SIZE:
                    linked = self._link_chunks_to_provisions(chunk_batch)
                    stats["chunks_linked"] += linked
                    chunk_batch = []

            if chunk_batch:
                linked = self._link_chunks_to_provisions(chunk_batch)
                stats["chunks_linked"] += linked

        logger.info("KG FSSAI provisions loaded: %s", stats)
        return stats

    def _write_provisions_batch(
        self,
        batch: list[dict],
        instrument_id: str,
    ) -> tuple[int, int, int]:
        """Write a batch of provision + chunk nodes to Neo4j.

        Returns (created_count, updated_count, chunks_linked).
        """
        created = 0
        updated = 0
        linked = 0

        for item in batch:
            pid = item["provision_id"]
            # MERGE provision
            result = self._execute_write(
                """
                MERGE (p:LegalProvision {provision_id: $pid})
                ON CREATE SET
                    p.provision_number = $pnum,
                    p.title = $title,
                    p.instrument_id = $inst,
                    p.legal_domain = 'FOOD_SAFETY',
                    p.status = 'current',
                    p.effective_from = date('2006-09-01'),
                    p.confidence = 0.95,
                    p.source = 'existing_db',
                    p.provision_text = $ptext
                ON MATCH SET
                    p.title = $title
                WITH p
                MATCH (i:Act {instrument_id: $inst})
                MERGE (i)-[:CONTAINS]->(p)
                WITH p
                MATCH (d:LegalDomain {domain_name: 'FOOD_SAFETY'})
                MERGE (p)-[:BELONGS_TO_DOMAIN]->(d)
                RETURN count(*) AS c
                """,
                {
                    "pid": pid,
                    "pnum": item["provision_number"],
                    "title": item["title"],
                    "inst": instrument_id,
                    "ptext": item.get("text", ""),
                },
            )
            created += 1

            # Link the header chunk to the provision
            if item.get("chunk_id"):
                self._execute_write(
                    """
                    MERGE (ch:Chunk {chunk_id: $cid})
                    ON CREATE SET ch.document_id = $doc_id,
                                ch.chunk_text = $ctxt,
                                ch.qdrant_point_id = $qpid
                    ON MATCH SET ch.chunk_text = $ctxt
                    WITH ch
                    MATCH (p:LegalProvision {provision_id: $pid})
                    MERGE (p)-[r:SUPPORTED_BY]->(ch)
                    ON CREATE SET r.confidence = 0.95, r.evidence_type = 'section_header'
                    WITH ch
                    MATCH (d:Document {document_id: $doc_id})
                    MERGE (d)-[:HAS_CHUNK]->(ch)
                    RETURN count(*) AS c
                    """,
                    {
                        "cid": item["chunk_id"],
                        "doc_id": item.get("document_id") or "",
                        "ctxt": item.get("chunk_text", "")[:500],
                        "qpid": item.get("qdrant_point_id"),
                        "pid": pid,
                    },
                )
                linked += 1

        return created, updated, linked

    def _link_chunks_to_provisions(self, chunks: list[dict]) -> int:
        """Link chunk nodes to provisions (provenance chain)."""
        linked = 0
        for chunk in chunks:
            self._execute_write(
                """
                MERGE (ch:Chunk {chunk_id: $cid})
                ON CREATE SET ch.document_id = $doc_id,
                            ch.chunk_index = $cidx,
                            ch.chunk_text = $ctxt,
                            ch.qdrant_point_id = $qpid
                ON MATCH SET ch.chunk_index = $cidx,
                            ch.chunk_text = $ctxt,
                            ch.qdrant_point_id = COALESCE(ch.qdrant_point_id, $qpid)
                WITH ch
                MATCH (p:LegalProvision {provision_id: $pid})
                MERGE (p)-[r:SUPPORTED_BY]->(ch)
                    ON CREATE SET r.confidence = 0.8, r.evidence_type = 'chunk_body'
                    RETURN count(*) AS c
                """,
                {
                    "cid": chunk["chunk_id"],
                    "doc_id": chunk["document_id"],
                    "cidx": chunk["chunk_index"],
                    "ctxt": chunk["chunk_text"][:500],
                    "qpid": chunk["qdrant_point_id"],
                    "pid": chunk["provision_id"],
                },
            )
            linked += 1
        return linked

    # ------------------------------------------------------------------ #
    # Step 4: Load stub provisions for non-FSSAI instruments
    # ------------------------------------------------------------------ #

    def load_stub_provisions(self) -> dict[str, int]:
        """Create LegalProvision + Chunk nodes for stub instruments.

        For each instrument in the pilot set that doesn't come from the DB
        (i.e., has `source_type='manual'`), creates Section nodes from
        PROVISION_STUBS and links them to a Document + Chunk node.
        """
        stats: dict[str, int] = {"provisions": 0, "chunks": 0, "relationships": 0}

        for inst in PILOT_INSTRUMENTS:
            if inst.source_type == "existing_db":
                continue  # FSS Act handled by load_fssai_provisions

            stubs = PROVISION_STUBS.get(inst.instrument_id, {})
            if not stubs:
                continue

            # Ensure a Document node exists for this instrument
            doc_id = inst.instrument_id
            self._execute_write(
                """
                MERGE (d:Document {document_id: $did})
                ON CREATE SET d.title = $title, d.document_type = $dtype,
                            d.legal_domain = $domain, d.source_uri = $uri,
                            d.source_type = 'manual', d.qdrant_collection = 'none'
                ON MATCH SET d.title = $title, d.legal_domain = $domain
                """,
                {
                    "did": doc_id,
                    "title": inst.title,
                    "dtype": inst.instrument_type,
                    "domain": inst.legal_domain,
                    "uri": inst.source_uri,
                },
            )

            for sec_num, (title, text) in stubs.items():
                provision_id = f"{inst.instrument_id}_SEC_{sec_num}"

                # Determine the label for the instrument
                label = self._instrument_label(inst.instrument_type)

                result = self._execute_write(
                    f"""
                    MERGE (p:LegalProvision {{provision_id: $pid}})
                    ON CREATE SET
                        p.provision_number = $pnum,
                        p.title = $title,
                        p.instrument_id = $inst,
                        p.legal_domain = $domain,
                        p.status = 'current',
                        p.effective_from = $eff,
                        p.effective_to = null,
                        p.confidence = 0.9,
                        p.source = 'manual_stub',
                        p.provision_text = $ptext,
                        p.version = '1.0'
                    ON MATCH SET
                        p.title = $title
                    WITH p
                    MATCH (i:{label} {{instrument_id: $inst}})
                    MERGE (i)-[:CONTAINS]->(p)
                    WITH p
                    MATCH (d:Document {{document_id: $did}})
                    MERGE (p)- [:SOURCE_OF]->(d)
                    WITH p, d
                    MERGE (d)-[:HAS_CHUNK]->(ch:Chunk {{chunk_id: $pid}})
                    ON CREATE SET ch.document_id = $did, ch.chunk_text = $ctxt
                    WITH p, ch
                    MERGE (p)-[r:SUPPORTED_BY]->(ch)
                    ON CREATE SET r.confidence = 0.9, r.evidence_type = 'manual_stub' 
                    RETURN count(*) AS c
                    """,
                    {
                        "pid": provision_id,
                        "pnum": sec_num,
                        "title": title,
                        "inst": inst.instrument_id,
                        "domain": inst.legal_domain,
                        "eff": inst.effective_date or "2023-01-01",
                        "ptext": text[:2000],
                        "did": doc_id,
                        "ctxt": text[:500],
                    },
                )
                stats["provisions"] += 1
                stats["chunks"] += 1

        logger.info("KG stub provisions loaded: %s", stats)
        return stats

    # ------------------------------------------------------------------ #
    # Step 5: Load cross-domain relationships
    # ------------------------------------------------------------------ #

    def load_cross_domain_relationships(self) -> int:
        """Create cross-domain provision-to-provision relationships.

        Only creates relationships from the CROSS_DOMAIN_RELATIONSHIPS registry
        — every link is source-supported by an evidence description.
        """
        count = 0
        for source_provision, rel_type, target_provision, evidence in CROSS_DOMAIN_RELATIONSHIPS:
            result = self._execute_write(
                f"""
                MATCH (src:LegalProvision {{provision_id: $src}})
                MATCH (tgt:LegalProvision {{provision_id: $tgt}})
                MERGE (src)-[r:{rel_type}]->(tgt)
                ON CREATE SET r.evidence = $ev, r.confidence = 0.85,
                            r.evidence_type = 'cross_domain_source',
                            r.source_document = 'manifest'
                ON MATCH SET r.evidence = $ev
                RETURN count(*) AS c
                """,
                {"src": source_provision, "tgt": target_provision, "ev": evidence},
            )
            for record in result.records:
                count += record["c"]
        logger.info("KG cross-domain relationships loaded: %d", count)
        return count

    # ------------------------------------------------------------------ #
    # Step 6: Load provision-to-concept mappings
    # ------------------------------------------------------------------ #

    def load_concept_relationships(self) -> int:
        """Create APPLIES_TO / IMPOSES_DUTY / CREATES_OFFENCE / etc. edges.

        Every edge is evidence-tagged with the source text that supports it.
        """
        count = 0
        for provision_id, concept_mappings in PROVISION_CONCEPT_MAP.items():
            for concept_id, rel_type, evidence in concept_mappings:
                result = self._execute_write(
                    f"""
                    MATCH (p:LegalProvision {{provision_id: $pid}})
                    MATCH (c:LegalConcept {{concept_id: $cid}})
                    MERGE (p)-[r:{rel_type}]->(c)
                    ON CREATE SET r.evidence = $ev, r.confidence = 0.9,
                                r.evidence_type = 'source_supported'
                    ON MATCH SET r.evidence = $ev
                    RETURN count(*) AS c
                    """,
                    {"pid": provision_id, "cid": concept_id, "ev": evidence},
                )
                for record in result.records:
                    count += record["c"]
        logger.info("KG concept relationships loaded: %d", count)
        return count

    # ------------------------------------------------------------------ #
    # Step 7: Load authority-provision relationships
    # ------------------------------------------------------------------ #

    def load_authority_relationships(self) -> int:
        """Link provisions to the authorities that enforce them.

        Uses the PROVISION_CONCEPT_MAP entries where concept_id is an
        authority_id (FSO, WB_FODDER_DEPT, KMC, etc.).
        """
        count = 0
        # Extract authority relationships from concept map
        auth_concepts = set(AUTHORITIES.keys())
        for provision_id, concept_mappings in PROVISION_CONCEPT_MAP.items():
            for concept_id, rel_type, evidence in concept_mappings:
                if concept_id in auth_concepts and rel_type in ("GRANTS_POWER_TO", "ENFORCED_BY"):
                    result = self._execute_write(
                        f"""
                        MATCH (p:LegalProvision {{provision_id: $pid}})
                        MATCH (a:Authority {{authority_id: $aid}})
                        MERGE (p)-[r:{rel_type}]->(a)
                        ON CREATE SET r.evidence = $ev, r.confidence = 0.9,
                                    r.evidence_type = 'source_supported'
                        ON MATCH SET r.evidence = $ev
                        RETURN count(*) AS c
                        """,
                        {"pid": provision_id, "aid": concept_id, "ev": evidence},
                    )
                    for record in result.records:
                        count += record["c"]
        logger.info("KG authority relationships loaded: %d", count)
        return count

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #

    def run_full_ingestion(self) -> dict[str, Any]:
        """Run the complete pilot ingestion pipeline.

        Steps:
        1. Setup schema (constraints + indexes)
        2. Load controlled vocabularies
        3. Load instruments
        4. Load FSSAI provisions from existing DB
        5. Load stub provisions for non-FSSAI instruments
        6. Load cross-domain relationships
        7. Load concept relationships (APPLIES_TO, IMPOSES_DUTY, etc.)
        8. Load authority relationships

        Returns a summary dict.
        """
        from kg.schema import setup_legal_kg_schema, clear_legal_kg

        results: dict[str, Any] = {}

        # Step 0: Clear existing legal KG (idempotent re-ingestion)
        deleted = clear_legal_kg(self._driver, self._database)
        results["cleared_nodes"] = deleted

        # Step 1: Schema
        schema_result = setup_legal_kg_schema(self._driver, self._database)
        results["schema"] = schema_result

        # Step 2: Vocabularies
        results["vocabularies"] = self.load_vocabularies()

        # Step 3: Instruments
        results["instruments"] = self.load_instruments()

        # Step 4: FSSAI provisions from DB
        results["fssai_provisions"] = self.load_fssai_provisions()

        # Step 5: Stub provisions
        results["stub_provisions"] = self.load_stub_provisions()

        # Step 6: Cross-domain relationships
        results["cross_domain_rels"] = self.load_cross_domain_relationships()

        # Step 7: Concept relationships
        results["concept_rels"] = self.load_concept_relationships()

        # Step 8: Authority relationships
        results["authority_rels"] = self.load_authority_relationships()

        # Summary counts
        node_counts = self._execute_write("RETURN 1")  # just to warm up
        counts = {}
        for label in ["Act", "Rule", "Regulation", "Notification", "LegalProvision",
                       "Authority", "LegalDomain", "LegalConcept", "Chunk", "Document"]:
            r = self._execute(f"MATCH (n:{label}) RETURN count(n) AS c")
            for row in r:
                counts[label] = row["c"]
        results["node_counts"] = counts

        edge_counts = {}
        for rel_type in ["CONTAINS", "SUPPORTED_BY", "BELONGS_TO_DOMAIN", "ISSUED_BY",
                          "BELONGS_TO_DOMAIN", "APPLIES_TO", "IMPOSES_DUTY", "CREATES_OFFENCE",
                          "RELATED_TO", "INTERACTS_WITH", "COMPLEMENTS", "CROSS_REFERENCES",
                          "GRANTS_POWER_TO", "PRESCRIBES", "REQUIRES", "HAS_CHUNK",
                          "SOURCE_OF", "PART_OF", "HAS_SUBSECTION"]:
            r = self._execute(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c")
            for row in r:
                edge_counts[rel_type] = row["c"]
        results["edge_counts"] = edge_counts

        logger.info("Full KG ingestion complete. Node counts: %s", counts)
        return results


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _iso_now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _extract_section_title(text: str | None) -> str | None:
    """Extract a section title from 'Section N: Title' header."""
    if not text:
        return None
    match = re.match(
        r"^\s*(?:Section|Sec\.?|§)\s*\d+\s*[:\-—.]?\s*(.+)$",
        text.strip(),
        re.IGNORECASE,
    )
    if match and match.group(1).strip():
        return match.group(1).strip()
    return None


def _normalise_name(name: str) -> str:
    """Normalise legal instrument name (strip 'The', collapse whitespace)."""
    text = re.sub(r"^(?:the|an|a)\s+", "", str(name or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


# End of ingestion.py
