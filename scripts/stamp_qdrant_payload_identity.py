"""CLI: stamp canonical legal identity onto Qdrant payloads (P1 remediation).

Adds ``provision_id`` / ``instrument_id`` / ``legal_domain`` / ``status`` to
every live Qdrant point, derived from the **same registry that builds the
Neo4j legal KG** (``other domain/manifest.json`` + FSS DB + ``legal_sections``)
so payload and graph agree by construction — closing the audit's last P1
gap (Qdrant was not provision-addressable on its own).

Idempotent + additive: only missing/different fields are written; re-runs
are safe. Unknown documents get ``legal_domain`` from the collection name
only (no guessed IDs).

Usage::

    python scripts/stamp_qdrant_payload_identity.py --dry-run        # plan only, NO writes
    python scripts/stamp_qdrant_payload_identity.py                  # stamp all collections
    python scripts/stamp_qdrant_payload_identity.py --collection env_legal_768
    python scripts/stamp_qdrant_payload_identity.py --limit 100      # first 100 points per collection
    python scripts/stamp_qdrant_payload_identity.py --no-indexes     # skip payload index creation

Exit codes: 0 ok, 1 failure.
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
os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stamp Qdrant payloads with canonical legal identity (provision_id/instrument_id/legal_domain/status).")
    p.add_argument("--dry-run", action="store_true", help="Compute planned updates and report — no Qdrant writes.")
    p.add_argument("--collection", type=str, default=None, help="Only stamp this collection (repeatable? use comma-separated).")
    p.add_argument("--limit", type=int, default=None, help="Only consider the first N points per collection (testing).")
    p.add_argument("--no-indexes", action="store_true", help="Do not create keyword payload indexes on the identity fields.")
    p.add_argument("--out-dir", type=Path, default=Path("reports"), help="Where to write the summary JSON.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print the summary.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from kg.payload_identity import QdrantPayloadStamper

    collections = [c.strip() for c in args.collection.split(",") if c.strip()] if args.collection else None

    stamper = QdrantPayloadStamper()
    try:
        if args.dry_run:
            summary = stamper.plan(collections=collections, limit=args.limit)
        else:
            summary = stamper.stamp(
                collections=collections,
                limit=args.limit,
                create_indexes=not args.no_indexes,
            )
    except Exception:
        return 1

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        name = "qdrant_payload_identity_dryrun.json" if args.dry_run else "qdrant_payload_identity.json"
        (args.out_dir / name).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
