"""CLI: restore the RAG vector store from a local JSON archive.

Recreates the target Qdrant collection with the archive's sparse layout and
upserts every point (chunk payloads + dense/BM25 sparse vectors) via
:func:`app.rag.backup.restore_collection`.

Usage::

    python scripts/restore_vector_store.py --archive backup.json
    python scripts/restore_vector_store.py --archive backup.json --collection fssai_legal_768
    python scripts/restore_vector_store.py --archive backup.json --drop-existing
    python scripts/restore_vector_store.py --archive backup.json --dry-run

``--drop-existing`` deletes the target collection first — REQUIRED when the
collection's sparse layout differs from the archive's (e.g. restoring a
BM25 archive over a dense-only index).  ``--dry-run`` validates the archive
without writing anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so that "from app" imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.backup import load_archive, restore_collection  # noqa: E402
from app.rag.qdrant_client import QdrantStore  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Restore the Qdrant RAG vector store from a JSON backup archive.",
    )
    parser.add_argument("--archive", required=True, help="Path to the backup archive JSON.")
    parser.add_argument(
        "--collection",
        default=None,
        help="Destination collection (default: the archive's collection name).",
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Delete the destination collection before restoring (required when the "
        "sparse layout changed).",
    )
    parser.add_argument(
        "--batch-size",
        dest="batch_size",
        type=int,
        default=100,
        help="Upsert batch size (default 100).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the archive and print the plan without writing to Qdrant.",
    )
    parser.add_argument(
        "--pretty",
        dest="pretty_json",
        action="store_true",
        help="Pretty-print the summary JSON (default compact).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (returns the process exit code)."""
    from dotenv import load_dotenv

    load_dotenv()
    os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        archive = load_archive(args.archive)
    except Exception as exc:  # noqa: BLE001 - CLI should never traceback
        print(f"error: cannot read archive: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        plan = {
            "archive": args.archive,
            "collection": args.collection or archive.get("collection"),
            "point_count": archive.get("point_count"),
            "has_sparse": archive.get("has_sparse"),
            "vector_size": archive.get("vector_size"),
            "dry_run": True,
            "integrity": "ok",
        }
        indent = 2 if args.pretty_json else None
        print(json.dumps(plan, indent=indent, default=str))
        return 0

    from app import create_app

    app = create_app()

    try:
        with app.app_context():
            store = QdrantStore(collection_name=args.collection or archive.get("collection"))
            summary = restore_collection(
                store,
                args.archive,
                drop_existing=args.drop_existing,
                batch_size=args.batch_size,
            )
    except Exception as exc:  # noqa: BLE001 - CLI should never traceback
        print(f"error: restore failed: {exc}", file=sys.stderr)
        return 2

    indent = 2 if args.pretty_json else None
    print(json.dumps(summary, indent=indent, default=str))
    # Non-zero when some points failed to restore.
    return 0 if summary["points_restored"] == summary["archive_point_count"] and not summary["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
