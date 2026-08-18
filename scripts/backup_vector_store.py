"""CLI: back up the Qdrant RAG vector store to a local JSON archive.

Exports every point — chunk payloads PLUS dense and BM25 sparse vectors —
from the configured Qdrant collection (``RAG_QDRANT_COLLECTION``) into a
portable, integrity-checked JSON archive via :func:`app.rag.backup.backup_collection`.

Usage::

    python scripts/backup_vector_store.py                      # backups/vector_store_<collection>_<date>.json
    python scripts/backup_vector_store.py --output backup.json
    python scripts/backup_vector_store.py --collection custom_coll
    python scripts/backup_vector_store.py --pretty

Restore with ``python scripts/restore_vector_store.py --archive <file>``.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure the project root is on sys.path so that "from app" imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.backup import backup_collection
from app.rag.qdrant_client import QdrantStore


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Export the Qdrant RAG vector store to a local JSON archive.",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Qdrant collection to back up (default: RAG_QDRANT_COLLECTION).",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        default=None,
        help="Output archive path (default: backups/vector_store_<collection>_<date>.json).",
    )
    parser.add_argument(
        "--batch-size",
        dest="batch_size",
        type=int,
        default=1000,
        help="Scroll page size (default 1000).",
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

    from app import create_app

    app = create_app()

    output_path = args.output_path
    if not output_path:
        collection = args.collection or app.config.get("RAG_QDRANT_COLLECTION", "fssai_legal_768")
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_path = str(Path("backups") / f"vector_store_{collection}_{stamp}.json")

    try:
        with app.app_context():
            store = QdrantStore(collection_name=args.collection)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            backup_collection(store, output_path, batch_size=args.batch_size)
    except Exception:
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
