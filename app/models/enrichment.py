"""Enrichment store models (Phases 3/5/11/12 — incremental enrichment).

The enrichment record is persisted **beside** the immutable chunk payload:
``ChunkEnrichment`` holds the versioned v1.0 record (JSON) keyed by the
existing ``chunk_id``; ``EnrichmentCheckpoint`` makes batch processing
resumable; ``ChunkCrossReference`` holds resolved REFERS_TO edges;
``ResourceUsage`` tracks the 8 GB RAM budget and batch telemetry.

Design notes:
- All tables are additive: nothing here touches ``legal_chunk`` /
  ``legal_document`` or the Qdrant payload.
- ``original_sha256`` mirrors the payload ``content_hash`` so the record's
  ``original_text`` can be integrity-checked against the payload.
- ``status`` uses the task vocabulary: PENDING / PROCESSING / ENRICHED /
  VALIDATED / FAILED / SKIPPED.
- Indexes mirror the Alembic migration to avoid ``flask db migrate`` drift.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ChunkEnrichment(db.Model):
    """One enrichment record per existing chunk (additive, versioned)."""

    __tablename__ = "chunk_enrichment"

    chunk_id = db.Column(db.String(64), primary_key=True)  # payload chunk_id
    enrichment_version = db.Column(db.String(16), nullable=False, default="1.0")
    # Index declared explicitly (idx_enrichment_status) so model and migration
    # names match — no auto ``ix_chunk_enrichment_status`` drift.
    status = db.Column(db.String(16), nullable=False, default="PENDING")
    data = db.Column(db.JSON, nullable=False)  # the v1.0 record
    original_sha256 = db.Column(db.String(64), nullable=False)
    confidence = db.Column(db.Float, nullable=True)
    llm_used = db.Column(db.Boolean, default=False)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        db.Index("idx_enrichment_status", "status"),
        db.Index("idx_enrichment_version", "enrichment_version"),
    )


class EnrichmentCheckpoint(db.Model):
    """Per-batch checkpoint so a stopped run resumes from the last batch."""

    __tablename__ = "enrichment_checkpoint"

    batch_id = db.Column(db.String(64), primary_key=True)
    last_chunk_id = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(16), nullable=False, default="RUNNING")  # RUNNING|COMPLETE|FAILED
    processed = db.Column(db.Integer, default=0)
    enriched = db.Column(db.Integer, default=0)
    failed = db.Column(db.Integer, default=0)
    skipped = db.Column(db.Integer, default=0)
    batch_size = db.Column(db.Integer, nullable=False)
    started_at = db.Column(db.DateTime, default=_utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)


class ChunkCrossReference(db.Model):
    """Resolved REFERS_TO edge between chunks (Phase 6 first pass)."""

    __tablename__ = "chunk_cross_reference"

    id = db.Column(db.String(64), primary_key=True)
    source_chunk_id = db.Column(db.String(64), nullable=False, index=True)
    target_chunk_id = db.Column(db.String(64), nullable=False, index=True)
    relation = db.Column(db.String(32), nullable=False, default="REFERS_TO")
    confidence = db.Column(db.Float, nullable=False, default=0.5)
    evidence = db.Column(db.Text, nullable=True)
    provenance = db.Column(db.String(16), nullable=False, default="deterministic")
    created_at = db.Column(db.DateTime, default=_utcnow)

    __table_args__ = (
        db.Index("idx_xref_source_relation", "source_chunk_id", "relation"),
        db.UniqueConstraint("source_chunk_id", "target_chunk_id", "relation", name="uq_xref_edge"),
    )


class ResourceUsage(db.Model):
    """Per-batch memory / timing telemetry (Phase 10 budget)."""

    __tablename__ = "enrichment_resource_usage"

    id = db.Column(db.Integer, primary_key=True)
    # Index name matches the migration (idx_ru_run_id) — explicit, no drift.
    run_id = db.Column(db.String(64), nullable=False)
    batch_id = db.Column(db.String(64), nullable=True)
    peak_ram_mb = db.Column(db.Float, nullable=True)
    avg_ram_mb = db.Column(db.Float, nullable=True)
    batch_size = db.Column(db.Integer, nullable=True)
    processed = db.Column(db.Integer, nullable=True)
    failed = db.Column(db.Integer, nullable=True)
    retries = db.Column(db.Integer, default=0)
    duration_s = db.Column(db.Float, nullable=True)
    recorded_at = db.Column(db.DateTime, default=_utcnow)

    __table_args__ = (db.Index("idx_ru_run_id", "run_id"),)
