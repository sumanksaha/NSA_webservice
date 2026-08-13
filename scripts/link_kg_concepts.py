"""CLI: P2 concept linking of the Neo4j legal KG (2026-08-11).

Grounds every *isolated* ``LegalConcept`` (0 inbound edges) to the provisions
that textually mention it (deterministic synonym matching over
``provision_text``, domain-scoped) via evidence-backed ``APPLIES_TO`` edges.
Concepts with no textual grounding anywhere in their domains are reported as
``PREMATURE_TAXONOMY`` rather than silently left at zero.

Usage::

    python scripts/link_kg_concepts.py --dry-run   # plan only, no writes
    python scripts/link_kg_concepts.py              # apply + verify

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
    p = argparse.ArgumentParser(description="Concept linking: APPLIES_TO edges from provisions to isolated LegalConcept nodes.")
    p.add_argument("--dry-run", action="store_true", help="Plan links and report — no Neo4j writes.")
    p.add_argument("--out-dir", type=Path, default=Path("reports"), help="Where to write the summary JSON.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print the summary.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from kg.concept_linking import ConceptLinker

    linker = ConceptLinker()
    try:
        summary = linker.link(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - CLI should never traceback
        print(f"error: concept linking failed: {exc}", file=sys.stderr)
        return 1

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        name = "kg_concept_links_dryrun.json" if args.dry_run else "kg_concept_links.json"
        (args.out_dir / name).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"summary -> {args.out_dir / name}")
    except OSError as exc:
        print(f"warning: could not write summary: {exc}", file=sys.stderr)

    print(json.dumps(summary, indent=2 if args.pretty else None, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# End of link_kg_concepts.py
