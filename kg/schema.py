"""Legal Knowledge Graph — Neo4j schema setup (constraints + indexes).

All Cypher statements use ``IF NOT EXISTS`` so the function is idempotent
and safe to call on every deployment or test run.  The new legal-instrument
labels do NOT conflict with the existing case-file labels (Case, FBO,
Inspector, Sample, Lab, Section, Evidence, Ancillary, Entity) — they are
completely separate namespaces in Neo4j.

Usage::

    from kg.schema import setup_legal_kg_schema
    summary = setup_legal_kg_schema()
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constraints — one per primary node label
# --------------------------------------------------------------------------- #

CONSTRAINTS_CYPHER: list[str] = [
    # --- Legal instruments (unique by instrument_id) ---
    "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Act) REQUIRE a.instrument_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (r:Rule) REQUIRE r.instrument_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (rg:Regulation) REQUIRE rg.instrument_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Notification) REQUIRE n.instrument_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (o:Order) REQUIRE o.instrument_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Circular) REQUIRE c.instrument_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (g:Guideline) REQUIRE g.instrument_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (j:Judgment) REQUIRE j.instrument_id IS UNIQUE",
    # --- Legal provisions (unique by provision_id) ---
    "CREATE CONSTRAINT IF NOT EXISTS FOR (p:LegalProvision) REQUIRE p.provision_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Section) REQUIRE s.provision_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (ss:Subsection) REQUIRE ss.provision_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (cl:Clause) REQUIRE cl.provision_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (sk:Schedule) REQUIRE sk.provision_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (rp:RuleProvision) REQUIRE rp.provision_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (rpg:RegulationProvision) REQUIRE rpg.provision_id IS UNIQUE",
    # --- Controlled vocabularies ---
    "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Authority) REQUIRE a.authority_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (d:LegalDomain) REQUIRE d.domain_name IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (c:LegalConcept) REQUIRE c.concept_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (jur:Jurisdiction) REQUIRE jur.jurisdiction_id IS UNIQUE",
    # --- Provenance ---
    "CREATE CONSTRAINT IF NOT EXISTS FOR (ch:Chunk) REQUIRE ch.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.document_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Source) REQUIRE s.source_id IS UNIQUE",
    # --- Enforcement ---
    "CREATE CONSTRAINT IF NOT EXISTS FOR (o:Offence) REQUIRE o.offence_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Penalty) REQUIRE p.penalty_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (i:Inspection) REQUIRE i.inspection_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (v:Violation) REQUIRE v.violation_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (no:Notice) REQUIRE no.notice_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (ob:Obligation) REQUIRE ob.obligation_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (pr:Prohibition) REQUIRE pr.prohibition_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (perm:Permission) REQUIRE perm.permission_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (pw:Power) REQUIRE pw.power_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (du:Duty) REQUIRE du.duty_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (proc:Procedure) REQUIRE proc.procedure_id IS UNIQUE",
    # --- Business / premises entities ---
    "CREATE CONSTRAINT IF NOT EXISTS FOR (fb:FoodBusiness) REQUIRE fb.business_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (ba:BusinessActivity) REQUIRE ba.activity_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (pr:Premises) REQUIRE pr.premises_id IS UNIQUE",
]

# --------------------------------------------------------------------------- #
# Indexes (non-unique) for fast lookup
# --------------------------------------------------------------------------- #

INDEXES_CYPHER: list[str] = [
    # Domain + status queries
    "CREATE INDEX IF NOT EXISTS FOR (a:Act) ON (a.legal_domain, a.status)",
    "CREATE INDEX IF NOT EXISTS FOR (r:Rule) ON (r.legal_domain, r.status)",
    "CREATE INDEX IF NOT EXISTS FOR (rg:Regulation) ON (rg.legal_domain, rg.status)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Notification) ON (n.legal_domain, n.status)",
    # Provision lookups
    "CREATE INDEX IF NOT EXISTS FOR (p:LegalProvision) ON (p.provision_number, p.instrument_id)",
    "CREATE INDEX IF NOT EXISTS FOR (p:LegalProvision) ON (p.legal_domain)",
    "CREATE INDEX IF NOT EXISTS FOR (p:LegalProvision) ON (p.status)",
    "CREATE INDEX IF NOT EXISTS FOR (s:Section) ON (s.instrument_id)",
    "CREATE INDEX IF NOT EXISTS FOR (p:LegalProvision) ON (p.confidence)",
    # Authority lookups
    "CREATE INDEX IF NOT EXISTS FOR (a:Authority) ON (a.name)",
    "CREATE INDEX IF NOT EXISTS FOR (a:Authority) ON (a.jurisdiction)",
    # Concept lookups
    "CREATE INDEX IF NOT EXISTS FOR (c:LegalConcept) ON (c.domains)",
    # Document/Chunk lookups
    "CREATE INDEX IF NOT EXISTS FOR (ch:Chunk) ON (ch.document_id)",
    "CREATE INDEX IF NOT EXISTS FOR (d:Document) ON (d.legal_domain)",
    "CREATE INDEX IF NOT EXISTS FOR (s:Section) ON (s.instrument_id)",
]


def setup_legal_kg_schema(driver: Any | None = None, database: str = "neo4j") -> dict[str, Any]:
    """Create all legal-KG constraints and indexes in Neo4j.

    Idempotent — all statements use ``IF NOT EXISTS``.  Returns a summary
    dict with counts of constraints and indexes created.

    Args:
        driver: Optional pre-built Neo4j driver (for testing).  When None,
                the driver is built from environment variables via
                ``app.services.neo4j_graph._get_driver``.
        database: Neo4j database name (default: from NEO4J_DATABASE env).

    Returns:
        ``{"constraints_added": N, "indexes_added": N, "existing": True}``
    """
    if driver is None:
        from app.services.neo4j_graph import _get_driver, neo4j_configured

        if not neo4j_configured():
            return {"error": "Neo4j not configured", "constraints_added": 0, "indexes_added": 0}
        driver = _get_driver()

    import os

    database = os.environ.get("NEO4J_DATABASE", database)

    constraints_added = 0
    indexes_added = 0

    # Wrap each statement so one failure doesn't block the rest
    for cypher in CONSTRAINTS_CYPHER:
        try:
            result = driver.execute_query(cypher, database_=database)
            if hasattr(result, "summary"):
                counters = getattr(result.summary, "counters", None)
                if counters:
                    constraints_added += getattr(counters, "constraints_added", 0) or 0
        except Exception as exc:
            logger.warning("Constraint failed (%s): %s", cypher[:60], exc)

    for cypher in INDEXES_CYPHER:
        try:
            result = driver.execute_query(cypher, database_=database)
            if hasattr(result, "summary"):
                counters = getattr(result.summary, "counters", None)
                if counters:
                    indexes_added += getattr(counters, "indexes_added", 0) or 0
        except Exception as exc:
            logger.warning("Index failed (%s): %s", cypher[:60], exc)

    logger.info(
        "Legal KG schema: %d constraints, %d indexes (existing=%s)",
        constraints_added,
        indexes_added,
        constraints_added == 0 and indexes_added == 0,
    )
    return {
        "constraints_added": constraints_added,
        "indexes_added": indexes_added,
        "existing": constraints_added == 0 and indexes_added == 0,
    }


def clear_legal_kg(driver: Any | None = None, database: str = "neo4j") -> int:
    """Delete ALL legal-instrument nodes (and their relationships) from Neo4j.

    This clears the LEGAL KG only — case-file labels (Case, FBO, etc.) are
    untouched.  Useful before a re-ingestion.

    Args:
        driver: Optional pre-built driver.
        database: Neo4j database name.

    Returns:
        Number of nodes deleted.

    Raises:
        RuntimeError: when ``NEO4J_ALLOW_WRITE`` is not ``1`` (fail-closed
            guard — this function deletes every legal-KG node).
    """
    if driver is None:
        from app.services.neo4j_graph import _get_driver, neo4j_configured

        if not neo4j_configured():
            return 0
        driver = _get_driver()

    import os

    database = os.environ.get("NEO4J_DATABASE", database)

    # Fail-closed write guard (2026-08-12): clear_legal_kg deletes the whole
    # legal KG, so it requires an explicit NEO4J_ALLOW_WRITE=1.  Deliberate
    # rebuilds (scripts/build_kg_corpus.py) set it; incidental callers
    # (test fixtures, misconfigured triggers) are refused.
    from app.services.neo4j_graph import neo4j_writes_allowed

    if not neo4j_writes_allowed():
        raise RuntimeError(
            "Refusing to clear the legal KG: set NEO4J_ALLOW_WRITE=1 to allow "
            "clear_legal_kg() (e.g. NEO4J_ALLOW_WRITE=1 python scripts/build_kg_corpus.py)."
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
        "Inspection",
        "Violation",
        "Notice",
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

    deleted_total = 0
    for label in legal_labels:
        result = driver.execute_query(
            f"MATCH (n:{label}) DETACH DELETE n RETURN count(*) AS deleted",
            database_=database,
        )
        for record in result.records:
            deleted_total += record["deleted"]

    logger.info("Legal KG cleared: %d nodes deleted", deleted_total)
    return deleted_total


# End of schema.py
