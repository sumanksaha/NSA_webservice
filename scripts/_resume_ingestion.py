"""Resume driver: ingest a specific list of corpus files inside an app context.

``ingest_corpus_dir`` scans a whole directory; this driver ingests ONLY the
files named on the command line (the ones missing from the production Qdrant
collection after an OOM-killed batch run), printing per-file JSON results so
a failed file is visible and the run is resumable.

Run:  python scripts/_resume_ingestion.py <file1> [<file2> ...]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python scripts/_resume_ingestion.py <file1> [<file2> ...]", file=sys.stderr)
        return 2

    from app import create_app
    from app.rag.ingestion import make_ingestion_pipeline

    app = create_app()
    with app.app_context():
        pipeline = make_ingestion_pipeline(full_enrichment=None)
        results = []
        for raw in args:
            path = Path(raw)
            try:
                res = pipeline.ingest_file(path, document={"document_uri": str(path)})
                summary = res.to_dict()
                print(json.dumps(summary, default=str), flush=True)
                results.append(summary)
            except Exception as exc:  # noqa: BLE001 - one bad file must not abort the resume
                print(json.dumps({"source_uri": str(path), "errors": [str(exc)], "ok": False}, default=str), flush=True)
                results.append({"ok": False, "errors": [str(exc)]})

    ok = all(r.get("ok") for r in results)
    print(json.dumps({"resumed": len(results), "ok": ok}, default=str), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
