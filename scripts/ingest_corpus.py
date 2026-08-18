"""CLI tool for manual RAG corpus ingestion (Agent A, Phase 3 — Day 12).

Ingests legal documents into the Qdrant vector store through the production
:func:`app.rag.ingestion.make_ingestion_pipeline` (Day 9 ``DocumentClassifier``
always wired; full Phase 2 enrichment when ``RAG_FULL_ENRICHMENT`` is set or
``--full-enrichment`` is passed).

Usage::

    python scripts/ingest_corpus.py <corpus_dir>            # every pdf/docx/txt
    python scripts/ingest_corpus.py --file <path>           # one file
    python scripts/ingest_corpus.py --text "<raw text>"     # raw text
    python scripts/ingest_corpus.py <corpus_dir> --full-enrichment

Prints a JSON summary to stdout and exits 0 on success (or when everything
was a duplicate), 1 when any document failed to ingest.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so that "from app" imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.ingestion import (
    ingest_corpus_dir,
    make_ingestion_pipeline,
    run_ingest_document,
)


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Ingest legal documents into the Qdrant RAG corpus.",
    )
    parser.add_argument(
        "corpus_dir",
        nargs="?",
        help="Directory of PDF/DOCX/TXT files to ingest (non-recursive).",
    )
    parser.add_argument(
        "--file",
        dest="file_path",
        help="Ingest a single supported corpus file.",
    )
    parser.add_argument(
        "--text",
        help="Ingest raw legal text.",
    )
    parser.add_argument(
        "--full-enrichment",
        action="store_true",
        help="Force the full Phase 2 enrichment chain (overrides RAG_FULL_ENRICHMENT).",
    )
    parser.add_argument(
        "--pretty",
        dest="pretty_json",
        action="store_true",
        help="Pretty-print the JSON summary (default compact).",
    )
    return parser


def ingest(args: argparse.Namespace) -> dict:
    """Run the ingestion for the parsed CLI args; returns the summary dict."""
    if not args.file_path and not args.text and not args.corpus_dir:
        raise ValueError("Provide a corpus_dir, --file, or --text.")

    full_enrichment = True if args.full_enrichment else None
    pipeline = make_ingestion_pipeline(full_enrichment=full_enrichment)

    if args.file_path:
        return run_ingest_document(args.file_path, pipeline=pipeline)
    if args.text:
        return run_ingest_document(args.text, pipeline=pipeline)
    return ingest_corpus_dir(args.corpus_dir, pipeline=pipeline)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code.

    Environment bootstrap (dotenv + startup-sync skip) happens here rather
    than at module import so importing the script in tests never loads a
    developer's ``.env`` into the pytest process.
    """
    # Load .env before anything else
    from dotenv import load_dotenv

    load_dotenv()

    # Skip FSO sync and other startup noise during script execution
    os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        summary = ingest(args)
    except (ValueError, FileNotFoundError, RuntimeError):
        return 2
    except Exception:
        return 2

    # Single-document results expose ``ok``; corpus summaries expose ``failed``.
    if summary.get("ok") is False or summary.get("failed", 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
