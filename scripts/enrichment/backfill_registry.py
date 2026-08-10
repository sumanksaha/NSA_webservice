#!/usr/bin/env python3
"""Backfill the empty ``legal_document`` / ``legal_chunk`` ORM registry.

The audit found the Qdrant corpus (12,819 points) is the canonical store
while ``legal_document``/``legal_chunk`` have 0 rows.  This script replays
each point's §5.1 payload into the registry so enrichment becomes
SQL-queryable (user-confirmed decision).

Memory contract: streams points one page/document at a time (Qdrant source)
or one document group at a time (backup source); commits per document.
Idempotent: re-running updates rows in place (``chunk_id`` / ``document_id``
are stable identity keys).

``file_hash`` on ``legal_document`` is the SHA-256 of ``document_uri`` (a
source-identity fingerprint for backfilled rows — NOT the raw-file hash the
ingestion pipeline stamps; the raw files are out of scope per guardrail 1).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Allow ``from audit_chunks import ...`` and ``from app import ...`` when
# run from anywhere (project root first, then this scripts dir).
_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR.parents[1]))  # project root
sys.path.insert(0, str(_SCRIPTS_DIR))

from audit_chunks import _resolve_source  # noqa: E402


def _doc_hash(uri: str, document_id: str) -> str:
    # Includes the document id so duplicate source URIs (re-ingested files
    # with fresh document ids) never collide on the UNIQUE file_hash.
    return hashlib.sha256(f"{document_id}:{uri}".encode("utf-8")).hexdigest()


def _upsert_document(pl: dict, chunk_count: int, used_uris: set[str]) -> str:
    from app.extensions import db
    from app.models.rag import LegalDocument

    did = str(pl.get("document_id") or "")
    uri = str(pl.get("document_uri") or f"backfill:{did}")
    # legal_document.source_uri is UNIQUE; duplicate URIs across documents
    # (the corpus has re-ingested files) get a stable per-document suffix.
    source_uri = uri
    if uri in used_uris:
        source_uri = f"{uri}#{did}"
    used_uris.add(source_uri)
    doc = db.session.get(LegalDocument, did) if did else None
    if doc is None:
        doc = LegalDocument(id=did, source_uri=source_uri)
        db.session.add(doc)
    doc.document_type = pl.get("document_type") or doc.document_type or "unknown"
    doc.title = pl.get("document_title") or doc.title
    doc.authority = pl.get("authority") or doc.authority
    doc.jurisdiction = pl.get("jurisdiction") or doc.jurisdiction
    doc.file_hash = _doc_hash(uri, did)
    doc.status = "indexed"
    doc.qdrant_collection = "fssai_legal_768"
    doc.chunk_count = chunk_count
    return did


def _upsert_chunk(p: dict) -> None:
    from app.extensions import db
    from app.models.rag import LegalChunk

    pl = p.get("payload") or {}
    cid = str(pl.get("chunk_id") or p.get("id") or "")
    if not cid:
        return
    chunk = db.session.get(LegalChunk, cid)
    if chunk is None:
        chunk = LegalChunk(id=cid)
        db.session.add(chunk)
    chunk.document_id = str(pl.get("document_id") or "")
    chunk.document_type = pl.get("document_type") or "unknown"
    chunk.section_number = pl.get("section_number")
    chunk.chunk_index = int(pl.get("chunk_index", 0) or 0)
    chunk.text = pl.get("chunk_text") or ""
    chunk.char_count = int(pl.get("chunk_char_count") or len(chunk.text))
    chunk.word_count = int(pl.get("word_count") or len(chunk.text.split()))
    chunk.hierarchy_level = int(pl.get("hierarchy_level", 0) or 0)
    chunk.parent_id = pl.get("parent_chunk_id")
    chunk.citations = pl.get("citations") or []
    chunk.references = pl.get("references") or []
    chunk.entities = pl.get("entities") or []
    chunk.metadata_json = pl
    chunk.content_hash = pl.get("content_hash") or ""
    chunk.qdrant_point_id = cid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="backup:backups/vector_store_fssai_legal_768_20260809_161941.json",
        help="backup:<path.json> | qdrant | <path.json>",
    )
    args = parser.parse_args(argv)

    from app import create_app
    from app.extensions import db

    _label, gen = _resolve_source(args.source)
    app = create_app()

    documents = 0
    chunks = 0
    with app.app_context():
        # Group points by document_id so each document commits atomically.
        # For the Qdrant source this is memory-bounded (one page at a time);
        # for the backup source the JSON is loaded once (documented one-shot).
        groups: dict[str, list] = {}
        order: list[str] = []
        used_uris: set[str] = set()
        for p in gen():
            pl = p.get("payload") or {}
            did = str(pl.get("document_id") or "?")
            if did not in groups:
                groups[did] = []
                order.append(did)
            groups[did].append(p)
        for did in order:
            pts = groups[did]
            _upsert_document(pts[0].get("payload") or {}, len(pts), used_uris)
            for p in pts:
                _upsert_chunk(p)
            documents += 1
            chunks += len(pts)
            db.session.commit()
            groups[did] = []  # release
            print(f"  doc {did[:8]}… {len(pts)} chunks")
        print(f"backfill complete: {documents} documents, {chunks} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
