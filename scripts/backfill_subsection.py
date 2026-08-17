"""Backfill `subsection` / `clause_number` on Qdrant payloads (G6, 2026-08-17).

Improves subsection-family coverage on the existing corpus **without
re-chunking** (identity-preserving — same chunk ids, vectors untouched):

  * ``subsection``  — recomputed with the canonical chunker rule
    (``_extract_subsection_markers``: leading parenthetical chains).  Only
    fills chunks that currently have NO value; existing values are never
    overwritten.
  * ``clause_number`` — NEW field (2026-08-17) for leading dotted regulatory
    clause numbers (``2.4.15``, ``3.04``) that the parenthetical regex
    cannot see.  Semantically distinct from ``subsection`` (a clause number
    is NOT a section subsection), so it lives in its own payload field.
    Guarded (``_extract_clause_number``): dates, measurements, bare numbers
    and OCR residue are rejected.

Why payload-only: re-chunking the PDFs would mint fresh chunk ids and break
``LegalChunk.id`` / ``qdrant_point_id`` identity (the same constraint that
drove ``reingest_fssai_from_db.py``).  This script updates Qdrant payloads
via ``set_payload`` (payload-only, vectors untouched) and — for chunks that
exist in the local DB — mirrors the change into ``LegalChunk.metadata_json``
so a future DB-driven re-ingest preserves the stamps.

Usage:
    python scripts/backfill_subsection.py                    # dry-run (frozen payload cache)
    python scripts/backfill_subsection.py --live             # dry-run (scroll Qdrant)
    python scripts/backfill_subsection.py --apply            # write Qdrant + DB metadata_json
    python scripts/backfill_subsection.py --apply --no-db    # Qdrant only

Exit codes: 0 ok, 1 failure, 2 usage/guard error.
"""

from __future__ import annotations

import argparse
import json
import logging
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
logger = logging.getLogger("backfill.subsection")

CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
SNAPSHOT_DIR = PROJECT_ROOT / "reports"


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


def derive(payload: dict) -> dict:
    """Return the subsection/clause_number stamps to add, or {} if none.

    Never overwrites: only fills missing ``subsection`` and adds
    ``clause_number`` when both are absent.  ``clause_number`` is computed
    for ALL chunks (not just subsection-less ones) so a chunk that starts
    with a dotted clause AND carries a parenthetical marker keeps both.
    """
    from app.rag.chunker import _extract_clause_number, _extract_subsection_markers

    changes: dict = {}
    if not payload.get("subsection"):
        ss = _extract_subsection_markers(payload.get("chunk_text") or "")
        if ss:
            changes["subsection"] = ss
    if not payload.get("clause_number"):
        cn = _extract_clause_number(payload.get("chunk_text") or "")
        if cn:
            changes["clause_number"] = cn
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill subsection/clause_number (G6).")
    parser.add_argument("--apply", action="store_true", help="write Qdrant (+ DB metadata_json unless --no-db)")
    parser.add_argument("--live", action="store_true", help="scroll Qdrant (default: frozen payload cache)")
    parser.add_argument("--no-db", action="store_true", help="apply to Qdrant only; skip DB metadata_json")
    parser.add_argument("--limit", type=int, default=None, help="only process first N points (testing)")
    args = parser.parse_args(argv)

    from app import create_app

    app = create_app()
    collections = collections_from_config(app)

    with app.app_context():
        if args.live or not CACHE.exists():
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

        # --- derive changes
        changes: dict[str, dict] = {}
        sub_added = 0
        clause_added = 0
        by_collection: Counter = Counter()
        for pid, pl in payloads.items():
            ch = derive(pl)
            if ch:
                changes[pid] = ch
                if "subsection" in ch:
                    sub_added += 1
                if "clause_number" in ch:
                    clause_added += 1
                by_collection[pid] = 1  # provenance resolved below

        logger.info("derived changes: %d points (subsection +%d, clause_number +%d)",
                    len(changes), sub_added, clause_added)

        # provenance (which collection each point lives in) for reporting
        prov: dict[str, str] = {}
        if args.live:
            from app.rag.qdrant_client import QdrantStore
            for coll in collections:
                store = QdrantStore(collection_name=coll)
                pts = store.scroll_all(batch_size=500)
                for p in pts:
                    prov[str(p["id"])] = coll
        else:
            prov = {pid: "cache" for pid in changes}

        by_coll: Counter = Counter(prov.get(pid, "?") for pid in changes)

        before_ss = sum(1 for p in payloads.values() if p.get("subsection"))
        before_cn = sum(1 for p in payloads.values() if p.get("clause_number"))
        after_ss = before_ss + sub_added
        after_cn = before_cn + clause_added

        summary = {
            "mode": "apply" if args.apply else "dry-run",
            "total_points": len(payloads),
            "subsection_before": before_ss,
            "subsection_after": after_ss,
            "clause_number_before": before_cn,
            "clause_number_after": after_cn,
            "changes": len(changes),
            "by_collection": dict(by_coll),
            "note": "identity-preserving payload backfill (G6): fills missing subsection, "
                    "adds guarded dotted clause_number. Vectors untouched.",
        }

        if args.apply:
            snap = SNAPSHOT_DIR / "subsection_backfill_pre.jsonl"
            with open(snap, "w", encoding="utf-8") as f:
                for pid, pl in payloads.items():
                    f.write(json.dumps({"id": pid, "payload": pl}, ensure_ascii=False) + "\n")
            logger.info("pre-backfill snapshot -> %s", snap)

            from app.rag.qdrant_client import QdrantStore

            applied = 0
            for coll in collections:
                chg = {pid: ch for pid, ch in changes.items() if prov.get(pid) == coll}
                if not chg:
                    continue
                store = QdrantStore(collection_name=coll)
                n = set_payload_batched(store._get_client(), coll, chg)
                applied += n
                logger.info("collection %s: %d updates", coll, n)

            if not args.no_db:
                n_db = mirror_metadata_json(changes)
                summary["db_metadata_json_updated"] = n_db
                logger.info("DB metadata_json mirrored for %d chunks", n_db)

            summary["qdrant_applied"] = applied
        else:
            pass

        name = "backfill_subsection_apply.json" if args.apply else "backfill_subsection_dryrun.json"
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        (SNAPSHOT_DIR / name).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    return 0


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


def mirror_metadata_json(changes: dict[str, dict]) -> int:
    """Mirror the payload changes into ``LegalChunk.metadata_json`` for rows
    that exist in the local DB (identity-preserving: a later DB-driven
    re-ingest must not lose the stamps)."""
    from app.extensions import db
    from app.models.rag import LegalChunk

    updated = 0
    for cid, ch in changes.items():
        chunk = db.session.get(LegalChunk, cid)
        if chunk is None:
            continue
        try:
            meta = dict(json.loads(chunk.metadata_json or "{}"))
        except Exception:
            meta = {}
        meta.update(ch)
        chunk.metadata_json = json.dumps(meta)
        updated += 1
    if updated:
        db.session.commit()
    return updated


if __name__ == "__main__":
    raise SystemExit(main())
