"""Legal KG — validation queries (structural + legal + cross-domain).

Runs Cypher validation queries against the Neo4j graph to detect:
- Structural issues: orphan provisions, provisions without instruments
- Legal issues: unsupported authority relationships, missing domains
- Cross-domain issues: test queries from the acceptance criteria

Every check returns a dict with ``passed``, ``issues``, and ``count``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class KGValidator:
    """Run structural and legal validation on the legal KG.

    Args:
        driver: Optional pre-built Neo4j driver (injected for tests).
        database: Neo4j database name.
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
        import os
        database = self._database or os.environ.get("NEO4J_DATABASE", "neo4j")
        result = self._get_driver().execute_query(
            cypher, parameters_=params or {}, database_=database
        )
        return [dict(r) for r in result.records]

    # ------------------------------------------------------------------ #
    # Structural validation
    # ------------------------------------------------------------------ #

    def check_orphan_provisions(self) -> dict[str, Any]:
        """Find LegalProvision nodes with no instrument parent."""
        results = self._execute("""
            MATCH (p:LegalProvision)
            WHERE NOT (p)<-[:CONTAINS]-(i)
            RETURN p.provision_id AS provision_id, p.title AS title
            LIMIT 50
        """)
        return {
            "check": "orphan_provisions",
            "passed": len(results) == 0,
            "issues": [{"provision_id": r["provision_id"], "title": r["title"]} for r in results],
            "count": len(results),
        }

    def check_provisions_without_domain(self) -> dict[str, Any]:
        """Find LegalProvision nodes without a BELONGS_TO_DOMAIN edge."""
        results = self._execute("""
            MATCH (p:LegalProvision)
            WHERE NOT (p)-[:BELONGS_TO_DOMAIN]->(:LegalDomain)
            RETURN p.provision_id AS provision_id, p.title AS title
            LIMIT 50
        """)
        return {
            "check": "provisions_without_domain",
            "passed": len(results) == 0,
            "issues": [{"provision_id": r["provision_id"], "title": r["title"]} for r in results],
            "count": len(results),
        }

    def check_instruments_without_domain(self) -> dict[str, Any]:
        """Find instrument nodes without a BELONGS_TO_DOMAIN edge."""
        results = self._execute("""
            MATCH (i)
            WHERE (i:Act OR i:Rule OR i:Regulation OR i:Notification)
            AND NOT (i)-[:BELONGS_TO_DOMAIN]->(:LegalDomain)
            RETURN i.instrument_id AS instrument_id, i.title AS title
            LIMIT 50
        """)
        return {
            "check": "instruments_without_domain",
            "passed": len(results) == 0,
            "issues": [{"instrument_id": r["instrument_id"], "title": r["title"]} for r in results],
            "count": len(results),
        }

    def check_chunks_without_provenance(self) -> dict[str, Any]:
        """Find Chunk nodes with no SUPPORTED_BY edge back to a provision."""
        results = self._execute("""
            MATCH (ch:Chunk)
            WHERE NOT (ch)<-[:SUPPORTED_BY]-(p:LegalProvision)
            RETURN ch.chunk_id AS chunk_id, ch.qdrant_point_id AS qdrant_point_id
            LIMIT 50
        """)
        return {
            "check": "chunks_without_provenance",
            "passed": len(results) == 0,
            "issues": [{"chunk_id": r["chunk_id"], "qdrant_point_id": r["qdrant_point_id"]} for r in results],
            "count": len(results),
        }

    def check_duplicate_instruments(self) -> dict[str, Any]:
        """Find instruments with duplicate titles."""
        results = self._execute("""
            MATCH (i)
            WHERE i:Act OR i:Rule OR i:Regulation OR i:Notification
            WITH i.title AS title, count(*) AS cnt
            WHERE cnt > 1
            RETURN title, cnt
            LIMIT 20
        """)
        return {
            "check": "duplicate_instruments",
            "passed": len(results) == 0,
            "issues": [{"title": r["title"], "count": r["cnt"]} for r in results],
            "count": len(results),
        }

    def check_invalid_relationship_types(self) -> dict[str, Any]:
        """Find relationships with unexpected types.

        Verifies that all relationship types in the legal KG are from
        the allowed set defined in the schema.
        """
        allowed = [
            "CONTAINS", "HAS_SUBSECTION", "HAS_CLAUSE", "HAS_SCHEDULE",
            "MADE_UNDER", "AMENDS", "REPEALS", "REPLACES",
            "APPLIES_TO", "RELATES_TO", "IMPOSES_DUTY", "CREATES_OBLIGATION",
            "CREATES_PROHIBITION", "CREATES_OFFENCE", "PRESCRIBES_PENALTY",
            "PRESCRIBES", "REQUIRES", "GRANTS_PERMISSION", "GRANTS_POWER_TO",
            "ENFORCED_BY", "REQUIRES_AUTHORIZATION_FROM",
            "RELATED_TO", "INTERACTS_WITH", "COMPLEMENTS",
            "CROSS_REFERENCES", "DEPENDS_ON", "OVERLAPS_WITH",
            "SOURCE_OF", "HAS_CHUNK", "SUPPORTED_BY",
            "BELONGS_TO_DOMAIN", "APPLIES_TO_JURISDICTION",
            "ISSUED_BY", "RELEVANT_IN",
            "INVOLVES", "TRIGGERS", "FINDS", "VIOLATES", "HAS_PENALTY",
            "TRIGGERS_NOTICE",
        ]
        results = self._execute("""
            MATCH (n)-[r]->(m)
            WHERE (n:LegalProvision OR n:Act OR n:Rule OR n:Regulation
                   OR n:Notification OR n:Authority OR n:LegalConcept
                   OR n:LegalDomain OR n:Chunk OR n:Document
                   OR n:Offence OR n:Penalty OR n:Inspection OR n:Violation
                   OR n:Notice OR n:Obligation OR n:Prohibition
                   OR n:Permission OR n:Power OR n:Duty OR n:Procedure
                   OR n:FoodBusiness OR n:BusinessActivity OR n:Premises
                   OR n:Judgment OR n:Order OR n:Circular OR n:Guideline)
            AND (m:LegalProvision OR m:Act OR m:Rule OR m:Regulation
                 OR m:Notification OR m:Authority OR m:LegalConcept
                 OR m:LegalDomain OR m:Chunk OR m:Document
                 OR m:Offence OR m:Penalty OR m:Inspection OR m:Violation
                 OR m:Notice OR m:Obligation OR m:Prohibition
                 OR m:Permission OR m:Power OR m:Duty OR m:Procedure
                 OR m:FoodBusiness OR m:BusinessActivity OR m:Premises
                 OR m:Judgment OR m:Order OR m:Circular OR m:Guideline)
            AND NOT type(r) IN $allowed
            RETURN DISTINCT type(r) AS rel_type, count(*) AS cnt
            LIMIT 20
        """, {"allowed": allowed})
        return {
            "check": "invalid_relationship_types",
            "passed": len(results) == 0,
            "issues": [{"type": r["rel_type"], "count": r["cnt"]} for r in results],
            "count": len(results),
        }

    # ------------------------------------------------------------------ #
    # Legal validation
    # ------------------------------------------------------------------ #

    def check_unsupported_authority_relationships(self) -> dict[str, Any]:
        """Find authority relationships without evidence/confidence.

        Every GRANTS_POWER_TO / ENFORCED_BY / REQUIRES_AUTHORIZATION_FROM
        edge must have evidence text and confidence >= 0.8.
        """
        results = self._execute("""
            MATCH (p:LegalProvision)-[r:GRANTS_POWER_TO|ENFORCED_BY|REQUIRES_AUTHORIZATION_FROM]->(a:Authority)
            WHERE (r.evidence IS NULL OR r.evidence = '')
               OR (r.confidence IS NULL OR r.confidence < 0.8)
            RETURN p.provision_id AS provision_id,
                   a.name AS authority,
                   type(r) AS rel_type,
                   r.confidence AS confidence,
                   r.evidence AS evidence
            LIMIT 50
        """)
        return {
            "check": "unsupported_authority_relationships",
            "passed": len(results) == 0,
            "issues": [
                {
                    "provision_id": r["provision_id"],
                    "authority": r["authority"],
                    "relationship_type": r["rel_type"],
                    "confidence": r["confidence"],
                    "evidence": r["evidence"],
                }
                for r in results
            ],
            "count": len(results),
        }

    def check_wrong_parent_links(self) -> dict[str, Any]:
        """Find Rule/Regulation provisions linked to wrong Act instruments."""
        results = self._execute("""
            MATCH (i:Regulation)-[:CONTAINS]->(p:LegalProvision)
            MATCH (i)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain {domain_name: 'ANIMAL_SLAUGHTER'})
            WHERE NOT (i)-[:MADE_UNDER]->(:Act)
            RETURN i.instrument_id AS instrument_id, i.title AS title
            LIMIT 20
        """)
        return {
            "check": "wrong_parent_links",
            "passed": len(results) == 0,
            "issues": [{"instrument_id": r["instrument_id"], "title": r["title"]} for r in results],
            "count": len(results),
        }

    def check_no_hallucinated_relationships(self) -> dict[str, Any]:
        """Verify no CONFLICTS_WITH / OVERRIDES / INVALIDATES / APPLIES edges exist."""
        forbidden = ["CONFLICTS_WITH", "OVERRIDES", "INVALIDATES", "APPLIES"]
        found_types: list[dict] = []
        for rel in forbidden:
            results = self._execute(f"""
                MATCH (n)-[r:{rel}]->(m)
                RETURN type(r) AS rel_type, count(*) AS cnt
            """)
            for r in results:
                found_types.append({"type": r["rel_type"], "count": r["cnt"]})
        return {
            "check": "no_hallucinated_relationships",
            "passed": len(found_types) == 0,
            "issues": found_types,
            "count": len(found_types),
        }

    # ------------------------------------------------------------------ #
    # Cross-domain validation
    # ------------------------------------------------------------------ #

    def check_cross_domain_retrieval(self, concept: str) -> dict[str, Any]:
        """Verify a concept returns provisions from multiple domains."""
        results = self._execute("""
            MATCH (c:LegalConcept {name: $concept})<-[:APPLIES_TO]-(p:LegalProvision)
            MATCH (p)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain)
            RETURN collect(DISTINCT d.domain_name) AS domains
            LIMIT 1
        """, {"concept": concept})
        if not results:
            return {"check": "cross_domain_retrieval", "passed": False, "domains": [], "count": 0}
        domains = results[0]["domains"] or []
        return {
            "check": "cross_domain_retrieval",
            "passed": len(domains) > 1,
            "domains": list(domains),
            "count": len(domains),
        }

    def check_domain_separation(self) -> dict[str, Any]:
        """Verify FSSAI provisions don't indiscriminately return other domains."""
        results = self._execute("""
            MATCH (p:LegalProvision)-[:BELONGS_TO_DOMAIN]->(d:LegalDomain {domain_name: 'FOOD_SAFETY'})
            RETURN count(DISTINCT p) AS food_safety_count,
                   count(DISTINCT CASE WHEN (p)-[:BELONGS_TO_DOMAIN]->(:LegalDomain {domain_name: 'LAND_PREMISES'}) THEN p END) AS land_premises_count
        """)
        if not results:
            return {"check": "domain_separation", "passed": False, "food_safety_count": 0, "land_premises_count": 0}
        row = results[0]
        fs_count = row["food_safety_count"] or 0
        lp_count = row["land_premises_count"] or 0
        return {
            "check": "domain_separation",
            "passed": lp_count == 0,  # FSSAI provisions must NOT have LAND_PREMISES domain
            "food_safety_count": fs_count,
            "land_premises_count": lp_count,
        }

    # ------------------------------------------------------------------ #
    # Full run
    # ------------------------------------------------------------------ #

    def run_all_checks(self) -> dict[str, Any]:
        """Run all validation checks and return a summary."""
        checks = [
            self.check_orphan_provisions,
            self.check_provisions_without_domain,
            self.check_instruments_without_domain,
            self.check_chunks_without_provenance,
            self.check_duplicate_instruments,
            self.check_invalid_relationship_types,
            self.check_unsupported_authority_relationships,
            self.check_wrong_parent_links,
            self.check_no_hallucinated_relationships,
            self.check_domain_separation,
        ]
        results = [check() for check in checks]

        # Cross-domain retrieval tests
        for concept in ["Slaughterhouse", "FoodBusiness", "Wastewater", "Licence", "Premises"]:
            results.append(self.check_cross_domain_retrieval(concept))

        passed = sum(1 for r in results if r["passed"])
        failed = sum(1 for r in results if not r["passed"])
        return {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "all_passed": failed == 0,
            "details": results,
        }


# End of validation.py
