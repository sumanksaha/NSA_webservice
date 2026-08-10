"""Persistence helpers for the enrichment store (ORM-backed).

All writes go through the app's SQLAlchemy session (same DB as the empty
``legal_chunk``/``legal_document`` registry — the user-confirmed persistence
surface).  Each batch is committed independently so a crash never loses more
than the in-flight batch (Phase 11 checkpointing).

Memory contract: the store only ever receives the *current batch* — it never
materialises the corpus.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Iterable

from app.extensions import db
from app.models.enrichment import (
    ChunkCrossReference,
    ChunkEnrichment,
    EnrichmentCheckpoint,
    ResourceUsage,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def upsert_enrichment(record: dict, status: str = "ENRICHED") -> None:
    """Insert or replace one enrichment record (keyed by chunk_id)."""
    row = db.session.get(ChunkEnrichment, record["chunk_id"])
    if row is None:
        row = ChunkEnrichment(chunk_id=record["chunk_id"])
        db.session.add(row)
    row.enrichment_version = record.get("enrichment_version", "1.0")
    row.status = status
    row.data = record
    row.original_sha256 = record.get("original_sha256", "")
    row.confidence = record.get("confidence")
    row.llm_used = bool(record.get("provenance", {}).get("llm_used"))
    row.error = None


def mark_failed(chunk_id: str, error: str) -> None:
    """Record a failed chunk without discarding prior valid data."""
    row = db.session.get(ChunkEnrichment, chunk_id)
    if row is None:
        row = ChunkEnrichment(
            chunk_id=chunk_id,
            enrichment_version="1.0",
            status="FAILED",
            data={"chunk_id": chunk_id, "error": error},
            original_sha256="",
        )
        db.session.add(row)
    row.status = "FAILED"
    row.error = error


def start_checkpoint(batch_id: str, batch_size: int) -> None:
    row = db.session.get(EnrichmentCheckpoint, batch_id)
    if row is None:
        row = EnrichmentCheckpoint(batch_id=batch_id, batch_size=batch_size, status="RUNNING")
        db.session.add(row)
    else:
        row.batch_size = batch_size
        row.status = "RUNNING"
        row.finished_at = None
        row.started_at = datetime.now(UTC)


def finish_checkpoint(
    batch_id: str,
    *,
    last_chunk_id: str | None,
    processed: int,
    enriched: int,
    failed: int,
    skipped: int,
    status: str = "COMPLETE",
) -> None:
    row = db.session.get(EnrichmentCheckpoint, batch_id)
    if row is None:
        row = EnrichmentCheckpoint(batch_id=batch_id, batch_size=processed, status=status)
        db.session.add(row)
    row.last_chunk_id = last_chunk_id
    row.processed = processed
    row.enriched = enriched
    row.failed = failed
    row.skipped = skipped
    row.status = status
    row.finished_at = datetime.now(UTC)


def get_last_checkpoint() -> EnrichmentCheckpoint | None:
    """Return the most recently completed checkpoint (resume anchor)."""
    return (
        EnrichmentCheckpoint.query.order_by(EnrichmentCheckpoint.finished_at.desc().nulls_last())
        .first()
    )


def record_cross_references(
    records: Iterable[dict],
    *,
    force: bool = False,
) -> int:
    """Persist resolved cross-reference edges from enrichment records.

    Idempotent per ``(source, target, relation)`` unique constraint; existing
    edges are refreshed with current confidence/evidence.
    """
    written = 0
    for rec in records:
        source = rec.get("chunk_id")
        if not source:
            continue
        for xr in rec.get("cross_references") or []:
            if xr.get("resolved") is not True or not xr.get("target_chunk_id"):
                continue
            target = xr["target_chunk_id"]
            relation = xr.get("relation", "REFERS_TO")
            row = (
                ChunkCrossReference.query.filter_by(
                    source_chunk_id=source, target_chunk_id=target, relation=relation
                ).first()
            )
            if row is None:
                row = ChunkCrossReference(
                    id=str(uuid.uuid4()),
                    source_chunk_id=source,
                    target_chunk_id=target,
                    relation=relation,
                    confidence=xr.get("confidence", 0.5),
                    evidence=xr.get("evidence"),
                    provenance=xr.get("source", "deterministic"),
                )
                db.session.add(row)
            else:
                row.confidence = xr.get("confidence", row.confidence)
                row.evidence = xr.get("evidence", row.evidence)
            written += 1
    return written


def record_resource_usage(
    run_id: str,
    batch_id: str | None,
    *,
    peak_ram_mb: float | None,
    avg_ram_mb: float | None,
    batch_size: int,
    processed: int,
    failed: int,
    retries: int,
    duration_s: float,
) -> None:
    db.session.add(
        ResourceUsage(
            run_id=run_id,
            batch_id=batch_id,
            peak_ram_mb=peak_ram_mb,
            avg_ram_mb=avg_ram_mb,
            batch_size=batch_size,
            processed=processed,
            failed=failed,
            retries=retries,
            duration_s=round(duration_s, 3),
            recorded_at=datetime.now(UTC),
        )
    )


def progress_summary() -> dict[str, Any]:
    """Aggregate enrichment status counts for reports/enrichment_progress.json."""
    from sqlalchemy import func

    rows = db.session.query(ChunkEnrichment.status, func.count()).group_by(ChunkEnrichment.status).all()
    statuses = {status: count for status, count in rows}
    resolved = ChunkCrossReference.query.count()
    checkpoints = EnrichmentCheckpoint.query.count()
    return {
        "enriched": statuses.get("ENRICHED", 0) + statuses.get("VALIDATED", 0),
        "validated": statuses.get("VALIDATED", 0),
        "failed": statuses.get("FAILED", 0),
        "pending": statuses.get("PENDING", 0),
        "processing": statuses.get("PROCESSING", 0),
        "skipped": statuses.get("SKIPPED", 0),
        "resolved_cross_references": resolved,
        "checkpoints": checkpoints,
        "generated_at": _now(),
    }


def resource_usage_summary() -> dict[str, Any]:
    """Aggregate resource telemetry for reports/resource_usage.json."""
    from sqlalchemy import func

    rows = (
        db.session.query(
            func.avg(ResourceUsage.peak_ram_mb),
            func.avg(ResourceUsage.avg_ram_mb),
            func.avg(ResourceUsage.duration_s),
            func.sum(ResourceUsage.processed),
            func.sum(ResourceUsage.failed),
            func.sum(ResourceUsage.retries),
            func.max(ResourceUsage.peak_ram_mb),
        ).first()
    )
    return {
        "avg_peak_ram_mb": round(rows[0], 2) if rows and rows[0] is not None else None,
        "avg_avg_ram_mb": round(rows[1], 2) if rows and rows[1] is not None else None,
        "avg_duration_s": round(rows[2], 2) if rows and rows[2] is not None else None,
        "total_processed": rows[3] if rows else 0,
        "total_failed": rows[4] if rows else 0,
        "total_retries": rows[5] if rows else 0,
        "peak_ram_mb": rows[6] if rows else None,
        "generated_at": _now(),
    }
