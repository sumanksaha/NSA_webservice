"""RAG ingestion observability (Agent A, Phase 2 — Day 8, §3.5).

``IngestionLogger`` records ingestion events (document_id, chunk_count,
duration, tokens, errors) in two places:

1. **Structured log lines** — every event is emitted via the stdlib
   ``logging`` module as a JSON-serializable record (``IngestionEvent``).
2. **Hash-chained audit trail** — best-effort ``AuditLog`` rows via
   :func:`app.services.audit.log_audit` (``entity_type="rag_ingestion"``),
   mirroring ``RetrievalAuditLog`` so the whole RAG pipeline (ingest →
   retrieve → generate) shares one tamper-evident chain per ``document_id``.

``IngestionEvent.fingerprint`` follows the QStash ``make_dedup_key`` pattern
(SHA-256 over the canonical JSON payload) so identical events collapse for
trend/dedup analysis, and the logger is **best-effort** — a DB or logging
failure never breaks ingestion (``log`` returns ``False`` instead of raising).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Terminal event names recorded by the logger.
EVENT_STARTED = "started"
EVENT_INDEXED = "indexed"
EVENT_DUPLICATE = "duplicate"
EVENT_FAILED = "failed"


@dataclass
class IngestionEvent:
    """One ingestion event — JSON-serializable observability record."""

    document_id: str
    event: str  # started | indexed | duplicate | failed
    source_uri: str = ""
    chunk_count: int = 0
    points_upserted: int = 0
    duration_ms: int = 0
    tokens_used: int = 0
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Fingerprint (QStash make_dedup_key pattern — SHA-256 of the payload)
    # ------------------------------------------------------------------ #

    def fingerprint(self) -> str:
        """SHA-256 hex digest over the canonical JSON payload.

        Two identical events (same doc, same result) produce the same
        fingerprint, enabling dedup / trend analysis; any field change
        (e.g. a real re-ingestion with different chunk counts) changes it.
        """
        payload = json.dumps(self.to_dict(), sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "event": self.event,
            "source_uri": self.source_uri,
            "chunk_count": self.chunk_count,
            "points_upserted": self.points_upserted,
            "duration_ms": self.duration_ms,
            "tokens_used": self.tokens_used,
            "error": self.error,
            "extra": dict(self.extra),
        }


class IngestionLogger:
    """Record ingestion events to logs + the hash-chained audit trail.

    Never raises: every persistence path is guarded (best-effort) so a
    Redis/DB hiccup cannot break ingestion.  Injected ``audit_fn`` and
    ``emit_fn`` are test seams mirroring the mock-injection pattern.
    """

    def __init__(
        self,
        *,
        actor: str = "rag_ingestion",
        audit_fn: Any | None = None,
        emit_fn: Any | None = None,
    ) -> None:
        self.actor = actor
        self._audit_fn = audit_fn  # None -> log_audit (lazy)
        self._emit_fn = emit_fn  # None -> logger.info (structured line)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def log(self, event: IngestionEvent) -> bool:
        """Record one ingestion event. Returns ``True`` on full success."""
        ok = True

        # 1. Structured log line (never fails).
        self._emit(event)

        # 2. Best-effort hash-chained audit row.
        try:
            self._audit(event)
        except Exception as exc:  # noqa: BLE001 - best-effort observability
            logger.warning("IngestionLogger audit failed (best-effort): %s", exc)
            ok = False
        return ok

    def log_ingested_result(self, result: Any) -> IngestionEvent:
        """Adapt an ``IngestedDocumentResult`` (or its ``to_dict``) into an event and log it.

        Returns the created :class:`IngestionEvent` so callers can inspect
        the fingerprint without re-constructing it.
        """
        data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        errors = data.get("errors") or []
        # Errors always win (a failed+duplicate result must surface as FAILED);
        # otherwise a duplicate is DUPLICATE and everything else is INDEXED.
        if errors:
            event_name = EVENT_FAILED
        elif data.get("duplicate"):
            event_name = EVENT_DUPLICATE
        else:
            event_name = EVENT_INDEXED
        event = IngestionEvent(
            document_id=str(data.get("document_id") or ""),
            event=event_name,
            source_uri=str(data.get("source_uri") or ""),
            chunk_count=int(data.get("chunk_count") or 0),
            points_upserted=int(data.get("points_upserted") or 0),
            duration_ms=int(data.get("latency_ms") or 0),
            # Truncate for audit storage (mirrors RetrievalLogger's 500-char cap).
            error="; ".join(str(e) for e in errors)[:500],
        )
        self.log(event)
        return event

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _emit(self, event: IngestionEvent) -> None:
        if self._emit_fn is not None:
            self._emit_fn(event.to_dict())
            return
        logger.info(
            "ingestion_event document_id=%s event=%s chunks=%d points=%d duration_ms=%d tokens=%d error=%s",
            event.document_id,
            event.event,
            event.chunk_count,
            event.points_upserted,
            event.duration_ms,
            event.tokens_used,
            event.error or "-",
        )

    def _audit(self, event: IngestionEvent) -> None:
        if self._audit_fn is not None:
            self._audit_fn(
                entity_type="rag_ingestion",
                entity_id=event.document_id or "rag_unknown",
                action=event.event.upper(),
                actor=self.actor,
                details=event.to_dict(),
            )
            return
        from app.services.audit import log_audit

        log_audit(
            entity_type="rag_ingestion",
            entity_id=event.document_id or "rag_unknown",
            action=event.event.upper(),
            actor=self.actor,
            details=event.to_dict(),
        )


# End of ingestion_logger.py
