"""
Connect to Neo4j Aura and load a knowledge graph.

Reads connection details from .env file:
    NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE

Usage:
    python neo4j_aura_loader.py           # Load the full knowledge graph
    python neo4j_aura_loader.py --test    # Test connection only

WARNING: loading clears the WHOLE graph first (``MATCH (n) DETACH DELETE n``).
Refuses to run unless ``NEO4J_ALLOW_WRITE=1`` is set (fail-closed guard added
2026-08-12 after ``push_to_neo4j`` wiped the 29k-node legal KG from a test).
"""

import os
import sys

from dotenv import load_dotenv
from neo4j import GraphDatabase, basic_auth  # pyright: ignore[reportMissingImports]


def load_env():
    """Load environment variables from .env file."""
    load_dotenv()
    uri = os.environ.get("NEO4J_URI", "")
    username = os.environ.get("NEO4J_USERNAME", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")

    if not uri or not password:
        raise SystemExit(1)

    return uri, username, password, database


def get_driver(uri, username, password, database):
    """Create and verify a Neo4j driver for Aura."""
    auth = basic_auth(username, password)
    driver = GraphDatabase.driver(uri, auth=auth)
    driver.verify_connectivity(database=database)
    return driver


def load_knowledge_graph(driver, database, clear_first=True):
    """
    Load sample knowledge graph data into the specified database.

    Replace this function with your own data-loading logic,
    or extend it with your real entities/relationships.
    """
    # --- Sample data: a mini legal-case knowledge graph ---
    # Nodes: Person, Organization, Act, Case, Section
    # Edges: KNOWN_AS, ENACTED, CITES, FILED_UNDER, PRESIDED_BY

    if clear_first and os.environ.get("NEO4J_ALLOW_WRITE", "0").lower() not in ("1", "true", "yes"):
        raise SystemExit(1)

    if clear_first:
        driver.execute_query("MATCH (n) DETACH DELETE n", database=database)

    # Merge nodes + relationships in a single transaction (idempotent)
    driver.execute_query(
        """
        // Persons
        MERGE (p1:Person {name: 'Ramesh Kumar'})
        MERGE (p2:Person {name: 'Food Safety Officer'})

        // Organizations
        MERGE (o1:Organization {name: 'FSSAI'})
        MERGE (o2:Organization {name: 'Local Municipal Council'})

        // Acts
        MERGE (a1:Act {title: 'Food Safety and Standards Act, 2006'})
        MERGE (a2:Act {title: 'Prevention of Food Adulteration Act, 1954'})

        // Cases
        MERGE (c1:Case {title: 'Ramesh Kumar v. State', year: 2024})

        // Sections
        MERGE (s1:Section {number: '33', act: a1.title})
        MERGE (s2:Section {number: '31', act: a1.title})

        // Relationships
        MERGE (p1)-[:VIOLATES]->(s1)
        MERGE (p1)-[:VIOLATES]->(s2)
        MERGE (c1)-[:FILED_UNDER]->(s1)
        MERGE (c1)-[:PRESIDED_BY]->(o1)
        MERGE (o1)-[:ENACTED]->(a1)
        MERGE (a1)-[:REPEALED]->(a2)
        MERGE (p2)-[:KNOWN_AS]->(o2)
        MERGE (o2)-[:REFERENCES]->(a1)
        """,
        database=database,
    )

    # Verify: count total nodes
    result = driver.execute_query("MATCH (n) RETURN count(n) AS total", database=database)
    result.records[0]["total"]


def main():
    uri, username, password, database = load_env()


    try:
        driver = get_driver(uri, username, password, database)
    except Exception:
        raise SystemExit(1) from None


    if "--test" in sys.argv:
        driver.close()
        return

    load_knowledge_graph(driver, database, clear_first=True)
    driver.close()


if __name__ == "__main__":
    main()
