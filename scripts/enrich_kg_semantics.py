"""CLI: corpus-scale semantic enrichment of the Neo4j legal KG.

Tags every provision with typed legal semantics using deterministic,
rule-based pattern matching (no LLM, no external calls) and writes
evidence-backed edges to the typed ``LegalConcept`` vocabulary:

- ``IMPOSES_DUTY``        -> Obligation / Duty
- ``PROHIBITS``           -> Prohibition
- ``CREATES_OFFENCE``     -> Offence
- ``PRESCRIBES_PENALTY``  -> Penalty
- ``GRANTS_POWER_TO``     -> Power
- ``GRANTS_PERMISSION``   -> Permission
- ``PRESCRIBES``          -> Procedure

Idempotent (``MERGE``) — re-runs refresh evidence without duplicating edges.

Usage::

    python scripts/enrich_kg_semantics.py --dry-run            # report only
    python scripts/enrich_kg_semantics.py --domain FOOD_SAFETY
    python scripts/enrich_kg_semantics.py --limit 500
    python scripts/enrich_kg_semantics.py --min-confidence 0.8

Exit codes: 0 ok, 1 failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Semantic enrichment of the legal KG (duty/offence/penalty/prohibition tags).")
    p.add_argument("--dry-run", action="store_true", help="Tag provisions and report planned edges — no Neo4j writes.")
    p.add_argument("--limit", type=int, default=None, help="Only tag the first N provisions (ORDER BY provision_id).")
    p.add_argument("--domain", type=str, default=None, help="Only tag provisions in this domain (e.g. FOOD_SAFETY).")
    p.add_argument("--min-confidence", type=float, default=0.7, help="Minimum rule confidence for an edge (default 0.7).")
    p.add_argument("--out-dir", type=Path, default=Path("reports"), help="Where to write the summary JSON.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print the summary.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from kg.enrichment import LegalSemanticEnricher

    enricher = LegalSemanticEnricher(min_confidence=args.min_confidence)
    try:
        summary = enricher.enrich(limit=args.limit, domain=args.domain, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - CLI should never traceback
        print(f"error: enrichment failed: {exc}", file=sys.stderr)
        return 1

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        name = "kg_enrichment_dryrun.json" if args.dry_run else "kg_enrichment_summary.json"
        (args.out_dir / name).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"summary -> {args.out_dir / name}")
    except OSError as exc:
        print(f"warning: could not write summary: {exc}", file=sys.stderr)

    print(json.dumps(summary, indent=2 if args.pretty else None, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
