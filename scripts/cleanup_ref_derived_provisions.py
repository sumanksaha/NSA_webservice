"""One-off remediation: remove ref-derived corpus provisions from the legal KG.

Background
----------
On 2026-10-03 an intermediate build resolved chunk sections from the Qdrant
``provision_ids`` / ``provision_spans`` fields.  Those were measured to be
degenerate — a single constant ref repeated over hundreds of chunks (``sog:s66``
on 130 of 141 Sale of Goods chunks, ``epa:s26.5`` on 1,625 environment
compilation chunks, 4 distinct refs across 633 Companies Act chunks).  The run
therefore wrote 169 fabricated ``<instrument>_SEC_<subsection>`` provisions and
inflated ``SUPPORTED_BY`` edges on legitimate provisions.

``kg.corpus_ingestion.resolve_section_keys`` now carries the last *declared*
``section_number`` forward instead, so a corrected rebuild reproduces the whole
chunk→provision edge set exactly.  That makes this cleanup safe: the edges are
deleted and immediately recreated by ``scripts/build_kg_corpus.py``.

Scope is deliberately narrow:

* Only ``LegalProvision`` nodes whose id contains ``_SEC_`` but not
  ``_CLAUSE_`` — i.e. corpus-section provisions, never the FSSAI
  clause-numbered ones built by a different code path.
* Subsection-qualified numbers (``26.5``, ``3(ii)``), which the section
  header path can never produce.

Every ``Chunk`` node in the graph originates from ``build_kg_corpus.py``, so
``SUPPORTED_BY`` edges may be rebuilt without losing anything written by the
pilot builder (``kg/ingestion.py``).

Usage::

    python scripts/cleanup_ref_derived_provisions.py --dry-run
    python scripts/cleanup_ref_derived_provisions.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

#: Fabricated nodes: subsection-qualified section numbers on corpus provisions.
DELETE_PROVISIONS = """
MATCH (p:LegalProvision)
WHERE p.provision_id CONTAINS '_SEC_'
  AND NOT p.provision_id CONTAINS '_CLAUSE_'
  AND p.provision_number =~ '.*[.()].*'
RETURN p.provision_id AS provision_id
"""

#: Stale links: every corpus-section provision link, rebuilt from scratch.
DELETE_EDGES = """
MATCH (p:LegalProvision)-[e:SUPPORTED_BY]->(:Chunk)
WHERE p.provision_id CONTAINS '_SEC_'
  AND NOT p.provision_id CONTAINS '_CLAUSE_'
RETURN count(e) AS n
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="Count only; delete nothing.")
    args = parser.parse_args(argv)

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )
    db = os.environ["NEO4J_DATABASE"]
    try:
        with driver.session(database=db) as session:
            provisions = [r["provision_id"] for r in session.run(DELETE_PROVISIONS)]
            edges = session.run(DELETE_EDGES).single()["n"]
            print(f"fabricated provisions: {len(provisions)}")
            print(f"corpus SUPPORTED_BY edges to rebuild: {edges}")
            if args.dry_run:
                print("dry run — nothing deleted")
                return 0
            if provisions:
                session.run(
                    """
                    UNWIND $ids AS pid
                    MATCH (p:LegalProvision {provision_id: pid})
                    DETACH DELETE p
                    """,
                    ids=provisions,
                )
            session.run(
                """
                MATCH (p:LegalProvision)-[e:SUPPORTED_BY]->(:Chunk)
                WHERE p.provision_id CONTAINS '_SEC_'
                  AND NOT p.provision_id CONTAINS '_CLAUSE_'
                DELETE e
                """,
            )
            print(f"deleted {len(provisions)} provisions and {edges} edges")
            print("now re-run: python scripts/build_kg_corpus.py --no-clear")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
