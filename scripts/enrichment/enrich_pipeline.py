#!/usr/bin/env python3
"""Run the deterministic enrichment pipeline over the existing chunk corpus.

Implements (deterministic-only mode — the user-confirmed first stage):

* Phase 3  — rule-based enrichment (section attribution, legal location,
             cross-reference candidates, keywords, structural flags)
* Phase 6  — first-pass chunk-level cross-reference resolution
* Phase 10 — 8 GB memory budget (stream pages / one document at a time,
             per-batch RAM telemetry via tracemalloc)
* Phase 11 — resumable checkpointing (VALIDATED/ENRICHED chunks are skipped
             on restart; per-batch commits)
* Phase 12 — structural validation of every record (immutability, evidence
             spans, confidence, resolved-only cross-refs)

Outputs:
* app DB tables  chunk_enrichment / enrichment_checkpoint /
                  chunk_cross_reference / enrichment_resource_usage
* reports/enrichment_progress.json  — status counts
* reports/resource_usage.json       — RAM / duration telemetry

Usage:
    python scripts/enrichment/enrich_pipeline.py --source backup:<path.json>
    python scripts/enrichment/enrich_pipeline.py --source qdrant --batch-size 100

The original chunk text and the Qdrant index are never modified.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
import uuid
from pathlib import Path
from typing import Any

# Allow ``from audit_chunks import ...`` and ``from app import ...`` when
# run from anywhere (project root first, then this scripts dir).
_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR.parents[1]))  # project root
sys.path.insert(0, str(_SCRIPTS_DIR))

from audit_chunks import _resolve_source

REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"


def _track_ram() -> tuple[float, float]:
    """Return (peak_mb, current_mb) from tracemalloc (stdlib, portable)."""
    try:
        cur, peak = tracemalloc.get_traced_memory()
        return round(peak / (1024 * 1024), 2), round(cur / (1024 * 1024), 2)
    except (RuntimeError, ValueError):
        return 0.0, 0.0


def _pass_one(points, section_index: dict, doc_ids: set[str]) -> str | None:
    """Single streaming pass: doc ids + section index + Act discovery."""
    act_counts: dict[str, int] = {}
    act_titles: dict[str, str] = {}
    act_ids: list[str] = []
    for p in points:
        pl = p.get("payload") or {}
        did = pl.get("document_id")
        if did:
            doc_ids.add(str(did))
        sec = pl.get("section_number")
        if sec:
            key = (str(did or ""), str(sec))
            section_index.setdefault(key, []).append(
                (str(pl.get("chunk_id") or p.get("id") or ""), int(pl.get("chunk_index", 0) or 0))
            )
        if (pl.get("document_type") or "").lower() == "act":
            adid = str(did or "")
            act_counts[adid] = act_counts.get(adid, 0) + 1
            act_titles[adid] = act_titles.get(adid, "") or str(pl.get("document_title") or "")
            act_ids.append(adid)
    if not act_ids:
        return None
    # Score: FSS Act title hit first, then chunk count (largest act wins ties).
    return max(
        act_ids,
        key=lambda d: (
            1 if "food safety and standards act" in act_titles[d].lower() else 0,
            act_counts[d],
        ),
    )


def _flush(records: list[dict], batch_id: str, t0: float) -> dict[str, int]:
    """Validate + persist one batch; returns per-batch {ok, failed}."""
    from app.extensions import db
    from app.rag.enrichment.store import (
        finish_checkpoint,
        mark_failed,
        record_cross_references,
        record_resource_usage,
        upsert_enrichment,
    )
    from app.rag.enrichment.validation import validate_record

    ok = 0
    failed = 0
    valid_records: list[dict] = []
    for rec in records:
        payload = rec.pop("_source_payload", None)  # validation-only, not stored
        vr = validate_record(rec, payload)
        if vr.ok:
            upsert_enrichment(rec, status="VALIDATED")
            ok += 1
            valid_records.append(rec)
        else:
            upsert_enrichment(rec, status="FAILED")
            mark_failed(rec.get("chunk_id", ""), "; ".join(vr.issues[:5]))
            failed += 1
    record_cross_references(valid_records)  # edges only from validated records
    peak_mb, avg_mb = _track_ram()
    record_resource_usage(
        run_id=_CURRENT_RUN[0],
        batch_id=batch_id,
        peak_ram_mb=peak_mb,
        avg_ram_mb=avg_mb,
        batch_size=len(records),
        processed=len(records),
        failed=failed,
        retries=0,
        duration_s=time.monotonic() - t0,
    )
    finish_checkpoint(
        batch_id,
        last_chunk_id=records[-1].get("chunk_id") if records else None,
        processed=len(records),
        enriched=ok,
        failed=failed,
        skipped=0,
    )
    db.session.commit()
    return {"ok": ok, "failed": failed}


#: Module-level run id (set by main; used by _flush without plumbing).
_CURRENT_RUN: list[str] = [""]


def _iter_documents(points, source_label: str, doc_ids: set[str]):
    """Yield ``(document_id, [point, ...])`` one document at a time.

    For the ``qdrant`` source, each document is streamed via a scroll filter
    (``{"document_id": ...}``) so **only one document is ever in memory** —
    the full corpus is never materialised (Phase 10).  For the backup JSON
    source the file is loaded once (a documented one-shot read) and grouped;
    each group is released as soon as it is yielded.
    """
    if source_label == "qdrant":
        from audit_chunks import iter_qdrant_points

        for did in sorted(doc_ids):
            pts = list(iter_qdrant_points(filters={"document_id": did}))
            if pts:
                yield did, pts
        return

    groups: dict[str, list] = {}
    order: list[str] = []
    for p in points:
        did = str((p.get("payload") or {}).get("document_id") or "?")
        if did not in groups:
            groups[did] = []
            order.append(did)
        groups[did].append(p)
    for did in order:
        yield did, groups[did]
        groups[did] = []


def _pass_two(
    points,
    source_label: str,
    doc_ids: set[str],
    section_index: dict,
    act_document_id: str | None,
    already: set[str],
    batch_size: int,
) -> dict[str, Any]:
    """Enrich every document; returns {processed, enriched, failed, skipped}."""
    from app.extensions import db
    from app.rag.enrichment.deterministic import enrich_document
    from app.rag.enrichment.store import mark_failed, start_checkpoint

    counts = {"processed": 0, "enriched": 0, "failed": 0, "skipped": 0}
    pending: list[dict] = []

    def _maybe_flush() -> None:
        nonlocal pending
        while len(pending) >= batch_size:
            batch = pending[:batch_size]
            pending = pending[batch_size:]
            counts["processed"] += len(batch)
            batch_id = f"{_CURRENT_RUN[0]}-b{counts['processed']}"
            start_checkpoint(batch_id, batch_size)
            res = _flush(batch, batch_id, time.monotonic())
            counts["enriched"] += res["ok"]
            counts["failed"] += res["failed"]

    for did, pts in _iter_documents(points, source_label, doc_ids):
        fresh = pts
        if already:
            fresh = [
                p for p in pts
                if str((p.get("payload") or {}).get("chunk_id") or p.get("id") or "") not in already
            ]
            counts["skipped"] += len(pts) - len(fresh)
        if fresh:
            try:
                records = enrich_document(fresh, section_index, act_document_id)
            except Exception as exc:
                counts["failed"] += len(fresh)
                for p in fresh:
                    mark_failed(
                        str((p.get("payload") or {}).get("chunk_id") or p.get("id") or ""),
                        str(exc)[:300],
                    )
                db.session.commit()
                continue
            payload_map = {
                str((p.get("payload") or {}).get("chunk_id") or p.get("id") or ""): p.get("payload")
                for p in fresh
            }
            for rec in records:
                rec["_source_payload"] = payload_map.get(rec.get("chunk_id"))
            pending.extend(records)
        _maybe_flush()

    if pending:
        counts["processed"] += len(pending)
        batch_id = f"{_CURRENT_RUN[0]}-final"
        start_checkpoint(batch_id, len(pending))
        res = _flush(pending, batch_id, time.monotonic())
        counts["enriched"] += res["ok"]
        counts["failed"] += res["failed"]
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="backup:backups/vector_store_fssai_legal_768_20260809_161941.json",
        help="backup:<path.json> | qdrant | <path.json>",
    )
    parser.add_argument("--batch-size", type=int, default=50, help="chunks per batch (default 50, max 100)")
    parser.add_argument("--no-resume", action="store_true", help="reprocess already-enriched chunks")
    parser.add_argument("--run-id", default=None, help="run id (default: auto)")
    args = parser.parse_args(argv)

    if args.batch_size > 100:
        return 2

    from app import create_app
    from app.models.enrichment import ChunkEnrichment

    _label, gen = _resolve_source(args.source)
    run_id = args.run_id or f"det-{uuid.uuid4().hex[:8]}"
    _CURRENT_RUN[0] = run_id
    app = create_app()
    tracemalloc.start()

    with app.app_context():
        section_index: dict[tuple[str, str], list[str]] = {}
        doc_ids: set[str] = set()

        act_id = _pass_one(gen(), section_index, doc_ids)

        already: set[str] = set()
        if not args.no_resume:
            already = {
                row.chunk_id
                for row in ChunkEnrichment.query.filter(
                    ChunkEnrichment.status.in_(["ENRICHED", "VALIDATED"])
                ).all()
            }

        _pass_two(gen(), _label, doc_ids, section_index, act_id, already, args.batch_size)

        from app.rag.enrichment.store import progress_summary, resource_usage_summary

        progress = progress_summary()
        usage = resource_usage_summary()
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "enrichment_progress.json").write_text(
            json.dumps(progress, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (REPORT_DIR / "resource_usage.json").write_text(
            json.dumps(usage, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    tracemalloc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
