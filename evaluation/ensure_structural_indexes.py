"""Phase 1 — ensure the payload indexes a structural retrieval arm needs.

Phase 0 (``evaluation/diagnose_retrieval_ceiling.py``) audited the live
cluster and found ``section_number`` already indexed on every collection, but
``act_name`` and ``document_title`` — the two fields
:mod:`app.rag.retrieval.section_scope` scopes an Act by, and the same two
``evaluation/resolution.py:payload_to_keys`` resolves a chunk's family from —
are **not** indexed anywhere. Without them a section/Act filter would 400, and
the failure mode is silent: ``DenseRetriever.search`` swallows the exception
and returns an empty arm (``app/rag/retrieval/dense_retriever.py:273-283``).

Indexes are metadata only. Creating one does not change what an *unfiltered*
query returns, so this is safe to run on a live collection, and it is
idempotent: an already-existing index is a no-op.

This script only ever creates indexes. It never deletes or recreates a
collection, never re-embeds, and never calls an LLM.

Usage:
    python -m evaluation.ensure_structural_indexes            # audit + create
    python -m evaluation.ensure_structural_indexes --dry-run  # audit only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval.indexes")

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "structural_index_report.json"

#: Fields the structural arm filters on. ``section_number`` was already
#: indexed everywhere (Phase 0); the rest are the Act-scoping pair plus the
#: fields ``DEFAULT_PAYLOAD_INDEX_FIELDS`` already declares.
REQUIRED_FIELDS: tuple[str, ...] = (
    "section_number",
    "act_name",
    "document_title",
)


def audit_and_create(collections: list[str], dry_run: bool) -> dict:
    """Ensure every required field is indexed on every collection.

    Returns a per-collection record of what was already present, what was
    created, and the functional result of a filtered scroll.
    """
    from app.rag.qdrant_client import QdrantStore

    report: dict[str, dict] = {}
    for coll in collections:
        entry: dict = {"collection": coll, "created": [], "already_indexed": [], "failed": [], "verify": {}}
        store = QdrantStore(collection_name=coll)

        declared: set[str] = set()
        try:
            from qdrant_client import QdrantClient

            url = os.environ.get("RAG_QDRANT_URL", "")
            client = QdrantClient(url=url, api_key=os.environ.get("RAG_QDRANT_API_KEY") or None)
            info = client.get_collection(coll)
            declared = {str(k) for k in (getattr(info, "payload_schema", None) or {})}
        except Exception as exc:
            entry["audit_error"] = str(exc)[:300]

        for field in REQUIRED_FIELDS:
            if field in declared:
                entry["already_indexed"].append(field)
                continue
            if dry_run:
                entry.setdefault("would_create", []).append(field)
                continue
            if store.create_payload_index(field):
                entry["created"].append(field)
                logger.info("%s: created payload index %r", coll, field)
            else:
                entry["failed"].append(field)
                logger.warning("%s: could not create payload index %r", coll, field)

        if not dry_run:
            # Functional verification: a section filter must return points,
            # not HTTP 400. Existence is the property under test.
            entry["verify"] = _probe(coll)
        report[coll] = entry
    return report


def _probe(coll: str) -> dict:
    from qdrant_client import QdrantClient

    url = os.environ.get("RAG_QDRANT_URL", "")
    if not url:
        return {"ok": False, "error": "RAG_QDRANT_URL not configured"}
    client = QdrantClient(url=url, api_key=os.environ.get("RAG_QDRANT_API_KEY") or None)
    for section in ("1", "2", "8", "16"):
        try:
            points, _ = client.scroll(
                collection_name=coll,
                scroll_filter={"must": [{"key": "section_number", "match": {"value": section}}]},
                limit=1,
                with_payload=False,
                with_vectors=False,
            )
            client.close()
            return {"ok": True, "probe_section": section, "hit": bool(points)}
        except Exception as exc:
            last = str(exc)[:300]
            continue
    client.close()
    return {"ok": False, "error": last}


def main() -> int:
    ap = argparse.ArgumentParser(description="Ensure payload indexes for the structural retrieval arm")
    ap.add_argument("--dry-run", action="store_true", help="audit only, create nothing")
    args = ap.parse_args()

    from app import create_app
    from app.rag.collections import DOMAIN_COLLECTIONS
    from app.shared.config import cfg

    app = create_app()
    with app.app_context():
        collections = sorted({str(c) for c in DOMAIN_COLLECTIONS.values()} | {str(cfg.qdrant_collection)})
        logger.info("%s indexes %s on %s", "auditing" if args.dry_run else "ensuring", list(REQUIRED_FIELDS), collections)
        report = audit_and_create(collections, dry_run=args.dry_run)

        for coll, entry in report.items():
            if entry.get("audit_error"):
                logger.warning("%s: audit error: %s", coll, entry["audit_error"])
            logger.info(
                "%s: already=%s created=%s failed=%s verify=%s",
                coll,
                entry.get("already_indexed"),
                entry.get("created"),
                entry.get("failed"),
                entry.get("verify", {}).get("ok"),
            )

        out = {
            "dry_run": args.dry_run,
            "required_fields": list(REQUIRED_FIELDS),
            "collections": report,
            "all_verified": all(e.get("verify", {}).get("ok") for e in report.values()),
        }
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("wrote %s", OUT_FILE)

        failed = [(c, f) for c, e in report.items() for f in e.get("failed", [])]
        if failed:
            logger.error("index creation failed for: %s", failed)
            return 1
        if not args.dry_run and not out["all_verified"]:
            logger.error("functional verification failed on at least one collection")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
