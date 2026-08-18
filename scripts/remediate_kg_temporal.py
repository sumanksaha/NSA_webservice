"""CLI: P3 temporal-status remediation of the Neo4j legal KG (2026-08-11).

Fixes the legal-risk defect where provisions default to ``status = 'current'``
even when their parent instrument is ``draft`` / ``repealed`` / ``superseded``
(regression source: ``kg/corpus_ingestion.py::write_provisions`` hardcoded
``p.status = 'current'`` — now fixed at the root for future rebuilds; this
script repairs the already-built graph).

Two idempotent operations:

1. **Status propagation** — every provision under a non-current instrument
   inherits the instrument's status (draft/repealed/superseded).  The risk
   query ``provisions status='current' under non-current instrument`` MUST be
   0 after this runs.
2. **effective_from backfill** — provisions under instruments that
   participate in supersession edges (``AMENDS`` / ``REPEALS`` /
   ``REPLACES`` / ``SUPERSEDED_BY``, either direction) or that are
   non-current get ``effective_from = instrument.effective_date`` when they
   have none — these are the highest-risk provisions for serving stale law.
   When an affected instrument is missing its ``effective_date`` and a
   repo-known canonical value exists (e.g. the FSS Act, 2006 — the DB row
   carries a null ``effective_date`` while ``kg/corpus_ingestion.py``
   ``PILOT_INSTRUMENTS`` records 2006-09-01), the instrument node is first
   corrected so the inheritance can happen.

Usage::

    python scripts/remediate_kg_temporal.py --dry-run   # report only
    python scripts/remediate_kg_temporal.py              # apply + verify

Exit codes: 0 ok, 1 failure, 2 verification failed (risk query > 0).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from neo4j import GraphDatabase, basic_auth

#: Labels that own legal instruments in this KG.
_INSTRUMENT_LABELS = "Act|Rule|Regulation|Notification|Circular|Order|Guideline|Judgment"
#: Supersession relationship types (either direction counts).
_SUPERSESSION_RELS = "AMENDS|REPEALS|REPLACES|SUPERSEDED_BY"

#: Repo-known canonical effective dates for instruments whose source row
#: (manifest / LegalDocument) leaves ``effective_date`` null.  Values are
#: taken from ``kg/corpus_ingestion.py`` ``PILOT_INSTRUMENTS``/``STUB_INSTRUMENTS``
#: — never invented here.  Applied only when the node's ``effective_date`` is
#: null AND the instrument participates in a supersession edge (so provisions
#: of stale-law-risk instruments gain an inherit-able date).
FIXED_INSTRUMENT_EFFECTIVE_DATES: dict[str, str] = {
    "FSS_ACT_2006": "2006-09-01",
}

_FIX_INSTRUMENT_EFFECTIVE_CYPHER = f"""
UNWIND $fixes AS f
MATCH (i:{_INSTRUMENT_LABELS} {{instrument_id: f.instrument_id}})
WHERE i.effective_date IS NULL
  AND (i)-[:{_SUPERSESSION_RELS}]->()
SET i.effective_date = f.effective_date
RETURN count(*) AS n
"""

_PROPAGATE_CYPHER = f"""
MATCH (i:{_INSTRUMENT_LABELS})-[:CONTAINS]->(p:LegalProvision)
WHERE i.status <> 'current' AND coalesce(p.status, 'current') = 'current'
SET p.status = i.status
RETURN count(*) AS n
"""

_BACKFILL_CYPHER = f"""
MATCH (i:{_INSTRUMENT_LABELS})
WHERE i.status <> 'current'
   OR (i)-[:{_SUPERSESSION_RELS}]->()
   OR ()-[:{_SUPERSESSION_RELS}]->(i)
WITH DISTINCT i
MATCH (i)-[:CONTAINS]->(p:LegalProvision)
WHERE p.effective_from IS NULL AND i.effective_date IS NOT NULL
SET p.effective_from = i.effective_date
RETURN count(*) AS n
"""

_RISK_CYPHER = f"""
MATCH (i:{_INSTRUMENT_LABELS})-[:CONTAINS]->(p:LegalProvision)
WHERE i.status <> 'current' AND coalesce(p.status, 'current') = 'current'
RETURN count(*) AS n
"""


def _session(drv, database: str):
    return drv.session(database=database)


def _one(cypher: str, drv, database: str, **params) -> int:
    with _session(drv, database) as s:
        rec = s.run(cypher, **params).single()
        return int(rec["n"]) if rec else 0


def remediate(drv, database: str, dry_run: bool = False) -> dict:
    """Apply (or preview) the two P3 operations.

    Returns ``{dry_run, risk_before, propagated, instrument_dates_fixed,
    effective_backfilled, risk_after}``.  ``dry_run`` performs NO writes — it
    returns the counts the real run would produce by running the same Cypher
    inside a transaction that is rolled back.
    """
    result: dict = {"dry_run": dry_run}
    result["risk_before"] = _one(_RISK_CYPHER, drv, database)
    fixes = [{"instrument_id": iid, "effective_date": date} for iid, date in FIXED_INSTRUMENT_EFFECTIVE_DATES.items()]
    if dry_run:
        # Preview inside a rolled-back transaction so the live graph is never
        # touched.  Each statement runs ONCE; counts are read from that run.
        with _session(drv, database) as s:
            tx = s.begin_transaction()
            try:
                result["instrument_dates_fixed"] = int(
                    tx.run(_FIX_INSTRUMENT_EFFECTIVE_CYPHER, fixes=fixes).single()["n"]
                )
                result["propagated"] = int(tx.run(_PROPAGATE_CYPHER).single()["n"])
                result["effective_backfilled"] = int(tx.run(_BACKFILL_CYPHER).single()["n"])
                result["risk_after"] = int(tx.run(_RISK_CYPHER).single()["n"])
            finally:
                tx.rollback()
    else:
        result["instrument_dates_fixed"] = _one(_FIX_INSTRUMENT_EFFECTIVE_CYPHER, drv, database, fixes=fixes)
        result["propagated"] = _one(_PROPAGATE_CYPHER, drv, database)
        result["effective_backfilled"] = _one(_BACKFILL_CYPHER, drv, database)
        result["risk_after"] = _one(_RISK_CYPHER, drv, database)
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="P3 temporal remediation of the legal KG (status propagation + effective_from)."
    )
    p.add_argument("--dry-run", action="store_true", help="Preview counts in a rolled-back transaction — no writes.")
    p.add_argument("--out-dir", type=Path, default=Path("reports"), help="Where to write the summary JSON.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    drv = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=basic_auth(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    try:
        summary = remediate(drv, database, dry_run=args.dry_run)
    except Exception:
        return 1
    finally:
        drv.close()

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        name = "kg_temporal_dryrun.json" if args.dry_run else "kg_temporal_remediation.json"
        (args.out_dir / name).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError:
        pass

    if summary["risk_after"] != 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# End of remediate_kg_temporal.py
