"""CLI: rebuild the Neo4j legal KG from the real multi-domain corpus (Option B).

Reads the corpus metadata authority (``other domain/manifest.json``), the
live Qdrant payloads (read-only), and the local FSSAI ``LegalChunk`` table,
then rebuilds the legal KG in Neo4j with domain edges on every provision,
full Document/Chunk provenance, corpus-truthful statuses, and supersession
edges.  The legacy case-file graph (``Case``/``FBO``/...) is never touched.

Usage::

    python scripts/build_kg_corpus.py --dry-run   # assemble + report, NO writes
    python scripts/build_kg_corpus.py --no-clear  # MERGE over existing legal KG
    NEO4J_ALLOW_WRITE=1 python scripts/build_kg_corpus.py   # full rebuild (clear + ingest)

NOTE: a full rebuild clears the legal KG first (``clear_legal_kg``), which is
protected by the fail-closed ``NEO4J_ALLOW_WRITE=1`` guard (added 2026-08-12
after test suites wiped the live KG twice).  Set it explicitly to rebuild.

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
    p = argparse.ArgumentParser(description="Rebuild the Neo4j legal KG from the real multi-domain corpus.")
    p.add_argument("--dry-run", action="store_true", help="Assemble everything and report counts — no Neo4j writes.")
    p.add_argument("--no-clear", action="store_true", help="Do not clear the legal KG first (MERGE over existing).")
    p.add_argument("--manifest", type=Path, default=Path("other domain/manifest.json"), help="Corpus manifest path.")
    p.add_argument("--out-dir", type=Path, default=Path("reports"), help="Where to write the summary JSON.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print the summary.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from kg.corpus_ingestion import KGCorpusIngestionEngine

    engine = KGCorpusIngestionEngine(manifest_path=args.manifest)
    try:
        summary = engine.run_rebuild(clear=not args.no_clear, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - CLI should never traceback
        print(f"error: rebuild failed: {exc}", file=sys.stderr)
        if "NEO4J_ALLOW_WRITE" in str(exc):
            print(
                "hint: a full rebuild clears the legal KG first. Re-run with "
                "NEO4J_ALLOW_WRITE=1 (e.g. NEO4J_ALLOW_WRITE=1 python scripts/build_kg_corpus.py)"
                " or pass --no-clear to MERGE over the existing graph.",
                file=sys.stderr,
            )
        return 1

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        name = "kg_rebuild_dryrun.json" if args.dry_run else "kg_rebuild_summary.json"
        (args.out_dir / name).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"summary -> {args.out_dir / name}")
    except OSError as exc:
        print(f"warning: could not write summary: {exc}", file=sys.stderr)

    print(json.dumps(summary, indent=2 if args.pretty else None, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
