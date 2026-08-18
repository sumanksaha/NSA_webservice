"""Strip spurious ``section_number`` from regulation/notification chunks (G8, 2026-08-17).

G8 finding (verified on the 27,351-chunk payload index): 1,518 regulation
+ 36 notification chunks carry a ``section_number`` that is NOT an Act
section — page numbers (``41``, ``01 -``), definition-list numbers, or
cross-references in text (``section 23 of Food Safety and Standards Act,
2006``).  They came from the L4 repair regex (``\\d{1,4}\\. [A-Z]``)
matching the *first* number on a line, and they pollute section-based
matching (``matches_gold``, ``same_section`` mining) with false positives —
e.g. a regulation fragment stamped ``sec=36`` matches any Act section-36
gold.

This script deletes ``section_number`` from every chunk whose
``document_type`` is regulation/notification/rule (``--document-types``
configurable).  The identity for those documents is the dotted
``clause_number`` (G6/G8), never an Act section — see
``scripts/backfill_subsection.py``.  ``rule`` chunks show the same noise
profile (page numbers, notification numbers, form labels, cross-references
like ``sub-rule (1) of rule 17`` — verified 2026-08-18: 298 rule chunks), so
they are stripped by default too.

Payload-only and identity-preserving (same chunk ids, vectors untouched):
``set_payload`` with ``{"section_number": None}`` deletes the field in
Qdrant (null payload values are field deletions), and the DB
``metadata_json`` cache mirrors the removal so a future DB-driven re-ingest
does not resurrect the noise.

Usage:
    python scripts/strip_reg_section_noise.py                  # dry-run (frozen payload cache)
    python scripts/strip_reg_section_noise.py --live           # dry-run (scroll Qdrant)
    python scripts/strip_reg_section_noise.py --apply          # write Qdrant + DB metadata_json
    python scripts/strip_reg_section_noise.py --apply --no-db  # Qdrant only
    python scripts/strip_reg_section_noise.py --apply --verify # re-scroll Qdrant and report residue
    python scripts/strip_reg_section_noise.py --document-types regulation,notification,rule

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
logger = logging.getLogger("strip.reg.section")

CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
SNAPSHOT_DIR = PROJECT_ROOT / "reports"

#: Document types whose identity is a clause number, not an Act section
#: (G8).  ``rule`` is included (2026-08-18) — its 298 section_number stamps
#: are the same page-number/def-list/xref noise (``161 27960/2022/UPC-II-HO``,
#: ``6 Summary of the mechanisms…``), never genuine Act sections.
DEFAULT_DOC_TYPES = ("regulation", "notification", "rule")


def derive_strip(payload: dict, document_types: tuple[str, ...] = DEFAULT_DOC_TYPES) -> dict | None:
    """Return ``{"section_number": None}`` (delete sentinel) or ``None``.

    Only chunks whose ``document_type`` is in *document_types* and that
    currently carry a ``section_number`` are stripped.  ``None`` is the
    Qdrant null-delete sentinel — setting a payload field to null deletes
    it; ``section_number`` must disappear, not become an empty string that
    would still satisfy ``payload.get("section_number")``.
    """
    if payload.get("document_type") in document_types and payload.get("section_number") is not None:
        return {"section_number": None}
    return None


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
    return list(
        dict.fromkeys([
            cfg.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
            cfg.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
        ])
    )


def set_payload_batched(client, collection: str, changes: dict[str, dict], batch_size: int = 200) -> int:
    """Group ids by identical payload dict; set_payload per group in batches."""
    groups: dict[str, list[str]] = {}
    for pid, ch in changes.items():
        groups.setdefault(json.dumps(ch, sort_keys=True), []).append(pid)
    applied = 0
    for key, ids in groups.items():
        payload = changes[ids[0]]
        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
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


def mirror_metadata_json_removal(ids: list[str], keys: tuple[str, ...]) -> int:
    """Remove *keys* from ``LegalChunk.metadata_json`` for rows that exist
    in the local DB (mirror of the Qdrant payload deletion — a later
    DB-driven re-ingest must not resurrect the noise)."""
    from app.extensions import db
    from app.models.rag import LegalChunk

    updated = 0
    for cid in ids:
        chunk = db.session.get(LegalChunk, cid)
        if chunk is None:
            continue
        try:
            meta = dict(json.loads(chunk.metadata_json or "{}"))
        except Exception:
            meta = {}
        changed = False
        for k in keys:
            if k in meta:
                del meta[k]
                changed = True
        if changed:
            chunk.metadata_json = json.dumps(meta)
            updated += 1
    if updated:
        db.session.commit()
    return updated


def verify_removed(app, collections, document_types: tuple[str, ...]) -> dict:
    """Re-scroll Qdrant after --apply and count remaining section_number on
    stripped document types (safety net for the null-delete semantics)."""
    from app.rag.qdrant_client import QdrantStore

    residue = 0
    by_type: Counter = Counter()
    for coll in collections:
        store = QdrantStore(collection_name=coll)
        for p in store.scroll_all(batch_size=500):
            pl = p.get("payload") or {}
            if pl.get("document_type") in document_types and pl.get("section_number") is not None:
                residue += 1
                by_type[pl.get("document_type")] += 1
    return {"residue": residue, "by_type": dict(by_type)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strip spurious section_number from reg/notification (G8 step 1).")
    parser.add_argument("--apply", action="store_true", help="write Qdrant (+ DB metadata_json unless --no-db)")
    parser.add_argument("--live", action="store_true", help="scroll Qdrant (default: frozen payload cache)")
    parser.add_argument("--no-db", action="store_true", help="apply to Qdrant only; skip DB metadata_json")
    parser.add_argument("--verify", action="store_true", help="after --apply, re-scroll Qdrant and report residue")
    parser.add_argument("--limit", type=int, default=None, help="only process first N points (testing)")
    parser.add_argument(
        "--document-types",
        default=",".join(DEFAULT_DOC_TYPES),
        help="comma-separated document_type values to strip (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    document_types = tuple(t.strip() for t in args.document_types.split(",") if t.strip())
    if not document_types:
        print("usage: --document-types must list at least one type", file=sys.stderr)
        return 2

    from app import create_app
    from app.rag.qdrant_client import QdrantStore

    app = create_app()
    collections = collections_from_config(app)

    # ``--apply`` implies a live scroll: the per-collection write loop needs
    # real provenance (which point lives in which collection), and changes
    # must be derived from the current data — never from a possibly stale
    # frozen cache.  ``--live`` alone stays a dry-run on live data.
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

        # --- derive deletions
        deletions: dict[str, dict] = {}
        by_type: Counter = Counter()
        for pid, pl in payloads.items():
            ch = derive_strip(pl, document_types)
            if ch:
                deletions[pid] = ch
                by_type[pl.get("document_type")] += 1

        logger.info("stripping section_number from %d points %s", len(deletions), dict(by_type))

        # provenance (which collection each point lives in) for reporting
        prov: dict[str, str] = {}
        if use_live:
            for coll in collections:
                store = QdrantStore(collection_name=coll)
                for p in store.scroll_all(batch_size=500):
                    prov[str(p["id"])] = coll
        else:
            prov = {pid: "cache" for pid in deletions}

        by_coll: Counter = Counter(prov.get(pid, "?") for pid in deletions)

        before = sum(1 for p in payloads.values() if p.get("section_number"))
        summary = {
            "mode": "apply" if args.apply else "dry-run",
            "document_types": list(document_types),
            "total_points": len(payloads),
            "section_number_before": before,
            "section_number_after": before - len(deletions),
            "stripped": len(deletions),
            "by_document_type": dict(by_type),
            "by_collection": dict(by_coll),
            "note": "identity-preserving strip (G8 step 1): deletes spurious section_number "
            "from regulation/notification chunks (page numbers, def-list numbers, "
            "cross-references). Vectors untouched.",
        }

        # --- evidence CSV (a sample of what was stripped, for review)
        rows = []
        for pid in list(deletions)[:500]:
            pl = payloads[pid]
            rows.append({
                "collection": prov.get(pid, "cache"),
                "point_id": pid,
                "document_type": pl.get("document_type"),
                "old_section_number": pl.get("section_number"),
                "clause_number": pl.get("clause_number") or "",
                "evidence": str(pl.get("chunk_text") or "")[:200].replace("\n", " "),
            })
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = SNAPSHOT_DIR / "strip_reg_section_noise_evidence.csv"
        import csv as _csv

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(
                f,
                fieldnames=[
                    "collection",
                    "point_id",
                    "document_type",
                    "old_section_number",
                    "clause_number",
                    "evidence",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
        logger.info("evidence CSV -> %s (%d rows)", csv_path, len(rows))

        if args.apply:
            snap = SNAPSHOT_DIR / "strip_reg_section_noise_pre.jsonl"
            with open(snap, "w", encoding="utf-8") as f:
                for pid, pl in payloads.items():
                    f.write(json.dumps({"id": pid, "payload": pl}, ensure_ascii=False) + "\n")
            logger.info("pre-strip snapshot -> %s", snap)

            from app.rag.qdrant_client import QdrantStore

            applied = 0
            for coll in collections:
                chg = {pid: ch for pid, ch in deletions.items() if prov.get(pid) == coll}
                if not chg:
                    continue
                store = QdrantStore(collection_name=coll)
                n = set_payload_batched(store._get_client(), coll, chg)
                applied += n
                logger.info("collection %s: %d updates", coll, n)

            if applied == 0 and deletions:
                logger.error("apply wrote 0 updates for %d deletions — aborting before DB mirror", len(deletions))
                return 1

            if not args.no_db:
                n_db = mirror_metadata_json_removal(list(deletions), ("section_number",))
                summary["db_metadata_json_updated"] = n_db
                logger.info("DB metadata_json mirrored for %d chunks", n_db)

            summary["qdrant_applied"] = applied

            if args.verify:
                summary["verify"] = verify_removed(app, collections, document_types)
                logger.info("post-apply verification: %s", summary["verify"])

        name = "strip_reg_section_noise_apply.json" if args.apply else "strip_reg_section_noise_dryrun.json"
        (SNAPSHOT_DIR / name).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
