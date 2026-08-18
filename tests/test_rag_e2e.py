"""End-to-end RAG retrieval tests (Phase 1, Day 5).

Test the full Phase 1 pipeline: QueryClassifier -> HybridRetriever -> Reranker
-> RetrievalLogger, with Qdrant and sentence-transformers mocked so no
external services are required.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.extensions import db
from app.models.rag import RAGQueryLog
from app.rag.retrieval import HybridRetriever, QueryClassifier, QueryParser, Reranker
from app.rag.retrieval.dense_retriever import DenseRetriever
from app.rag.retrieval.logger import RetrievalLogger
from app.rag.retrieval.sparse_retriever import SparseRetriever


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
    user = User(username="e2euser", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()
    return app, ctx


def _teardown(ctx):
    db.session.remove()
    db.drop_all()
    ctx.pop()


def _mock_qdrant_point(cid, sid, score=0.9):
    return SimpleNamespace(
        id=cid,
        score=score,
        payload={
            "chunk_id": cid,
            "chunk_text": f"Content about Section {sid}.",
            "section_number": sid,
            "document_title": "FSS Act 2006",
            "document_type": "Act",
            "authority": "FSSAI",
            "chunk_index": 0,
            "hierarchy_level": 1,
            "parent_chunk_id": None,
        },
    )


def _build_pipeline():
    """Build a full Phase 1 pipeline with mocks."""
    encoder = SimpleNamespace(encode=lambda text: [0.5] * 768)
    points = [_mock_qdrant_point(f"dense_{i}", str(50 + i), score=0.95 - i * 0.05) for i in range(5)]
    client = SimpleNamespace(search=lambda **kw: points[: kw.get("limit", 10)])
    dense = DenseRetriever(collection_name="fssai_legal_768", client=client, encoder=encoder)

    corpus = {
        f"sparse_{i}": {
            "chunk_id": f"sparse_{i}",
            "text": f"Section {50 + i} of the FSS Act contains important provisions.",
            "document_title": "FSS Act 2006",
            "document_type": "Act",
            "authority": "FSSAI",
            "section_number": str(50 + i),
            "chunk_index": 0,
            "hierarchy_level": 1,
        }
        for i in range(5)
    }
    sparse = SparseRetriever(corpus=corpus)
    reranker = Reranker()
    hybrid = HybridRetriever(dense=dense, sparse=sparse, reranker=reranker)
    return hybrid, dense, sparse


class TestE2EPipeline:
    def test_full_pipeline_query_to_logger(self):
        _app, ctx = _setup_test_env()
        try:
            hybrid, _, _ = _build_pipeline()
            classifier = QueryClassifier()
            query_type = classifier.classify("What does Section 55 say?")
            assert query_type.value == "section_lookup"
            parser = QueryParser()
            parsed = parser.parse("What does Section 55 say?", query_type)
            assert parsed["section_number"] == "55"
            result = hybrid.retrieve("What does Section 55 say?", top_k=5)
            assert result.total > 0
            assert result.source == "hybrid"
            logger = RetrievalLogger()
            log_entry = logger.log(query=result.query, query_type=result.query_type, result=result)
            assert log_entry is not None
            assert isinstance(log_entry, RAGQueryLog)
            assert log_entry.query == "What does Section 55 say?"
        finally:
            _teardown(ctx)

    def test_pipeline_health_endpoint(self):
        """The RAG health endpoint should be public and return 200."""
        app, ctx = _setup_test_env()
        try:
            client = app.test_client()
            resp = client.get("/api/rag/health")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
        finally:
            _teardown(ctx)

    def test_pipeline_classification_smoke(self):
        """Smoke Test 1: query classification for all 5 types."""
        classifier = QueryClassifier()
        assert classifier.classify("What does Section 55 say?").value == "section_lookup"
        assert classifier.classify("Section 37 of the FSS Act").value == "section_lookup"
        assert classifier.classify("FSS Act amendments in 2023").value == "amendment_query"
        assert classifier.classify("Supreme Court ruling").value == "case_law"
        assert classifier.classify("Tell me about food safety").value == "general_qa"

    def test_pipeline_dense_retrieval_mock(self):
        """Smoke Test 2: mock Qdrant search returns top-5 chunks."""
        hybrid, _, _ = _build_pipeline()
        result = hybrid.dense.search("Section 55", top_k=5)
        assert result.total == 5
        assert all(c.section_number for c in result.chunks)

    def test_pipeline_hybrid_fusion(self):
        """Smoke Test 3: hybrid dense + sparse -> fused ranking."""
        hybrid, _, _ = _build_pipeline()
        result = hybrid.retrieve("Section 55 of FSS Act", top_k=10)
        assert result.total > 0
        assert result.source == "hybrid"

    def test_pipeline_reranker_applied(self):
        """Smoke Test 4: reranker is called during hybrid retrieval."""
        hybrid, _, _ = _build_pipeline()
        result = hybrid.retrieve("Section 55", top_k=10)
        assert result.total > 0
        assert all(c.score is not None for c in result.chunks)

    def test_pipeline_logger_persists(self):
        """Smoke Test 5: retrieval results persisted to rag_query_log."""
        _app, ctx = _setup_test_env()
        try:
            hybrid, _, _ = _build_pipeline()
            result = hybrid.retrieve("Section 55", top_k=5)
            logger = RetrievalLogger()
            log_entry = logger.log(query="Section 55", query_type="section_lookup", result=result)
            assert log_entry is not None
            found = db.session.query(RAGQueryLog).filter_by(query="Section 55").first()
            assert found is not None
        finally:
            _teardown(ctx)

    def test_pipeline_retrieval_audit_log(self):
        """Smoke Test 6: retrieval events are hash-chained in AuditLog."""
        from app.models import AuditLog
        from app.rag.retrieval.logger import RetrievalAuditLog
        from app.services.audit import verify_audit_chain

        _app, ctx = _setup_test_env()
        try:
            hybrid, _, _ = _build_pipeline()
            result = hybrid.retrieve("Section 55", top_k=5)
            logger = RetrievalLogger()
            log_entry = logger.log(query="Section 55", query_type="section_lookup", result=result)
            audit = RetrievalAuditLog()
            audit.log_retrieval(
                query_log_id=log_entry.id,
                query="Section 55",
                query_type="section_lookup",
                chunk_ids=[c.chunk_id for c in result.chunks],
                latency_ms=result.latency_ms,
            )
            entry = AuditLog.query.filter_by(entity_type="rag_query").first()
            assert entry is not None
            assert entry.curr_hash is not None
            assert entry.prev_hash is None
            assert verify_audit_chain(log_entry.id) is True
        finally:
            _teardown(ctx)

    def test_pipeline_retrieve_task(self):
        """Smoke Test 7: retrieve_task() runs the full pipeline."""
        from unittest import mock

        from app.rag.tasks import run_retrieval_pipeline

        _app, ctx = _setup_test_env()
        try:
            with (
                mock.patch.object(
                    DenseRetriever, "_get_encoder", return_value=SimpleNamespace(encode=lambda t: [0.5] * 768)
                ),
                mock.patch.object(
                    DenseRetriever,
                    "_get_client",
                    return_value=SimpleNamespace(
                        search=lambda **kw: [
                            _mock_qdrant_point(f"dense_{i}", str(50 + i), 0.95 - i * 0.05) for i in range(5)
                        ]
                    ),
                ),
            ):
                result = run_retrieval_pipeline("What does Section 55 say?", top_k=5, collection_name="fssai_legal_768")
                assert result["query_type"] == "section_lookup"
                assert "chunks" in result
                assert result["latency_ms"] >= 0
        finally:
            _teardown(ctx)
