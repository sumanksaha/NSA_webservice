"""CLI: P4 provision-type classification backfill of the Neo4j legal KG (2026-08-11).

Backfills ``LegalProvision.provision_type`` from the ``provision_number``
shape — a purely mechanical mapping (no semantics):

    ``6(2)(a)``  -> Clause
    ``6(2)``     -> Subsection
    ``6``        -> Section
    ``Schedule`` -> Schedule (name contains 'schedule')

It also wires the previously-unused ``Section`` node label from
``kg/schema.py`` onto every provision node (the label is declared in the
schema with a ``provision_id`` uniqueness constraint but was never applied).

**Hierarchy note (evidence-cited):** this corpus has NO subsection/clause
granularity — all 1,861 ``provision_number`` values are digits-only, and a
read-only probe of the source FSS DB found 0/3,158 ``legal_chunk`` rows with
sub-clause shapes (parentheses / trailing letters).  The chunker collapses
sub-clause structure into the section's text, so ``Section CONTAINS Subsection
CONTAINS Clause`` parent-child edges cannot be derived from the current data;
building them is a chunking-granularity change (out of scope for this
remediation).  The script reports the shape distribution as evidence.

Usage::

    python scripts/backfill_kg_provision_types.py --dry-run   # report only
    python scripts/backfill_kg_provision_types.py              # apply + verify

Exit codes: 0 ok, 1 failure, 2 verification failed (coverage < 100%).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from neo4j import GraphDatabase, basic_auth  # noqa: E402

_LOAD_CYPHER = "MATCH (p:LegalProvision) RETURN p.provision_id AS provision_id, coalesce(p.provision_number,'') AS provision_number"

_WRITE_CYPHER = """
UNWIND $rows AS r
MATCH (p:LegalProvision {provision_id: r.provision_id})
SET p.provision_type = r.provision_type, p:Section
RETURN count(*) AS n
"""

_VERIFY_CYPHER = """
MATCH (p:LegalProvision)
RETURN count(p) AS total,
       sum(CASE WHEN coalesce(p.provision_type,'') <> '' THEN 1 ELSE 0 END) AS typed,
       sum(CASE WHEN coalesce(p.provision_type,'') = 'Section' THEN 1 ELSE 0 END) AS sections
"""

_LABEL_COUNT_CYPHER = "MATCH (s:Section) RETURN count(*) AS n"


def classify_provision_type(number: str) -> str:
    """Mechanical provision_type from a provision_number shape."""
    n = str(number or "").strip().lower()
    if not n:
        return "Section"
    if re.fullmatch(r"\d+\(\d+\)\(\w+\)", n):
        return "Clause"  # 6(2)(a)
    if re.fullmatch(r"\d+\(\d+\)", n):
        return "Subsection"  # 6(2)
    if "schedule" in n:
        return "Schedule"
    if re.fullmatch(r"\d+", n):
        return "Section"
    return "Section"  # anything else is top-level in this corpus


def shape_distribution(rows: list[dict]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for r in rows:
        n = str(r.get("provision_number") or "")
        kind = "digits-only" if re.fullmatch(r"\d+", n) else "other"
        dist[kind] = dist.get(kind, 0) + 1
    return dist


def backfill(drv, database: str, dry_run: bool = False) -> dict:
    """Classify all provisions and (optionally) write type + Section label."""
    with drv.session(database=database) as s:
        recs = [dict(r) for r in s.run(_LOAD_CYPHER)]
    rows = [{"provision_id": r["provision_id"], "provision_type": classify_provision_type(r["provision_number"])} for r in recs]
    result: dict = {
        "dry_run": dry_run,
        "provisions_loaded": len(rows),
        "shape_distribution": shape_distribution(recs),
        "type_breakdown": _type_breakdown(rows),
        "planned_updates": len(rows),
    }
    if dry_run:
        result["updates_applied"] = 0
    else:
        written = 0
        with drv.session(database=database) as s:
            for i in range(0, len(rows), 500):
                batch = rows[i : i + 500]
                written += int(s.run(_WRITE_CYPHER, rows=batch).single()["n"])
        result["updates_applied"] = written
    # Verification (same read path for dry-run and live)
    with drv.session(database=database) as s:
        v = s.run(_VERIFY_CYPHER).single()
        labels = s.run(_LABEL_COUNT_CYPHER).single()["n"]
    result["verify"] = {
        "total": v["total"],
        "with_provision_type": v["typed"],
        "type_section": v["sections"],
        "coverage_pct": round(100 * v["typed"] / max(v["total"], 1), 1),
        "section_label_nodes": labels,
    }
    return result


def _type_breakdown(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["provision_type"]] = out.get(r["provision_type"], 0) + 1
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="P4 provision-type backfill (mechanical provision_number -> type).")
    p.add_argument("--dry-run", action="store_true", help="Classify + report — no Neo4j writes.")
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
        summary = backfill(drv, database, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - CLI should never traceback
        print(f"error: backfill failed: {exc}", file=sys.stderr)
        return 1
    finally:
        drv.close()

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        name = "kg_provision_types_dryrun.json" if args.dry_run else "kg_provision_types_backfill.json"
        (args.out_dir / name).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"summary -> {args.out_dir / name}")
    except OSError as exc:
        print(f"warning: could not write summary: {exc}", file=sys.stderr)

    print(json.dumps(summary, indent=2))
    v = summary["verify"]
    if not args.dry_run and v["with_provision_type"] != v["total"]:
        print(f"error: verification failed — {v['with_provision_type']}/{v['total']} provisions typed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# End of backfill_kg_provision_types.py
