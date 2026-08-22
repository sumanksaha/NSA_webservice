"""Tests for the FastAPI ASGI entry-point (``asgi.py``).

FastAPI coexists with Flask via ``WSGIMiddleware`` (FASTAPI_IMPLEMENTATION_PLAN.md, Ambition B):
- ``GET  /api/v2/health``          — standalone ASGI health probe (no Flask app context)
- ``POST /api/v2/rag/generate``   — FastAPI-native route delegating to ResilientRAGPipeline
- ``POST /api/v2/rag/retrieve``   — FastAPI-native route delegating to HybridRetriever
- ``POST /api/v2/rag/query/agent`` — FastAPI-native route delegating to the LangGraph agent

All heavy lifting stays in the existing Flask services; FastAPI only owns the
HTTP/transport layer for these endpoints.
"""

from __future__ import annotations

import importlib
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def asgi_app():
    """Import the ASGI app module (fastapi is an installed optional dep)."""
    return importlib.import_module("asgi")


@pytest.fixture(scope="module")
def client(asgi_app):
    return TestClient(asgi_app.app)


# --------------------------------------------------------------------------- #
# Skeleton structure
# --------------------------------------------------------------------------- #
class TestAsgiSkeleton:
    def test_module_imports(self, asgi_app):
        assert hasattr(asgi_app, "app")

    def test_app_is_fastapi(self, asgi_app):
        from fastapi import FastAPI

        assert isinstance(asgi_app.app, FastAPI)

    def test_flask_mounted(self, asgi_app):
        assert hasattr(asgi_app, "flask_app")

    def test_v2_router_registered(self, asgi_app):
        paths = [r.path for r in asgi_app.app.routes if hasattr(r, "path")]
        assert "/api/v2/health" in paths

    def test_rag_v2_router_registered(self, asgi_app):
        paths = [r.path for r in asgi_app.app.routes if hasattr(r, "path")]
        assert "/api/v2/rag/generate" in paths
        assert "/api/v2/rag/retrieve" in paths
        assert "/api/v2/rag/query/agent" in paths
        assert "/api/v2/rag/query/agent/resume" in paths

    def test_search_routes_registered(self, client):
        """Verify /api/v2/search and /api/v2/ai-assistant/assist are served."""
        # Missing query is legal (Flask parity): empty q → 200 with no results.
        r = client.get("/api/v2/search")
        assert r.status_code == 200
        assert r.json()["total"] == 0
        # Missing payload → 422 (route exists, validation fires).
        r = client.post("/api/v2/ai-assistant/assist", json={})
        assert r.status_code == 422

    def test_lifespan_callable(self, asgi_app):
        assert getattr(asgi_app.app, "router", None) is not None


