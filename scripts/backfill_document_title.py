"""Backfill `document_title` on Qdrant payloads (G8 / COVERAGE_COMPLETENESS P1, 2026-08-18).

G8 finding: ``document_title`` is empty on 12,820 of 27,351 payloads (the
fssai corpus was ingested with a NULL DB ``title`` — only ``document_uri``
filenames like ``Food_Additives_Regulations-4.pdf`` exist).  This script
derives a human-readable title from the ``document_uri`` basename and stamps
it on every chunk of the document:

  * basename (path + ``#fragment`` + extension stripped),
  * ``_`` -> space, whitespace collapsed,
  * leading junk digits/dashes dropped when a capital word follows
    (``6928478129442Final Notificat…`` -> ``Final Notificat…``,
    ``1_Notification dt 10_03_2026`` -> ``Notification dt 10 03 2026``),
  * unchanged when the filename is already clean (``FSS Amendment Act
    3-2023``) or opaque (``A2013-18`` — best-effort, never invented).

Payload-only and identity-preserving (same chunk ids, vectors untouched);
never overwrites an existing ``document_title``.  The DB mirrors the change
into ``LegalDocument.title`` and ``LegalChunk.metadata_json`` so a future
DB-driven re-ingest preserves the titles.

Usage:
    python scripts/backfill_document_title.py              # dry-run (frozen payload cache)
    python scripts/backfill_document_title.py --live       # dry-run (scroll Qdrant)
    python scripts/backfill_document_title.py --apply      # write Qdrant + DB
    python scripts/backfill_document_title.py --apply --no-db

Exit codes: 0 ok, 1 failure, 2 usage/guard error.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()
os_environ_defaults = __import__("os").environ.setdefault
os_environ_defaults("SKIP_FSO_STARTUP_SYNC", "1")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill.document_title")

CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
SNAPSHOT_DIR = PROJECT_ROOT / "reports"

_EXT_RE = re.compile(r"\.(pdf|docx?|txt|html?)$", re.IGNORECASE)
#: leading junk (digits/dashes/underscores) followed by a capital word —
#: ``6928478129442Final …`` -> ``Final …``; ``1_Notification …`` -> ``Notification …``.
_JUNK_PREFIX_RE = re.compile(r"^[\d\-_]+(?=\s*[A-Z])")


def derive_title(uri: str | None) -> str:
    """Best-effort human-readable title from a ``document_uri`` filename.

    Returns ``""`` when there is nothing usable (no uri / empty basename).
    """
    if not uri:
        return ""
    name = str(uri).replace("\\", "/").rsplit("/", 1)[-1]
    name = name.split("#", 1)[0].strip()
    if not name:
        return ""
    name = _EXT_RE.sub("", name)
    title = re.sub(r"[_]+", " ", name).strip()
    title = re.sub(r"\s+", " ", title)
    m = _JUNK_PREFIX_RE.match(title)
    if m:
        title = title[m.end():].strip()
    if len(title) >= 3:
        return title
    return name  # fall back to the raw basename (opaque but non-empty)


def scroll_payloads(app, collections) -> dict[str, dict]:
    """Scroll every collection once -> {point_id: payload}."""
    from app.rag.qdrant_client import QdrantStore

    payloads: dict[str, dict] = {}
    for coll in collections:
        store = QdrantStore(collection_name=coll)
        points = store.scroll_all(batch_size=500)
        for p in points:
            payloads[str(p["id"])] = p.get("payload") or {}
        logger.info("scrolled %s: %d points", coll, len(points))
    return payloads


def collections_from_config(app) -> list[str]:
    cfg = app.config
    return list(dict.fromkeys([
        cfg.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
    ]))


def derive_changes(payloads: dict[str, dict]) -> dict[str, dict]:
    """Map point_id -> {"document_title": title} for chunks with an empty
    title whose document has a derivable title.  Never overwrites."""
    uri_by_doc: dict[str, str] = {}
    for pl in payloads.values():
        did = str(pl.get("document_id") or "")
        uri = pl.get("document_uri") or ""
        if did and uri and did not in uri_by_doc:
            uri_by_doc[did] = uri
    title_by_doc = {did: derive_title(uri) for did, uri in uri_by_doc.items()}
    changes: dict[str, dict] = {}
    for pid, pl in payloads.items():
        if pl.get("document_title"):
            continue
        title = title_by_doc.get(str(pl.get("document_id") or ""))
        if title:
            changes[pid] = {"document_title": title}
    return changes


def set_payload_batched(client, collection: str, changes: dict[str, dict], batch_size: int = 200) -> int:
    """Group ids by identical payload dict; set_payload per group in batches."""
    groups: dict[str, list[str]] = {}
    for pid, ch in changes.items():
        groups.setdefault(json.dumps(ch, sort_keys=True), []).append(pid)
    applied = 0
    for key, ids in groups.items():
        payload = changes[ids[0]]
        for i in range(0, len(ids), batch_size):
            batch = ids[i:i + batch_size]
            try:
                client.set_payload(collection_name=collection, payload=payload, points=batch)
                applied += len(batch)
            except TypeError:
                client.set_payload(collection=collection, payload=payload, points=batch)
                applied += len(batch)
            except Exception as exc:
                logger.warning("set_payload batch failed (%s) — retrying per point", exc)
                for pid in batch:
                    try:
                        client.set_payload(collection_name=collection, payload=payload, points=[pid])
                        applied += 1
                    except Exception as exc2:
                        logger.warning("set_payload %s failed: %s", pid, exc2)
    return applied


def mirror_db(document_titles: dict[str, str]) -> dict:
    """Mirror per-document titles into ``LegalDocument.title`` and each
    chunk's ``LegalChunk.metadata_json`` for rows that exist locally."""
    from app.extensions import db
    from app.models.rag import LegalChunk, LegalDocument

    docs_updated = 0
    chunks_updated = 0
    for did, title in document_titles.items():
        doc = db.session.get(LegalDocument, did)
        if doc is not None and not doc.title:
            doc.title = title
            docs_updated += 1
    if docs_updated:
        db.session.flush()
    # chunks: metadata_json carries the full payload cache — stamp the title
    # in so a DB-driven re-ingest preserves it.
    rows = LegalChunk.query.filter(
        LegalChunk.document_id.in_(list(document_titles))
    ).all()
    for chunk in rows:
        try:
            meta = dict(chunk.metadata_json or {})
        except Exception:
            meta = {}
        title = document_titles.get(str(chunk.document_id) or "")
        if title and not meta.get("document_title"):
            meta["document_title"] = title
            chunk.metadata_json = meta
            chunks_updated += 1
    if chunks_updated:
        db.session.commit()
    return {"legal_documents": docs_updated, "legal_chunks": chunks_updated}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill document_title from document_uri (G8 P1).")
    parser.add_argument("--apply", action="store_true", help="write Qdrant (+ DB mirror unless --no-db)")
    parser.add_argument("--live", action="store_true", help="scroll Qdrant (default: frozen payload cache)")
    parser.add_argument("--no-db", action="store_true", help="apply to Qdrant only; skip DB mirror")
    parser.add_argument("--limit", type=int, default=None, help="only process first N points (testing)")
    args = parser.parse_args(argv)

    from app import create_app

    app = create_app()
    collections = collections_from_config(app)

    # ``--apply`` implies a live scroll (see strip_reg_section_noise.py):
    # changes must derive from current data and the write loop needs real
    # per-collection provenance.
    use_live = args.live or args.apply

    with app.app_context():
        if use_live or not CACHE.exists():
            payloads = scroll_payloads(app, collections)
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE, "w", encoding="utf-8") as f:
                for pid, pl in payloads.items():
                    f.write(json.dumps({"id": pid, "payload": pl}, ensure_ascii=False) + "\n")
            logger.info("payload cache refreshed: %d points", len(payloads))
        else:
            payloads = {}
            with open(CACHE, encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    payloads[str(rec["id"])] = rec["payload"]
            logger.info("payload cache loaded: %d points", len(payloads))

        items = list(payloads.items())
        if args.limit:
            items = items[: args.limit]
        payloads = dict(items)

        changes = derive_changes(payloads)
        by_doc: dict[str, str] = {}
        for pid, ch in changes.items():
            by_doc[str(payloads[pid].get("document_id") or "")] = ch["document_title"]

        before = sum(1 for p in payloads.values() if p.get("document_title"))
        summary = {
            "mode": "apply" if args.apply else "dry-run",
            "total_points": len(payloads),
            "document_title_before": before,
            "document_title_after": before + len(changes),
            "fills": len(changes),
            "documents_titled": len(by_doc),
            "note": "identity-preserving title backfill (G8 P1): derives document_title "
                    "from document_uri filenames; never overwrites. Vectors untouched.",
        }

        if args.apply:
            from app.rag.qdrant_client import QdrantStore

            prov: dict[str, str] = {}
            for coll in collections:
                store = QdrantStore(collection_name=coll)
                for p in store.scroll_all(batch_size=500):
                    prov[str(p["id"])] = coll

            snap = SNAPSHOT_DIR / "document_title_backfill_pre.jsonl"
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            with open(snap, "w", encoding="utf-8") as f:
                for pid, pl in payloads.items():
                    f.write(json.dumps({"id": pid, "payload": pl}, ensure_ascii=False) + "\n")
            logger.info("pre-backfill snapshot -> %s", snap)

            applied = 0
            by_coll: Counter = Counter()
            for coll in collections:
                chg = {pid: ch for pid, ch in changes.items() if prov.get(pid) == coll}
                if not chg:
                    continue
                store = QdrantStore(collection_name=coll)
                n = set_payload_batched(store._get_client(), coll, chg)
                applied += n
                by_coll[coll] = n
                logger.info("collection %s: %d updates", coll, n)

            if applied == 0 and changes:
                logger.error("apply wrote 0 updates for %d changes — aborting before DB mirror",
                             len(changes))
                return 1
            summary["qdrant_applied"] = applied
            summary["by_collection"] = dict(by_coll)

            if not args.no_db:
                db_summary = mirror_db(by_doc)
                summary["db_mirror"] = db_summary
                logger.info("DB mirror: %s", db_summary)

        name = "document_title_backfill_apply.json" if args.apply else "document_title_backfill_dryrun.json"
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        (SNAPSHOT_DIR / name).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
