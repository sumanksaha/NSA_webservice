"""Tests for the Agent A Phase 2 ingestion logger (app/rag/ingestion_logger.py).

Pins the observability contract: SHA-256 event fingerprints (QStash
make_dedup_key pattern), structured log emission, best-effort hash-chained
audit rows (never raises), and the ``IngestedDocumentResult`` adapter.
"""

from __future__ import annotations

import hashlib
import json
import logging

from app.rag.ingestion_logger import (
    EVENT_DUPLICATE,
    EVENT_FAILED,
    EVENT_INDEXED,
    IngestionEvent,
    IngestionLogger,
)

_CAPTURED: list[dict] = []


def _emit_sink(event_dict: dict) -> None:
    _CAPTURED.append(event_dict)


def _audit_sink(**kwargs) -> None:
    _CAPTURED.append(kwargs)


def _make_event(**overrides) -> IngestionEvent:
    defaults = {
        "document_id": "doc-1",
        "event": EVENT_INDEXED,
        "source_uri": "/corpus/fss_act.txt",
        "chunk_count": 12,
        "points_upserted": 12,
        "duration_ms": 340,
        "tokens_used": 0,
    }
    defaults.update(overrides)
    return IngestionEvent(**defaults)


def _make_result_dict(**overrides) -> dict:
    data = {
        "document_id": "doc-1",
        "source_uri": "/corpus/fss_act.txt",
        "file_type": "txt",
        "file_hash": "a" * 64,
        "text_chars": 1200,
        "chunk_count": 12,
        "duplicate_chunks": 0,
        "points_upserted": 12,
        "duplicate": False,
        "latency_ms": 340,
        "errors": [],
        "ok": True,
    }
    data.update(overrides)
    return data


class TestIngestionEvent:
    def test_fingerprint_is_deterministic_sha256(self):
        event = _make_event()
        assert event.fingerprint() == event.fingerprint()
        assert len(event.fingerprint()) == 64

    def test_fingerprint_changes_with_payload(self):
        assert _make_event(chunk_count=10).fingerprint() != _make_event(chunk_count=11).fingerprint()

    def test_fingerprint_matches_canonical_payload_hash(self):
        event = _make_event()
        canonical = json.dumps(event.to_dict(), sort_keys=True, default=str).encode("utf-8")
        assert event.fingerprint() == hashlib.sha256(canonical).hexdigest()

    def test_to_dict_is_json_serializable(self):
        json.loads(json.dumps(_make_event().to_dict()))


class TestIngestionLogger:
    def test_log_emits_structured_record(self):
        _CAPTURED.clear()
        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_audit_sink)
        ok = logger.log(_make_event())
        assert ok is True
        assert len(_CAPTURED) == 2
        record = _CAPTURED[0]
        assert record["document_id"] == "doc-1"
        assert record["event"] == EVENT_INDEXED
        assert record["chunk_count"] == 12
        assert record["duration_ms"] == 340

    def test_log_writes_audit_row(self):
        _CAPTURED.clear()
        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_audit_sink)
        logger.log(_make_event())
        audit = _CAPTURED[1]
        assert audit["entity_type"] == "rag_ingestion"
        assert audit["entity_id"] == "doc-1"
        assert audit["action"] == "INDEXED"
        assert audit["details"]["chunk_count"] == 12

    def test_log_best_effort_returns_false_on_audit_failure(self):
        def _broken_audit(**kwargs):
            raise RuntimeError("audit DB down")

        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_broken_audit)
        assert logger.log(_make_event()) is False  # never raises

    def test_log_unknown_entity_id_defaults(self):
        _CAPTURED.clear()
        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_audit_sink)
        logger.log(IngestionEvent(document_id="", event=EVENT_FAILED, error="boom"))
        assert _CAPTURED[1]["entity_id"] == "rag_unknown"


class TestLogIngestedResult:
    def test_indexed_result_maps_event(self):
        _CAPTURED.clear()
        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_audit_sink)
        event = logger.log_ingested_result(_make_result_dict())
        assert event.event == EVENT_INDEXED
        assert event.chunk_count == 12
        assert event.duration_ms == 340
        assert event.source_uri == "/corpus/fss_act.txt"
        assert _CAPTURED[1]["action"] == "INDEXED"

    def test_duplicate_result_maps_event(self):
        _CAPTURED.clear()
        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_audit_sink)
        event = logger.log_ingested_result(_make_result_dict(duplicate=True))
        assert event.event == EVENT_DUPLICATE

    def test_failed_result_maps_event_and_errors(self):
        _CAPTURED.clear()
        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_audit_sink)
        event = logger.log_ingested_result(_make_result_dict(ok=False, errors=["empty after cleaning"]))
        assert event.event == EVENT_FAILED
        assert "empty after cleaning" in event.error

    def test_duplicate_with_errors_surfaces_as_failed(self):
        """Errors win over the duplicate flag — a failed+duplicate result is FAILED."""
        _CAPTURED.clear()
        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_audit_sink)
        event = logger.log_ingested_result(
            _make_result_dict(duplicate=True, ok=False, errors=["reindex failed"])
        )
        assert event.event == EVENT_FAILED
        assert "reindex failed" in event.error

    def test_error_truncated_for_audit(self):
        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_audit_sink)
        long_error = "x" * 2000
        event = logger.log_ingested_result(_make_result_dict(ok=False, errors=[long_error]))
        assert len(event.error) == 500

    def test_accepts_object_with_to_dict(self):
        class _Result:
            def to_dict(self):
                return _make_result_dict()

        _CAPTURED.clear()
        logger = IngestionLogger(emit_fn=_emit_sink, audit_fn=_audit_sink)
        event = logger.log_ingested_result(_Result())
        assert event.event == EVENT_INDEXED


class TestRealAuditPath:
    """The default path (real log_audit) writes verifiable AuditLog rows."""

    def test_log_writes_verifiable_audit_chain(self):
        from app import create_app
        from app.extensions import db
        from app.models import AuditLog
        from app.models import FSO, User

        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        ctx = app.app_context()
        ctx.push()
        db.drop_all()
        db.create_all()
        try:
            user = User(username="ingest_log_test", password_hash="pbkdf2:sha256$test$dummy")  # noqa: S106
            db.session.add(user)
            db.session.add(FSO(fso_name="Test Officer"))
            db.session.commit()

            logger = IngestionLogger()  # default audit path
            first = logger.log(_make_event(event=EVENT_INDEXED, chunk_count=10))
            second = logger.log(_make_event(event=EVENT_FAILED, chunk_count=0, error="boom"))
            assert first is True and second is True

            rows = AuditLog.query.filter_by(entity_type="rag_ingestion", entity_id="doc-1").order_by(AuditLog.id.asc()).all()
            assert len(rows) == 2
            assert rows[0].action == "INDEXED"
            assert rows[1].action == "FAILED"
            # Hash chain: each row links to the previous.
            assert rows[0].prev_hash is None
            assert rows[1].prev_hash == rows[0].curr_hash

            from app.services.audit import verify_audit_chain

            assert verify_audit_chain("doc-1") is True
        finally:
            db.session.remove()
            db.drop_all()
            ctx.pop()