# --------------------------------------------------------------------------- #
# /api/v2/health
# --------------------------------------------------------------------------- #
class TestAsgiHealth:
    def test_health_returns_200(self, client):
        r = client.get("/api/v2/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["fastapi"] is True

    def test_health_has_cors(self, client):
        r = client.get("/api/v2/health", headers={"Origin": "https://example.com"})
        assert r.headers.get("access-control-allow-origin") is not None


# --------------------------------------------------------------------------- #
# /api/v2/rag/generate — resilient pipeline delegation
# --------------------------------------------------------------------------- #
class TestAsgiRagGenerate:
    def test_generate_missing_query(self, client):
        r = client.post("/api/v2/rag/generate", json={})
        assert r.status_code == 422

    def test_generate_stub_mode(self, asgi_app, monkeypatch):
        """Delegates to the resilient pipeline with a stub (no Qdrant)."""
        from app.rag.resilient import ResilientRAGPipeline

        def stub_pipeline(query, **kw):
            return {
                "query": query,
                "answer": "stub answer",
                "citations": [],
                "retrieved_chunks": [],
                "groundedness_score": 0.5,
                "hallucination_detected": False,
            }

        # Inject stub pipeline via asgi.get_rag_pipeline (Phase 2 wiring).
        monkeypatch.setattr(
            asgi_app,
            "get_rag_pipeline",
            lambda: ResilientRAGPipeline(
                pipeline_fn=stub_pipeline,
                fallback_fn=lambda q, **kw: stub_pipeline(q, **kw),
            ),
        )
        c = TestClient(asgi_app.app)
        resp = c.post(
            "/api/v2/rag/generate",
            json={"query": "Section 50 of FSS Act penalty"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "answer" in body
        assert "citations" in body


# --------------------------------------------------------------------------- #
# /api/v2/rag/retrieve — live hybrid retrieval wiring
# --------------------------------------------------------------------------- #
class TestAsgiRagRetrieve:
    def test_retrieve_rag_disabled(self, client, monkeypatch):
        """When RAG_ENABLED is false, returns 503 without touching Qdrant."""
        monkeypatch.setenv("RAG_ENABLED", "false")
        resp = client.post("/api/v2/rag/retrieve", json={"query": "penalty"})
        assert resp.status_code == 503
        assert "disabled" in resp.json()["error"]

    def test_retrieve_missing_query(self, client):
        r = client.post("/api/v2/rag/retrieve", json={})
        assert r.status_code == 422

    def test_retrieve_stub_mode(self, client, monkeypatch):
        import app.rag.retrieval.hybrid_retriever as hybrid_mod
        from app.rag.retrieval.result import RetrievedChunk, SearchResult

        chunks = [RetrievedChunk(chunk_id=f"c{i}", score=0.9 - i * 0.1, text=f"chunk {i}") for i in range(3)]
        stub_result = SearchResult(chunks=chunks, total=3, query="penalty", query_type="general")

        class _StubRetriever:
            def retrieve(self, query, **kw):
                return stub_result

        monkeypatch.setenv("RAG_ENABLED", "true")
        monkeypatch.setattr(hybrid_mod, "HybridRetriever", lambda **kw: _StubRetriever())
        resp = client.post(
            "/api/v2/rag/retrieve",
            json={"query": "penalty", "collection_name": "criminal_legal_768"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["chunks"]) == 3


# --------------------------------------------------------------------------- #
# /api/v2/rag/query/agent — LangGraph agent route (M3, M4, M5)
# --------------------------------------------------------------------------- #
class TestAsgiAgentRoute:
    def test_agent_legacy_path(self, asgi_app, monkeypatch):
        """When RAG_USE_AGENT_PIPELINE=false (default), delegates to resilient pipeline."""
        from app.rag.resilient import ResilientRAGPipeline

        def stub_pipeline(query, **kw):
            return {
                "query": query,
                "answer": "legacy answer",
                "citations": [],
                "retrieved_chunks": [],
                "groundedness_score": 0.9,
            }

        monkeypatch.setattr(
            asgi_app,
            "get_rag_pipeline",
            lambda: ResilientRAGPipeline(
                pipeline_fn=stub_pipeline,
                fallback_fn=lambda q, **kw: stub_pipeline(q, **kw),
            ),
        )
        monkeypatch.setattr(asgi_app, "get_flag", lambda key: False)
        c = TestClient(asgi_app.app)
        resp = c.post("/api/v2/rag/query/agent", json={"query": "penalty"})
        assert resp.status_code == 200
        assert resp.json()["answer"] == "legacy answer"

    def test_agent_path_with_stub(self, asgi_app, monkeypatch):
        """When RAG_USE_AGENT_PIPELINE=true, runs the agent graph (stub)."""

        def fake_run_agent(state, **kw):
            return {"response": {"answer": "agent answer", "citations": []}}

        monkeypatch.setenv("RAG_USE_AGENT_PIPELINE", "true")
        monkeypatch.setattr("app.rag.agent.graph.run_agent", fake_run_agent)
        c = TestClient(asgi_app.app)
        resp = c.post("/api/v2/rag/query/agent", json={"query": "penalty"})
        assert resp.status_code == 200
        assert resp.json()["answer"] == "agent answer"

    def test_agent_hitl_awaiting_review(self, asgi_app, monkeypatch):
        """When RAG_AGENT_HITL=true and graph pauses, returns 202 awaiting_review."""

        class _FakeInterrupt:
            value: ClassVar[dict] = {"reason": "needs human review"}

        def fake_run_agent(state, **kw):
            return {"__interrupt__": [_FakeInterrupt()]}

        monkeypatch.setenv("RAG_USE_AGENT_PIPELINE", "true")
        monkeypatch.setenv("RAG_AGENT_HITL", "true")
        monkeypatch.setattr("app.rag.agent.graph.run_agent", fake_run_agent)
        c = TestClient(asgi_app.app)
        resp = c.post(
            "/api/v2/rag/query/agent",
            json={"query": "penalty", "thread_id": "tid-123"},
        )
        # 202 matches the Flask route's awaiting_review contract.
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "awaiting_review"
        assert body["thread_id"] == "tid-123"

    def test_resume_without_hitl(self, client, monkeypatch):
        """Resume endpoint returns 400 when RAG_AGENT_HITL is false."""
        monkeypatch.setenv("RAG_AGENT_HITL", "false")
        resp = client.post(
            "/api/v2/rag/query/agent/resume",
            json={"thread_id": "tid"},
        )
        assert resp.status_code == 400
        assert "RAG_AGENT_HITL" in resp.json()["error"]

    def test_resume_with_hitl(self, client, monkeypatch):
        """Resume endpoint with RAG_AGENT_HITL=true calls resume_agent."""

        def fake_resume(thread_id, approved):
            return {"response": {"answer": "resumed answer"}}

        monkeypatch.setenv("RAG_AGENT_HITL", "true")
        monkeypatch.setattr("app.rag.agent.graph.resume_agent", fake_resume)
        resp = client.post(
            "/api/v2/rag/query/agent/resume",
            json={"thread_id": "tid-123"},
        )
        assert resp.status_code == 200
        assert resp.json()["answer"] == "resumed answer"

    def test_resume_passes_approved_false_through(self, client, monkeypatch):
        """The human's rejection must reach resume_agent (regression: v2 used
        to hardcode approved=True, silently defeating rejections)."""
        captured: dict = {}

        def fake_resume(thread_id, approved):
            captured["thread_id"] = thread_id
            captured["approved"] = approved
            return {"response": {"answer": "regenerated after rejection"}}

        monkeypatch.setenv("RAG_AGENT_HITL", "true")
        monkeypatch.setattr("app.rag.agent.graph.resume_agent", fake_resume)
        resp = client.post(
            "/api/v2/rag/query/agent/resume",
            json={"thread_id": "tid-reject", "approved": False},
        )
        assert resp.status_code == 200
        assert captured == {"thread_id": "tid-reject", "approved": False}

    def test_resume_reinterrupt_returns_202(self, client, monkeypatch):
        """A re-interrupt after resume returns 202 awaiting_review (Flask parity)."""

        class _FakeInterrupt:
            value: ClassVar[dict] = {"reason": "still not grounded"}

        def fake_resume(thread_id, approved):
            return {"__interrupt__": [_FakeInterrupt()]}

        monkeypatch.setenv("RAG_AGENT_HITL", "true")
        monkeypatch.setattr("app.rag.agent.graph.resume_agent", fake_resume)
        resp = client.post(
            "/api/v2/rag/query/agent/resume",
            json={"thread_id": "tid-again", "approved": True},
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "awaiting_review"

    def test_resume_blank_thread_id_400(self, client, monkeypatch):
        """Blank thread_id → 400 (mirrors the Flask validation)."""
        monkeypatch.setenv("RAG_AGENT_HITL", "true")
        resp = client.post(
            "/api/v2/rag/query/agent/resume",
            json={"thread_id": "   ", "approved": True},
        )
        assert resp.status_code == 400
        assert "thread_id" in resp.json()["error"]


# --------------------------------------------------------------------------- #
# /api/v2/search — mirrors Flask search.api_search
# --------------------------------------------------------------------------- #
class TestAsgiSearch:
    def test_search_missing_query(self, client):
        """Missing query is legal (Flask parity): empty q → 200, no results."""
        r = client.get("/api/v2/search")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_search_invalid_entity_type(self, client):
        r = client.get("/api/v2/search?q=test&type=invalid_type")
        assert r.status_code == 400
        assert "Invalid entity type" in r.json()["error"]

    def test_search_stub_mode(self, client, monkeypatch):
        """Search via app.search.indexer.search with injected stub."""

        def fake_search(q, entity_type=None, limit=20, fuzzy=False):
            return [{"id": "1", "title": "Found doc", "snippet": "...", "score": 0.95}]

        monkeypatch.setattr("app.search.indexer.search", fake_search)
        resp = client.get("/api/v2/search?q=penalty&type=case_file&limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "penalty"
        assert body["total"] == 1
        assert body["results"][0]["title"] == "Found doc"

    def test_search_reindex_has_no_rag_gate(self, client, monkeypatch):
        """Reindexing is a search concern — RAG_ENABLED must NOT gate it
        (regression: v2 used to 503 on a false RAG flag; Flask never did)."""

        def fake_index_all():
            return 7

        monkeypatch.setenv("RAG_ENABLED", "false")  # hostile env: gate must not fire
        monkeypatch.setattr("app.search.indexer.index_all", fake_index_all)
        resp = client.post("/api/v2/search/reindex")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "records_indexed": 7}


# --------------------------------------------------------------------------- #
# /api/v2/ai-assistant/assist — mirrors Flask ai_bp.assist
# --------------------------------------------------------------------------- #
class TestAsgiAiAssistant:
    def test_assist_missing_action(self, client):
        r = client.post("/api/v2/ai-assistant/assist", json={"content": "text"})
        assert r.status_code == 422

    def test_assist_invalid_action(self, client):
        r = client.post(
            "/api/v2/ai-assistant/assist",
            json={"action": "bogus", "content": "text"},
        )
        assert r.status_code == 400
        assert "Invalid action" in r.json()["error"]

    def test_assist_ai_disabled(self, client, monkeypatch):
        """Returns 503 when AI service is not enabled."""

        class _StubService:
            def is_enabled(self):
                return False

        monkeypatch.setattr(
            "app.plugins.registry.PluginRegistry.get_instance",
            lambda: type("PR", (), {"get_active": lambda self, k: _StubService()})(),
        )
        resp = client.post(
            "/api/v2/ai-assistant/assist",
            json={"action": "summarize", "content": "sample text"},
        )
        assert resp.status_code == 503
        assert "not configured" in resp.json()["error"]

    def test_assist_stub_mode(self, client, monkeypatch):
        """Successful AI dispatch via plugin registry."""

        class _StubService:
            def is_enabled(self):
                return True

            def summarize_text(self, content, **kw):
                return f"Summary of {len(content)} chars"

            tokens_used = 42

        monkeypatch.setattr(
            "app.plugins.registry.PluginRegistry.get_instance",
            lambda: type("PR", (), {"get_active": lambda self, k: _StubService()})(),
        )
        resp = client.post(
            "/api/v2/ai-assistant/assist",
            json={"action": "summarize", "content": "sample text"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "summarize"
        assert body["tokens_used"] == 42
        assert "Summary of" in body["result"]


# --------------------------------------------------------------------------- #
# Security headers + API-key auth (Phase 4)
# --------------------------------------------------------------------------- #
class TestAsgiSecurity:
    def test_health_has_security_headers(self, client):
        """/api/v2/* responses must include security headers."""
        r = client.get("/api/v2/health")
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_api_key_rejects_without_header(self, client, monkeypatch):
        """When API_V2_KEY is set, requests without x-api-key get 401."""
        monkeypatch.setenv("API_V2_KEY", "secret123")
        try:
            resp = client.get("/api/v2/health", headers={"Origin": "https://x.com"})
            assert resp.status_code == 401
            assert "x-api-key" in resp.json()["error"]
        finally:
            monkeypatch.delenv("API_V2_KEY", raising=False)

    def test_api_key_accepts_with_valid_header(self, client, monkeypatch):
        """When API_V2_KEY is set, correct header passes through."""
        monkeypatch.setenv("API_V2_KEY", "secret123")
        try:
            resp = client.get("/api/v2/health", headers={"x-api-key": "secret123", "Origin": "https://x.com"})
            assert resp.status_code == 200
        finally:
            monkeypatch.delenv("API_V2_KEY", raising=False)

    def test_api_key_open_when_unset(self, client, monkeypatch):
        """When API_V2_KEY is unset (dev), routes are open."""
        monkeypatch.delenv("API_V2_KEY", raising=False)
        resp = client.get("/api/v2/health")
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# /api/v2/bill/lookup-fbo-issues — mirrors Flask bill_generator.lookup_fbo_issues
# --------------------------------------------------------------------------- #
class TestAsgiBillLookup:
    def test_lookup_missing_params(self, client):
        r = client.get("/api/v2/bill/lookup-fbo-issues")
        assert r.status_code == 400
        assert "fbo_id or issue_id" in r.json()["error"]

    def test_lookup_stub_mode(self, client):
        """Bill lookup via get_db with injected stub session."""
        import asgi

        class _StubIssue:
            id = 1
            fbo_id = "fbo-001"
            manufacturer_fbo_id = None
            fbo_name = "Test FBO"
            source_type = "inspection"
            state = "open"
            fso_name = "Test Officer"
            created_at = "2024-01-01T00:00:00"
            detail_json = '{"violation": "section 50"}'

        class _StubQuery:
            def filter(self, *a, **kw):
                return self

            def order_by(self, *a, **kw):
                return self

            def all(self):
                return [_StubIssue()]

        class _StubSession:
            def query(self, model):
                return _StubQuery()

        from app.api.deps import get_db

        app = asgi.app
        orig = app.dependency_overrides.get(get_db)
        app.dependency_overrides[get_db] = lambda: _StubSession()
        try:
            resp = client.get("/api/v2/bill/lookup-fbo-issues?fbo_id=fbo-001")
            assert resp.status_code == 200
            body = resp.json()
            assert len(body) == 1
            assert body[0]["fbo_name"] == "Test FBO"
            assert body[0]["state"] == "open"
        finally:
            if orig is not None:
                app.dependency_overrides[get_db] = orig
            else:
                app.dependency_overrides.pop(get_db, None)


# --------------------------------------------------------------------------- #
# Phase 5: Cut-over parity — Flask routes still served via WSGIMiddleware
# --------------------------------------------------------------------------- #
class TestAsgiFlaskParity:
    """Verify Flask routes are still accessible through the ASGI gateway."""

    def test_flask_health_via_asgi(self, client):
        """Flask's /health endpoint still served via WSGIMiddleware mount at /."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_flask_rag_health_via_asgi(self, client):
        """Flask RAG routes (/api/rag/*) still served via WSGIMiddleware."""
        resp = client.get("/api/rag/health")
        assert resp.status_code in (200, 308)

    def test_no_route_collision(self, client):
        """/api/v2/* → FastAPI, /api/rag/* → Flask (no conflicts)."""
        # FastAPI route
        assert client.get("/api/v2/health").status_code == 200
        # Flask route (may 301/308 redirect if trailing slash differs)
        r = client.get("/api/rag/health")
        assert r.status_code in (200, 301, 308)


# --------------------------------------------------------------------------- #
# /api/v2/rag/eval + /api/v2/validation/validate — Phase 6-incremental ports
# --------------------------------------------------------------------------- #
class TestAsgiRagEval:
    def test_eval_missing_dataset(self, client):
        r = client.post("/api/v2/rag/eval", json={})
        assert r.status_code == 422

    def test_eval_empty_dataset(self, client):
        r = client.post("/api/v2/rag/eval", json={"dataset": []})
        assert r.status_code == 422

    def test_eval_rag_disabled(self, client, monkeypatch):
        monkeypatch.setattr("app.api.routers.get_flag", lambda key: False)
        r = client.post(
            "/api/v2/rag/eval",
            json={"dataset": [{"query": "test"}]},
        )
        assert r.status_code == 503
        assert "disabled" in r.json()["error"]

    def test_eval_stub_mode(self, client, monkeypatch):
        """Eval delegates to run_evaluate with stubbed function."""
        monkeypatch.setattr("app.api.routers.get_flag", lambda key: True)

        def fake_run_evaluate(dataset, eval_run_id=None, top_k=10):
            return {
                "eval_run_id": eval_run_id or "abc",
                "metrics": {"mrr": 0.85, "recall@10": 0.92},
                "total_queries": len(dataset),
                "results": [{"query": d["query"], "answer": "stub"} for d in dataset],
            }

        monkeypatch.setattr("app.rag.tasks.run_evaluate", fake_run_evaluate)
        resp = client.post(
            "/api/v2/rag/eval",
            json={"dataset": [{"query": "Section 50 penalty"}, {"query": "Section 51"}], "top_k": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_queries"] == 2
        assert body["metrics"]["mrr"] == 0.85


class TestAsgiValidation:
    def test_validate_missing_fields(self, client):
        r = client.post("/api/v2/validation/validate", json={})
        assert r.status_code == 422

    def test_validate_invalid_case_type(self, client):
        r = client.post(
            "/api/v2/validation/validate",
            json={"case_id": 1, "case_type": "bogus"},
        )
        assert r.status_code == 400
        assert "case_type" in r.json()["error"]

    def test_validate_stub_mode(self, client, monkeypatch):
        """Validation delegates to ValidationEngine.validate_case (stub)."""

        class _StubEngine:
            def validate_case(self, case_id, case_type=None):
                return {"valid": True, "issues": [], "case_id": case_id}

        monkeypatch.setattr("app.validation.engine.ValidationEngine", _StubEngine)
        resp = client.post(
            "/api/v2/validation/validate",
            json={"case_id": 42, "case_type": "case_file"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["case_id"] == 42


# --------------------------------------------------------------------------- #
# /api/v2/rag/ingest + /api/v2/rag/ingest/corpus — Phase 4 Core-API port
# --------------------------------------------------------------------------- #
class TestAsgiRagIngest:
    def test_ingest_missing_text_and_source(self, client, monkeypatch):
        monkeypatch.setattr("app.api.routers.get_flag", lambda key: True)
        r = client.post("/api/v2/rag/ingest", json={})
        assert r.status_code == 400
        assert "text" in r.json()["error"]

    def test_ingest_rag_disabled(self, client, monkeypatch):
        """When RAG is disabled via flag, returns 503."""
        monkeypatch.setattr("app.api.routers.get_flag", lambda key: False)
        r = client.post("/api/v2/rag/ingest", json={"text": "Section 50 of FSS Act"})
        assert r.status_code == 503

    def test_ingest_stub_mode(self, client, monkeypatch):
        """Ingest delegates to run_ingest_document with stubbed pipeline."""
        monkeypatch.setattr("app.api.routers.get_flag", lambda key: True)

        def fake_run_ingest_document(source, document=None, pipeline=None):
            return {"ok": True, "chunks_indexed": 3, "content_sha256": "abc123"}

        class _StubPipeline:
            pass

        monkeypatch.setattr("app.rag.ingestion.make_ingestion_pipeline", lambda **kw: _StubPipeline())
        monkeypatch.setattr("app.rag.ingestion.run_ingest_document", fake_run_ingest_document)
        resp = client.post(
            "/api/v2/rag/ingest",
            json={"text": "Section 50 of FSS Act deals with penalties."},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["chunks_indexed"] == 3

    def test_ingest_source_takes_precedence(self, client, monkeypatch):
        """When both text and source provided, source wins."""
        monkeypatch.setattr("app.api.routers.get_flag", lambda key: True)
        seen_source = {}

        class _StubPipeline:
            pass

        def fake_run_ingest_document(source, document=None, pipeline=None):
            seen_source["source"] = source
            return {"ok": True, "chunks_indexed": 1}

        monkeypatch.setattr("app.rag.ingestion.make_ingestion_pipeline", lambda **kw: _StubPipeline())
        monkeypatch.setattr("app.rag.ingestion.run_ingest_document", fake_run_ingest_document)
        client.post(
            "/api/v2/rag/ingest",
            json={"text": "ignored", "source": "/corpus/fss_act.pdf"},
        )
        assert seen_source["source"] == "/corpus/fss_act.pdf"


class TestAsgiRagIngestCorpus:
    def test_ingest_corpus_missing_dir(self, client, monkeypatch):
        monkeypatch.setattr("app.api.routers.get_flag", lambda key: True)
        r = client.post("/api/v2/rag/ingest/corpus", json={})
        assert r.status_code == 422

    def test_ingest_corpus_disabled(self, client, monkeypatch):
        monkeypatch.setattr("app.api.routers.get_flag", lambda key: False)
        r = client.post(
            "/api/v2/rag/ingest/corpus",
            json={"corpus_dir": "/nonexistent"},
        )
        assert r.status_code == 503

    def test_ingest_corpus_stub_mode(self, client, monkeypatch):
        """Corpus ingest delegates to ingest_corpus_dir with stubbed pipeline."""
        monkeypatch.setattr("app.api.routers.get_flag", lambda key: True)
        seen_dir = {}

        class _StubPipeline:
            pass

        def fake_ingest_corpus_dir(corpus_dir, document=None, pipeline=None):
            seen_dir["dir"] = corpus_dir
            return {"total": 3, "indexed": 2, "duplicates": 1, "failed": 0, "results": []}

        monkeypatch.setattr("app.rag.ingestion.make_ingestion_pipeline", lambda **kw: _StubPipeline())
        monkeypatch.setattr("app.rag.ingestion.ingest_corpus_dir", fake_ingest_corpus_dir)
        resp = client.post(
            "/api/v2/rag/ingest/corpus",
            json={"corpus_dir": "/corpus/fss"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert body["indexed"] == 2
        assert seen_dir["dir"] == "/corpus/fss"
