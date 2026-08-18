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
  * **Clause propagation (L6, G8 step 2+3, 2026-08-18)** — header-anchored
    propagation of the last dotted clause header forward within a document
    (mirror of the L5 section pass in ``backfill_payload_identity.py``):
    fills ``clause_number`` on substantive (hl>=2) chunks that follow a
    verified dotted clause boundary in the same document.  Never
    overwrites; resets at ``PART``/``SCHEDULE``/``CHAPTER``/``ANNEXURE``
    boundaries; scoped to regulation/notification/rule documents
    (``--clause-doc-types``).  This is what gives the parenthetical
    sub-clause chains under a dotted header (e.g. ``(1) Every petty Food
    Business Operator…`` under ``2.1.1``) their parent clause — G8 step 3.

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
    python scripts/backfill_subsection.py --no-clause-propagation  # marker fills only
    python scripts/backfill_subsection.py --clause-doc-types regulation,notification

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
logger = logging.getLogger("backfill.subsection")

CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
SNAPSHOT_DIR = PROJECT_ROOT / "reports"

#: Structural boundaries that reset clause propagation (G8 step 2 — same
#: semantics as the L5 section pass).  A new PART/SCHEDULE/CHAPTER/ANNEXURE
#: starts a fresh identity namespace; the previous running clause must not
#: leak across it.
_STRUCTURAL_BOUNDARY_RE = re.compile(r"^\s*(PART|SCHEDULE|CHAPTER|ANNEXURE)\b", re.I)

#: Document types whose identity is a dotted clause number (G8).  ``rule``
#: is included because rules use the same dotted numbering (``1.2.3 Rule
#: heading``) — the ``_extract_clause_number`` guard keeps the pass honest.
DEFAULT_CLAUSE_DOC_TYPES = ("regulation", "notification", "rule")


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


def derive_clause_propagation(
    payloads_by_doc: dict[str, list[dict]],
    document_types: tuple[str, ...] = DEFAULT_CLAUSE_DOC_TYPES,
) -> dict[str, str]:
    """Return {point_id: clause_number} for propagatable fragments (L6).

    ``payloads_by_doc`` maps document_id -> payloads **already ordered by
    chunk_index** (each payload carries its ``chunk_id``).  Mirror of the
    L5 section propagation in ``backfill_payload_identity.py``:

      * a chunk whose text starts with a guarded dotted clause number
        (``_extract_clause_number``) is a verified boundary — it resets the
        running clause (and keeps its own ``clause_number``, never
        overwritten here),
      * ``PART``/``SCHEDULE``/``CHAPTER``/``ANNEXURE`` headers reset the
        running clause to ``None`` (new identity namespace),
      * a chunk with NO ``clause_number`` AND ``hierarchy_level >= 2``
        inherits the running clause,
      * documents outside *document_types* (act/circular/etc.) never
        propagate — their identity is the section, not a clause,
      * everything else (already stamped, hl1 boilerplate, before the first
        boundary) is left untouched.

    This is what gives G8 step-3 parenthetical sub-clause chains
    (``(1)…(6)`` under a dotted header like ``2.1.1``) their parent clause.
    """
    from app.rag.chunker import _extract_clause_number

    out: dict[str, str] = {}
    for _doc_id, payloads in payloads_by_doc.items():
        running: str | None = None
        for pl in payloads:
            if pl.get("document_type") not in document_types:
                running = None
                continue
            text = pl.get("chunk_text") or ""
            if _STRUCTURAL_BOUNDARY_RE.match(text):
                running = None
                continue
            cn = _extract_clause_number(text)
            if cn:
                running = cn
                continue
            if running and not pl.get("clause_number") and (pl.get("hierarchy_level") or 1) >= 2:
                out[str(pl.get("chunk_id") or "")] = running
    return out


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
    parser.add_argument(
        "--no-clause-propagation",
        action="store_true",
        help="disable the L6 clause-propagation pass (marker fills only)",
    )
    parser.add_argument(
        "--clause-doc-types",
        default=",".join(DEFAULT_CLAUSE_DOC_TYPES),
        help="comma-separated document_type values that may receive propagated clause numbers (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    clause_doc_types = tuple(t.strip() for t in args.clause_doc_types.split(",") if t.strip())

    from app import create_app

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

        # --- L6 clause propagation (G8 step 2+3, 2026-08-18): header-anchored
        # propagation of the last dotted clause header forward within a
        # document (mirror of L5).  Merges into ``changes`` so a chunk that
        # derive() filled with subsection-only still gets its parent clause;
        # never overwrites an existing clause_number (derive or payload).
        prop_added = 0
        if not args.no_clause_propagation:
            payloads_by_doc: dict[str, list[dict]] = {}
            for pid, pl in payloads.items():
                payloads_by_doc.setdefault(str(pl.get("document_id") or ""), []).append(dict(pl, chunk_id=pid))
            for pls in payloads_by_doc.values():
                pls.sort(key=lambda p: p.get("chunk_index") or 0)
            prop = derive_clause_propagation(payloads_by_doc, clause_doc_types)
            for pid, cn in prop.items():
                if payloads.get(pid, {}).get("clause_number"):
                    continue
                existing = changes.get(pid)
                if existing is None:
                    changes[pid] = {"clause_number": cn}
                elif "clause_number" not in existing:
                    existing["clause_number"] = cn
                else:
                    continue
                prop_added += 1
                by_collection[pid] = 1
            clause_added += prop_added
            logger.info("clause propagation: +%d fills", prop_added)

        logger.info(
            "derived changes: %d points (subsection +%d, clause_number +%d incl. %d propagated)",
            len(changes),
            sub_added,
            clause_added,
            prop_added,
        )

        # provenance (which collection each point lives in) for reporting
        prov: dict[str, str] = {}
        if use_live:
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
            "clause_propagation_fills": prop_added,
            "changes": len(changes),
            "by_collection": dict(by_coll),
            "note": "identity-preserving payload backfill (G6+G8): fills missing subsection, "
            "adds guarded dotted clause_number, L6 clause propagation. Vectors untouched.",
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

            if applied == 0 and changes:
                logger.error("apply wrote 0 updates for %d changes — aborting before DB mirror", len(changes))
                return 1

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
