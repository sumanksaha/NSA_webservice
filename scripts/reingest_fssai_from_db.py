"""P1-4 remediation: rebuild ``fssai_legal_768`` from the local DB (identity-preserving).

Reads the 29 FSSAI ``LegalDocument`` rows and 12,819 ``LegalChunk`` rows from the
local DB (``instance/app.db``) and rebuilds the Qdrant ``fssai_legal_768``
collection with **preserved chunk identity** (``chunk_id = LegalChunk.id``) and
full §5.1 metadata (cached ``metadata_json`` + authoritative row fields +
``act_name``).  The Neo4j KG already mirrors these DB chunk ids 1:1, so this
rebuild makes the graph↔payload join resolvable from the vector side — closing
P1-4 (see ``docs/FSSAI_REINGEST_PLAN.md`` and ``CORPUS_IDENTITY_REPORT.md``).

Why NOT a PDF re-ingest: re-chunking the PDFs would mint fresh UUIDs and break
``Chunk.chunk_id`` / ``qdrant_point_id`` = ``LegalChunk.id``, recreating the
identity gap this remediation exists to close.

Usage::

    python scripts/reingest_fssai_from_db.py --dry-run            # plan only, NO Qdrant writes
    python scripts/reingest_fssai_from_db.py --delete-collection  # delete + recreate + rebuild
    python scripts/reingest_fssai_from_db.py                      # append/complete only (no delete)
    python scripts/reingest_fssai_from_db.py --only <doc-id>      # one document (resume)

Exit codes: 0 all ok (or all duplicates), 1 any document failed, 2 usage/error.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

FSS_ACT_NAME = "Food Safety and Standards Act, 2006"
COLLECTION = "fssai_legal_768"

#: Expected corpus size at remediation time (audited 2026-08-11). Printed as a
#: warning on deviation; NOT a hard gate so legitimate corpus growth still runs.
EXPECTED_DOCS = 29
EXPECTED_CHUNKS = 12_819

#: Rollback artifact produced by plan STEP 1 (§3.1) — required before delete.
BACKUP_PATH = Path("reports") / "fssai_legal_768_pre_reingest_backup.json"

#: FSS-family markers matched against ``source_uri`` (titles are empty on this
#: corpus). Guards against a non-FSS document being silently stamped with the
#: FSS Act name and entering ``fssai_legal_768``.
_FSS_MARKERS = ("fssai", "food", "fss_act", "standards act")


def _is_fss_document(doc: dict[str, Any]) -> bool:
    """Whether a document is recognisably part of the FSS Act family.

    Mirrors ``app/rag/enrichment/deterministic.py::_looks_like_fss_document``
    (title + document_id + URI markers); on this corpus titles are empty so the
    URI is the decisive field.
    """
    haystack = " ".join(str(doc.get(key) or "") for key in ("title", "source_uri")).lower()
    return any(marker in haystack for marker in _FSS_MARKERS)


def _app_context():
    """Minimal Flask app context carrying the RAG config for Qdrant/embedding."""
    from flask import Flask

    app = Flask(__name__)
    app.config["RAG_QDRANT_URL"] = os.environ.get("RAG_QDRANT_URL", "")
    app.config["RAG_QDRANT_API_KEY"] = os.environ.get("RAG_QDRANT_API_KEY", "")
    app.config["RAG_QDRANT_COLLECTION"] = COLLECTION
    app.config["RAG_VECTOR_SIZE"] = int(os.environ.get("RAG_VECTOR_SIZE", "768"))
    app.config["RAG_EMBEDDING_MODEL"] = os.environ.get("RAG_EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")
    app.config["RAG_FULL_ENRICHMENT"] = True
    app.config["RAG_ENABLE_SPARSE"] = os.environ.get("RAG_ENABLE_SPARSE", "true").lower() == "true"
    for key, val in os.environ.items():
        if key.startswith("RAG_QDRANT_COLLECTION_"):
            app.config[key] = val
    return app.app_context()


def load_corpus(db_path: str | Path | None = None) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Read LegalDocument rows + LegalChunk rows (STRICT read-only SQLite).

    Args:
        db_path: Optional explicit database path (tests). ``None`` resolves the
            production default ``instance/app.db`` (falling back to the sole
            ``instance/*.db`` if absent).
    """
    import sqlite3

    if db_path is None:
        db_path = Path("instance") / "app.db"
        if not Path(db_path).exists():
            candidates = sorted(Path("instance").glob("*.db"))
            if not candidates:
                raise RuntimeError("cannot locate instance/app.db")
            db_path = candidates[0]
    db_path = Path(db_path)
    if not db_path.exists():
        raise RuntimeError(f"database not found: {db_path}")
    con = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    docs: list[dict[str, Any]] = []
    chunks: dict[str, list[dict[str, Any]]] = {}
    try:
        for r in con.execute(
            """
            SELECT id, source_uri, title, document_type, authority, jurisdiction,
                   effective_date, enactment_date, amended_date, is_current,
                   qdrant_collection, chunk_count, created_at
            FROM legal_document ORDER BY created_at
            """
        ):
            docs.append({k: r[k] for k in r})
        for r in con.execute(
            """
            SELECT id, document_id, document_type, section_number, chunk_index,
                   text, char_count, word_count, hierarchy_level, parent_id,
                   citations, "references", entities, metadata_json, content_hash,
                   qdrant_point_id, created_at
            FROM legal_chunk ORDER BY document_id, chunk_index
            """
        ):
            chunks.setdefault(r["document_id"], []).append({k: r[k] for k in r})
    finally:
        con.close()
    return docs, chunks


