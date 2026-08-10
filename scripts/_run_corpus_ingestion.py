"""One-off driver: run the production corpus ingestion inside an app context.

``scripts/ingest_corpus.py`` does not open an app context, so QdrantStore
cannot resolve RAG_QDRANT_URL there (outside ``current_app`` the URL reads as
empty and the store refuses to connect).  This driver wraps the production
:func:`app.rag.ingestion.ingest_corpus_dir` in ``create_app().app_context()``
so the real pipeline can reach Qdrant Cloud, and prints the JSON summary.

Run:  python scripts/_run_corpus_ingestion.py [corpus_dir] [--full-enrichment]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

    parser = argparse.ArgumentParser(description="Ingest the corpus inside an app context.")
    parser.add_argument("corpus_dir", nargs="?", default="FSSAI_rules documents")
    parser.add_argument("--full-enrichment", action="store_true")
    args = parser.parse_args(argv)

    from app import create_app
    from app.rag.ingestion import ingest_corpus_dir, make_ingestion_pipeline

    app = create_app()
    with app.app_context():
        pipeline = make_ingestion_pipeline(full_enrichment=True if args.full_enrichment else None)
        summary = ingest_corpus_dir(args.corpus_dir, pipeline=pipeline)

    print(json.dumps(summary, indent=2, default=str))
    return 1 if summary.get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
