"""Tests for the Phase 5 RAG ingestion API (app/rag/routes.py).

Covers the ``/api/rag/*`` blueprint: the public health probe, auth-gated
``/ingest`` + ``/ingest/corpus`` endpoints, RAG-disabled 503s, payload
validation 400s, file-not-found 404, and delegation to the plain ingestion
entry points (monkeypatched so no Qdrant/sentence-transformers required).
One real-path test exercises graceful degradation (no optional deps ->
result dict with errors, not an exception).

Follows the test pattern from tests/test_ai_assistant.py / test_rag_e2e.py:
_create_app() builds the app with in-memory SQLite + db.create_all(), seeds
User/FSO, and authenticates via session_transaction().
"""

from __future__ import annotations


def _setup_test_env():
    """Create a test app with in-memory SQLite, a user, and an FSO.

    Returns (app, client, app_context). The client is pre-authenticated.
    """
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="raguser", password_hash="pbkdf2:sha256$test$dummy")  # noqa: S106
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)

    return app, client, app_context


def _setup_unauthenticated_client():
    """Create a test app/client without authentication (302 redirect tests)."""
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="raguser", password_hash="pbkdf2:sha256$test$dummy")  # noqa: S106
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()  # No session_transaction — unauthenticated
    return app, client, app_context


class TestHealthRoute:
    def test_health_is_public_and_ok(self):
        app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/health")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
        finally:
            ctx.pop()


class TestIngestRoute:
    def test_route_registered(self):
        app, client, ctx = _setup_test_env()
        try:
            rules = [str(r) for r in app.url_map.iter_rules()]
            assert any("api/rag/ingest" in r for r in rules)
        finally:
            ctx.pop()

    def test_unauthenticated_redirects(self):
        app, unauth_client, ctx = _setup_unauthenticated_client()
        try:
            resp = unauth_client.post(
                "/api/rag/ingest",
                json={"text": "The Food Safety and Standards Act, 2006"},
                follow_redirects=False,
            )
            assert resp.status_code in (302, 303)
        finally:
            ctx.pop()

    def test_disabled_returns_503(self):
        app, client, ctx = _setup_test_env()
        try:
            app.config["RAG_ENABLED"] = False
            resp = client.post("/api/rag/ingest", json={"text": "some text"})
            assert resp.status_code == 503
            assert "error" in resp.get_json()
        finally:
            ctx.pop()

    def test_non_dict_payload_returns_400(self):
        app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/api/rag/ingest", json="not a dict")
            assert resp.status_code == 400
        finally:
            ctx.pop()

    def test_missing_text_and_source_returns_400(self):
        app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/api/rag/ingest", json={})
            assert resp.status_code == 400
            assert "text" in resp.get_json()["error"]
        finally:
            ctx.pop()

    def test_invalid_full_enrichment_returns_400(self):
        app, client, ctx = _setup_test_env()
        try:
            resp = client.post(
                "/api/rag/ingest",
                json={"text": "some text", "full_enrichment": "yes"},
            )
            assert resp.status_code == 400
        finally:
            ctx.pop()

    def test_non_dict_document_returns_400(self):
        app, client, ctx = _setup_test_env()
        try:
            resp = client.post(
                "/api/rag/ingest",
                json={"text": "some text", "document": "not-a-dict"},
            )
            assert resp.status_code == 400
        finally:
            ctx.pop()

    def test_file_not_found_returns_404(self):
        app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/api/rag/ingest", json={"source": "/no/such/file.pdf"})
            assert resp.status_code == 404
            assert "not found" in resp.get_json()["error"].lower()
        finally:
            ctx.pop()

    def test_successful_text_ingest_returns_result(self, monkeypatch):
        app, client, ctx = _setup_test_env()
        try:
            monkeypatch.setattr(
                "app.rag.ingestion.make_ingestion_pipeline",
                lambda full_enrichment=None: _FakePipeline(),
            )
            monkeypatch.setattr(
                "app.rag.ingestion.run_ingest_document",
                lambda source, document=None, pipeline=None: {
                    "document_id": "doc-1",
                    "chunk_count": 2,
                    "points_upserted": 2,
                    "ok": True,
                    "errors": [],
                },
            )
            resp = client.post(
                "/api/rag/ingest",
                json={"text": "The Food Safety and Standards Act, 2006", "full_enrichment": True},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["chunk_count"] == 2
        finally:
            ctx.pop()

    def test_real_path_degrades_gracefully(self):
        """No Qdrant/sentence-transformers installed -> 200 + errors, no raise."""
        app, client, ctx = _setup_test_env()
        try:
            resp = client.post(
                "/api/rag/ingest",
                json={"text": "The Food Safety and Standards Act, 2006"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "ok" in data
            assert isinstance(data.get("errors", []), list)
        finally:
            ctx.pop()


class TestCorpusRoute:
    def test_non_dict_document_returns_400(self):
        app, client, ctx = _setup_test_env()
        try:
            resp = client.post(
                "/api/rag/ingest/corpus",
                json={"corpus_dir": "/corpus", "document": "not-a-dict"},
            )
            assert resp.status_code == 400
        finally:
            ctx.pop()

    def test_successful_corpus_ingest_returns_summary(self, monkeypatch):
        app, client, ctx = _setup_test_env()
        try:
            monkeypatch.setattr(
                "app.rag.ingestion.make_ingestion_pipeline",
                lambda full_enrichment=None: _FakePipeline(),
            )
            monkeypatch.setattr(
                "app.rag.ingestion.ingest_corpus_dir",
                lambda corpus_dir, document=None, pipeline=None: {
                    "corpus_dir": "/corpus",
                    "total": 1,
                    "indexed": 1,
                    "duplicates": 0,
                    "failed": 0,
                    "results": [],
                },
            )
            resp = client.post("/api/rag/ingest/corpus", json={"corpus_dir": "/corpus"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total"] == 1
            assert data["indexed"] == 1
        finally:
            ctx.pop()

    def test_missing_corpus_dir_returns_400(self):
        app, client, ctx = _setup_test_env()
        try:
            resp = client.post("/api/rag/ingest/corpus", json={})
            assert resp.status_code == 400
        finally:
            ctx.pop()

    def test_disabled_returns_503(self):
        app, client, ctx = _setup_test_env()
        try:
            app.config["RAG_ENABLED"] = False
            resp = client.post("/api/rag/ingest/corpus", json={"corpus_dir": "/corpus"})
            assert resp.status_code == 503
        finally:
            ctx.pop()


class _FakePipeline:
    """Minimal pipeline stand-in so the route never touches real components."""

    def __init__(self):
        self.classifier = None