def build_payload(chunk: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """Full §5.1 payload for one chunk — identity-preserving.

    Starts from the cached ``metadata_json`` (complete §5.1) then overrides the
    authoritative keys from the row so ``chunk_id``/text/hash always match the
    DB (the identity Neo4j mirrors).  ``act_name`` is the FSS Act for the whole
    FSS family (matches ``app/rag/enrichment/deterministic.py::legal_act_of``).
    """
    import json as _json

    try:
        payload = dict(_json.loads(chunk.get("metadata_json") or "{}"))
    except Exception:
        payload = {}
    payload.update({
        "chunk_id": str(chunk["id"]),
        "document_id": str(chunk["document_id"]),
        "document_uri": str(doc.get("source_uri") or ""),
        "document_title": str(doc.get("title") or ""),
        "document_type": str(chunk.get("document_type") or doc.get("document_type") or "unknown"),
        "chunk_index": int(chunk.get("chunk_index") or 0),
        "chunk_text": str(chunk.get("text") or ""),
        "chunk_char_count": int(chunk.get("char_count") or 0),
        "word_count": int(chunk.get("word_count") or 0),
        "section_number": chunk.get("section_number") or None,
        "content_hash": str(chunk.get("content_hash") or ""),
        "created_at": str(chunk.get("created_at") or ""),
        "act_name": FSS_ACT_NAME,
    })
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild fssai_legal_768 from the local DB (identity-preserving, P1-4)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Build + validate payloads only; NO Qdrant writes.")
    parser.add_argument(
        "--delete-collection",
        action="store_true",
        help="Delete + recreate fssai_legal_768 before rebuilding (required for the full replace).",
    )
    parser.add_argument("--only", help="Restrict to one document id (resume after a partial failure).")
    args = parser.parse_args(argv)

    docs, chunks = load_corpus()
    total_chunks = sum(len(v) for v in chunks.values())

    # Scope guard: every document must be recognisably FSS-family, otherwise
    # ``act_name`` would be wrongly stamped and a foreign doc would enter the
    # FSSAI collection. Hard fail — never silently proceed.
    foreign = [d for d in docs if not _is_fss_document(d)]
    if foreign:
        return 2

    # Corpus-size expectation (audited baseline). Warn only — legitimate growth
    # (a new FSSAI doc ingested through the pipeline) is a valid re-run.
    if not args.only and (len(docs) != EXPECTED_DOCS or total_chunks != EXPECTED_CHUNKS):
        pass

    if args.only:
        docs = [d for d in docs if d["id"] == args.only or args.only in str(d["source_uri"])]
        if not docs:
            return 2

    # Build all payloads first (cheap) so --dry-run is a faithful pre-flight.
    by_doc: dict[str, list[dict[str, Any]]] = {}
    total_payloads = 0
    for doc in docs:
        doc_id = doc["id"]
        doc_chunks = chunks.get(doc_id, [])
        payloads = [build_payload(c, doc) for c in doc_chunks]
        by_doc[doc_id] = payloads
        total_payloads += len(payloads)

    if args.dry_run:
        return 0

    # Destructive guard: --delete-collection requires the STEP-1 rollback export
    # to exist, so the pre-rebuild collection is always recoverable (§7 rollback).
    if args.delete_collection and not BACKUP_PATH.exists():
        return 2

    ctx = _app_context()
    ctx.push()
    try:
        from qdrant_client import QdrantClient

        from app.rag.qdrant_indexer import QdrantIndexer

        client = QdrantClient(
            url=os.environ.get("RAG_QDRANT_URL", ""),
            api_key=os.environ.get("RAG_QDRANT_API_KEY") or None,
        )
        if args.delete_collection:
            if client.collection_exists(COLLECTION):
                client.delete_collection(COLLECTION)
            else:
                pass

        indexer = QdrantIndexer(collection_name=COLLECTION)
        if not indexer.ensure_collection():
            return 1

        any_failed = False
        time.monotonic()
        for doc in docs:
            doc_id = doc["id"]
            payloads = by_doc[doc_id]
            if not payloads:
                continue
            time.monotonic()
            try:
                result = indexer.sync_payloads(payloads)
            except Exception:
                any_failed = True
                continue
            if not result.ok:
                any_failed = True
        return 1 if any_failed else 0
    finally:
        ctx.pop()


if __name__ == "__main__":
    raise SystemExit(main())
