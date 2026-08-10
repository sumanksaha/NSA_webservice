"""Neo4j Aura service for knowledge-graph persistence.

Manages the Bolt connection to the Neo4j Aura instance and provides
methods to push the local entity/relationship graph into Aura as a
Cypher node-link graph.

Uses APOC (available on Aura) for dynamic node labels — each entity type
becomes a real Neo4j label (``:Case``, ``:FBO``, ``:Section``, etc.) rather
than a single ``Entity`` catch-all. Falls back to ``CREATE`` with a static
``Entity`` label when APOC is unavailable.

Uses the same lazy-import / graceful-degradation pattern as the rest of
the codebase: if the ``neo4j`` driver or the connection env vars are not
available, functions raise ``RuntimeError`` with a helpful message.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.extensions import db
from app.models.document import Entity, Relationship

logger = logging.getLogger(__name__)

# Node type → Neo4j label
_NODE_LABELS: dict[str, str] = {
    "case": "Case",
    "fbo": "FBO",
    "inspector": "Inspector",
    "sample": "Sample",
    "lab": "Lab",
    "section": "Section",
    "evidence": "Evidence",
    "ancillary": "Ancillary",
}

# Edge type → stored as a property on a :RELATIONSHIP edge
_EDGE_TYPES: dict[str, str] = {
    "INSPECTED_BY": "INSPECTED_BY",
    "SAMPLED_FROM": "SAMPLED_FROM",
    "TESTED_AT": "TESTED_AT",
    "VIOLATED_SECTION": "VIOLATED_SECTION",
    "SUPPORTED_BY": "SUPPORTED_BY",
    "REFERENCES": "REFERENCES",
}

# Constraints + indexes to set up on first push (idempotent via IF NOT EXISTS)
_CONSTRAINTS_CYPHER: list[str] = [
    # Uniqueness: each local_id maps to exactly one node
    "CREATE CONSTRAINT entity_local_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.local_id IS UNIQUE",
    # Per-label uniqueness (APOC path)
    "CREATE CONSTRAINT case_local_id IF NOT EXISTS FOR (n:Case) REQUIRE n.local_id IS UNIQUE",
    "CREATE CONSTRAINT fbo_local_id IF NOT EXISTS FOR (n:FBO) REQUIRE n.local_id IS UNIQUE",
    "CREATE CONSTRAINT inspector_local_id IF NOT EXISTS FOR (n:Inspector) REQUIRE n.local_id IS UNIQUE",
    "CREATE CONSTRAINT sample_local_id IF NOT EXISTS FOR (n:Sample) REQUIRE n.local_id IS UNIQUE",
    "CREATE CONSTRAINT lab_local_id IF NOT EXISTS FOR (n:Lab) REQUIRE n.local_id IS UNIQUE",
    "CREATE CONSTRAINT section_local_id IF NOT EXISTS FOR (n:Section) REQUIRE n.local_id IS UNIQUE",
    "CREATE CONSTRAINT evidence_local_id IF NOT EXISTS FOR (n:Evidence) REQUIRE n.local_id IS UNIQUE",
    "CREATE CONSTRAINT ancillary_local_id IF NOT EXISTS FOR (n:Ancillary) REQUIRE n.local_id IS UNIQUE",
]

_INDEXES_CYPHER: list[str] = [
    "CREATE INDEX entity_type_index IF NOT EXISTS FOR (e:Entity) ON (e.entity_type)",
    "CREATE INDEX entity_name_index IF NOT EXISTS FOR (e:Entity) ON (e.name)",
    "CREATE INDEX rel_type_index IF NOT EXISTS FOR ()-[r:RELATIONSHIP]-() ON (r.type)",
]


def neo4j_configured() -> bool:
    """True when all Neo4j Aura env vars are present."""
    return bool(os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_USERNAME") and os.environ.get("NEO4J_PASSWORD"))


def _get_driver():
    """Create a Neo4j driver from env vars. Raises if not configured."""
    if not neo4j_configured():
        raise RuntimeError("Neo4j not configured. Set NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD in .env")
    from neo4j import GraphDatabase, basic_auth  # pyright: ignore[reportMissingImports]

    uri = os.environ["NEO4J_URI"]
    auth = basic_auth(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
    return GraphDatabase.driver(uri, auth=auth)


def _entity_to_node(entity: Entity) -> dict[str, Any]:
    """Convert a local Entity row to a Neo4j node dict."""
    import json as _json

    meta = {}
    if entity.metadata_json:
        try:
            meta = _json.loads(entity.metadata_json)
        except (ValueError, TypeError):
            logger.debug("Failed to parse metadata_json for entity %s", entity.id)

    return {
        "id": entity.id,
        "label": _NODE_LABELS.get(entity.entity_type, "Entity"),
        "name": entity.name,
        "entity_type": entity.entity_type,
        "source_table": entity.source_table,
        "source_id": entity.source_id,
        **meta,
    }


def _relationship_to_edge(rel: Relationship) -> dict[str, Any]:
    """Convert a local Relationship row to a Neo4j edge dict."""
    return {
        "source_id": rel.source_id,
        "target_id": rel.target_id,
        "type": _EDGE_TYPES.get(rel.relationship_type, rel.relationship_type),
        "weight": rel.weight,
    }


def build_cypher_payload() -> dict[str, Any]:
    """Build a JSON-serializable Cypher payload from local Entity/Relationship rows.

    Returns a dict with ``nodes`` and ``edges`` lists suitable for
    Cypher ``UNWIND`` or direct ingestion.
    """
    entities = db.session.execute(db.select(Entity).order_by(Entity.id)).scalars().all()
    relationships = db.session.execute(db.select(Relationship).order_by(Relationship.id)).scalars().all()

    nodes = [_entity_to_node(e) for e in entities]
    edges = [_relationship_to_edge(r) for r in relationships]

    return {
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def setup_constraints_and_indexes() -> dict[str, Any]:
    """Create uniqueness constraints and indexes on the Neo4j Aura database.

    All constraints use ``IF NOT EXISTS`` so they're idempotent on re-runs.
    Returns a summary dict.
    """
    driver = _get_driver()
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    created_constraints = 0
    created_indexes = 0

    try:
        for cypher in _CONSTRAINTS_CYPHER:
            result = driver.execute_query(  # type: ignore[call-arg]
                cypher, database_=database
            )
            # execute_query returns summary with count of constraints added
            if hasattr(result, "summary"):
                created_constraints += result.summary.counters.constraints_added or 0

        for cypher in _INDEXES_CYPHER:
            result = driver.execute_query(  # type: ignore[call-arg]
                cypher, database_=database
            )
            if hasattr(result, "summary"):
                created_indexes += result.summary.counters.indexes_added or 0
    finally:
        driver.close()

    return {
        "constraints_added": created_constraints,
        "indexes_added": created_indexes,
        "existing": True,  # IF NOT EXISTS means pre-existing ones are skipped
    }


def push_to_neo4j(
    case_type: str | None = None,
    case_id: int | None = None,
    use_apoc: bool = True,
) -> dict[str, Any]:
    """Push the local knowledge graph into Neo4j Aura.

    Args:
        case_type: Optional filter (currently unused — pushes full graph).
        case_id: Optional specific case ID to push (currently unused — pushes full graph).
        use_apoc: If True, use ``apoc.create.node`` for dynamic labels.
                  Falls back to ``CREATE (:Entity {...})`` if APOC fails.

    Returns a summary dict with ``nodes``, ``edges``, ``deleted``, ``created``.
    """
    payload = build_cypher_payload()

    # Ensure constraints/indexes exist before loading data
    try:
        setup_constraints_and_indexes()
    except Exception as exc:
        logger.warning("Constraint/index setup failed: %s", exc)

    driver = _get_driver()
    database = os.environ.get("NEO4J_DATABASE", "neo4j")

    try:
        # Clear the graph (idempotent on re-push)
        driver.execute_query(
            "MATCH (n) DETACH DELETE n",
            database_=database,  # type: ignore[call-arg]
        )

        if use_apoc:
            # APOC-based dynamic labels — each node gets its real label
            try:
                driver.execute_query(
                    """
                    UNWIND $nodes AS n
                    CALL apoc.create.node([n.label], {
                        name: n.name, entity_type: n.entity_type,
                        node_label: n.label, source_table: n.source_table,
                        source_id: n.source_id, local_id: n.id,
                        created_at: timestamp()
                    }) YIELD node
                    RETURN count(*) AS created
                    """,
                    parameters_={"nodes": payload["nodes"]},  # type: ignore[call-arg]
                    database_=database,
                )
            except Exception as exc:
                logger.warning("APOC push failed (%s) — falling back to static label", exc)
                use_apoc = False

        if not use_apoc:
            # Fallback: single :Entity label (no APOC required)
            driver.execute_query(
                """
                WITH $nodes AS nodes
                UNWIND nodes AS n
                CREATE (e:Entity {
                    name: n.name, entity_type: n.entity_type,
                    node_label: n.label, source_table: n.source_table,
                    source_id: n.source_id, local_id: n.id,
                    created_at: timestamp()
                })
                """,
                parameters_={"nodes": payload["nodes"]},  # type: ignore[call-arg]
                database_=database,
            )

        # Push edges — same approach regardless of APOC for nodes
        if use_apoc:
            try:
                driver.execute_query(
                    """
                    UNWIND $edges AS e
                    MATCH (src {local_id: e.source_id})
                    MATCH (tgt {local_id: e.target_id})
                    CALL apoc.create.relationship(src, 'RELATIONSHIP', {
                        type: e.type, weight: e.weight
                    }, tgt) YIELD rel
                    RETURN count(*) AS created
                    """,
                    parameters_={"edges": payload["edges"]},  # type: ignore[call-arg]
                    database_=database,
                )
            except Exception as exc:
                logger.warning("APOC rel push failed (%s) — falling back", exc)
                use_apoc = False

        if not use_apoc:
            # Fallback: standard MERGE on Entity label
            driver.execute_query(
                """
                UNWIND $edges AS e
                MATCH (src:Entity {local_id: e.source_id})
                MATCH (tgt:Entity {local_id: e.target_id})
                MERGE (src)-[r:RELATIONSHIP {type: e.type}]->(tgt)
                SET r.weight = e.weight
                """,
                parameters_={"edges": payload["edges"]},  # type: ignore[call-arg]
                database_=database,
            )

        return {
            "nodes": payload["node_count"],
            "edges": payload["edge_count"],
            "deleted": "all",
            "created": "new",
            "apoc_used": use_apoc,
        }
    finally:
        driver.close()


def query_neo4j(cypher: str, params: dict | None = None) -> list[dict]:
    """Run an arbitrary Cypher query against the Aura instance.

    Returns a list of dict result rows.
    """
    driver = _get_driver()
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    try:
        result = driver.execute_query(  # type: ignore[call-arg]
            cypher,
            parameters_=params or {},
            database_=database,
        )
        return [dict(r) for r in result.records]
    finally:
        driver.close()
