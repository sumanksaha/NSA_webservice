"""Tests for the RetrievalLogger and RetrievalAuditLog (Phase 1, Day 4).

Tests verify that retrieval events are persisted to ``rag_query_log`` and
that the hash-chained audit trail in ``AuditLog`` is correctly written and
verifiable.

Follows the DB-setup pattern from ``tests/test_ai_assistant.py``.
"""

from __future__ import annotations

from app.extensions import db
from app.models import AuditLog
from app.models.rag import RAGQueryLog
from app.rag.retrieval.logger import RetrievalAuditLog, RetrievalLogger
from app.rag.retrieval.result import RetrievedChunk, SearchResult


def _setup_test_env():
    from app import create_app
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    user = User(username="ragtest", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()
    return app, ctx, user


def _teardown(ctx):
    db.session.remove()
    db.drop_all()
    ctx.pop()


def _make_search_result() -> SearchResult:
    return SearchResult(
        query="Section 55 of FSS Act",
        query_type="section_lookup",
        chunks=[
            RetrievedChunk(
                chunk_id="c1",
                score=0.91,
                text="Section 55 deals with penalties for adulteration.",
                section_number="55",
                document_title="FSS Act 2006",
                document_type="Act",
                authority="FSSAI",
                chunk_index=0,
                hierarchy_level=1,
            ),
        ],
        total=1,
        latency_ms=45,
        source="hybrid",
    )


class TestRetrievalLogger:
    def test_log_creates_query_log_row(self):
        _app, ctx, _user = _setup_test_env()
        try:
            logger = RetrievalLogger()
            result = _make_search_result()
            log_entry = logger.log(query=result.query, query_type=result.query_type, result=result)
            assert log_entry is not None
            assert isinstance(log_entry, RAGQueryLog)
            assert log_entry.query == "Section 55 of FSS Act"
            assert log_entry.query_type == "section_lookup"
            assert log_entry.retrieved_chunk_ids == ["c1"]
            assert log_entry.retrieval_latency_ms == 45
        finally:
            _teardown(ctx)

    def test_log_content_hash_is_sha256(self):
        _app, ctx, _user = _setup_test_env()
        try:
            logger = RetrievalLogger()
            result = _make_search_result()
            log_entry = logger.log(query=result.query, query_type=result.query_type, result=result)
            assert len(log_entry.content_hash) == 64
            import hashlib

            expected = hashlib.sha256(b"Section 55 of FSS Act:").hexdigest()
            assert log_entry.content_hash == expected
        finally:
            _teardown(ctx)

    def test_log_empty_chunks(self):
        _app, ctx, _user = _setup_test_env()
        try:
            logger = RetrievalLogger()
            result = SearchResult(query="unknown", query_type="general_qa", chunks=[], total=0, latency_ms=10)
            log_entry = logger.log(query=result.query, query_type=result.query_type, result=result)
            assert log_entry is not None
            assert log_entry.retrieved_chunk_ids == []
        finally:
            _teardown(ctx)

    def test_log_with_error(self):
        _app, ctx, _user = _setup_test_env()
        try:
            logger = RetrievalLogger()
            result = SearchResult(
                query="bad query",
                query_type="general_qa",
                chunks=[],
                total=0,
                latency_ms=0,
                source="dense",
                error="Qdrant down",
            )
            log_entry = logger.log(query=result.query, query_type=result.query_type, result=result, error=result.error)
            assert log_entry.error == "Qdrant down"
        finally:
            _teardown(ctx)

    def test_log_best_effort_no_raise_on_db_error(self):
        _app, ctx, _user = _setup_test_env()
        try:
            logger = RetrievalLogger()
            result = _make_search_result()
            original = db.session.commit
            db.session.commit = lambda: (_ for _ in ()).throw(RuntimeError("DB down"))
            try:
                log_entry = logger.log(query="q", query_type="general_qa", result=result)
                assert log_entry is None
            finally:
                db.session.commit = original
        finally:
            _teardown(ctx)


class TestRetrievalAuditLog:
    def test_log_retrieval_creates_audit_entry(self):
        from app import create_app
        from app.models import FSO, User

        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        ctx = app.app_context()
        ctx.push()
        db.drop_all()
        db.create_all()
        try:
            user = User(username="ragtest2", password_hash="pbkdf2:sha256$test$dummy")
            db.session.add(user)
            db.session.add(FSO(fso_name="Test Officer"))
            db.session.commit()

            audit = RetrievalAuditLog(actor="rag_test")
            log = RAGQueryLog(query="test query", query_type="section_lookup", content_hash="abc123")
            db.session.add(log)
            db.session.commit()
            log_id = log.id

            ok = audit.log_retrieval(
                query_log_id=log_id,
                query="test query",
                query_type="section_lookup",
                chunk_ids=["c1", "c2"],
                latency_ms=50,
            )
            assert ok is True

            entry = AuditLog.query.filter_by(entity_type="rag_query").first()
            assert entry is not None
            assert entry.entity_id == log_id
            assert entry.action == "RETRIEVAL"
            assert entry.actor == "rag_test"
        finally:
            _teardown(ctx)

    def test_log_retrieval_with_error(self):
        from app import create_app
        from app.models import FSO, User

        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        ctx = app.app_context()
        ctx.push()
        db.drop_all()
        db.create_all()
        try:
            user = User(username="ragtest3", password_hash="pbkdf2:sha256$test$dummy")
            db.session.add(user)
            db.session.add(FSO(fso_name="Test Officer"))
            db.session.commit()

            audit = RetrievalAuditLog()
            ok = audit.log_retrieval(
                query_log_id="test-id",
                query="bad query",
                query_type="general_qa",
                chunk_ids=[],
                latency_ms=0,
                error="something went wrong",
            )
            assert ok is True
            entry = AuditLog.query.filter_by(entity_id="test-id").first()
            assert entry is not None
            import json

            details = json.loads(entry.details_json)
            assert details["error"] == "something went wrong"
        finally:
            _teardown(ctx)

    def test_log_retrieval_best_effort(self):
        from app import create_app
        from app.models import FSO, User

        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        ctx = app.app_context()
        ctx.push()
        db.drop_all()
        db.create_all()
        try:
            user = User(username="ragtest4", password_hash="pbkdf2:sha256$test$dummy")
            db.session.add(user)
            db.session.add(FSO(fso_name="Test Officer"))
            db.session.commit()

            import app.rag.retrieval.logger as logger_mod

            original = logger_mod.log_audit
            logger_mod.log_audit = lambda **kw: (_ for _ in ()).throw(RuntimeError("audit down"))
            try:
                audit = RetrievalAuditLog()
                ok = audit.log_retrieval(
                    query_log_id="x", query="q", query_type="general_qa", chunk_ids=[], latency_ms=1
                )
                assert ok is False
            finally:
                logger_mod.log_audit = original
        finally:
            _teardown(ctx)
