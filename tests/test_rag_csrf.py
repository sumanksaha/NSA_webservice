"""CSRF-contract regression tests for the RAG query UI (audit gap #8).

``rag_query.js`` posts JSON to ``/api/rag/query/agent`` and relies on
``base.html``'s global ``fetch()`` interceptor attaching ``X-CSRFToken``
from the ``csrf-token`` meta tag (see base.html ~line 416).  These tests
pin both ends of that contract so a refactor of either side fails loudly:

* the server-rendered page carries the meta tag AND the interceptor JS,
* with ``WTF_CSRF_ENABLED=true`` the server actually rejects token-less
  POSTs with 400,
* a POST carrying the session's token passes CSRF validation end-to-end.

No Qdrant / network required — the pipeline entry point is monkeypatched.
"""

from __future__ import annotations

import re

import pytest

from tests.test_rag_routes import _setup_test_env


@pytest.fixture()
def csrf_env():
    """Authenticated test app with CSRF protection explicitly enabled."""
    app, client, ctx = _setup_test_env()
    app.config["WTF_CSRF_ENABLED"] = True
    yield app, client
    ctx.pop()


@pytest.fixture(autouse=True)
def _fresh_breaker():
    """Isolate the module-level query-breaker singleton between tests."""
    import app.rag.routes as routes

    routes._query_breaker = None
    yield
    routes._query_breaker = None


def _csrf_token(html: str) -> str:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert match, "csrf-token meta tag missing from rendered page"
    return match.group(1)


def _ok_result(query: str) -> dict:
    return {
        "query": query,
        "answer": f"answer for {query}",
        "groundedness_score": 0.9,
        "citations": [],
        "retrieved_chunks": [],
    }


class TestCsrfContract:
    def test_page_renders_csrf_meta_and_fetch_interceptor(self, csrf_env):
        """base.html must keep the meta tag + interceptor rag_query.js relies on."""
        _, client = csrf_env
        resp = client.get("/api/rag/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert '<meta name="csrf-token"' in html
        assert "X-CSRFToken" in html

    def test_post_without_token_rejected_when_csrf_enabled(self, csrf_env):
        _, client = csrf_env
        resp = client.post("/api/rag/query/agent", json={"query": "penalty"})
        assert resp.status_code == 400  # CSRFProtect rejection, not a crash/500

    def test_wrong_token_still_rejected(self, csrf_env):
        _, client = csrf_env
        resp = client.post(
            "/api/rag/query/agent",
            json={"query": "penalty"},
            headers={"X-CSRFToken": "bogus-token"},
        )
        assert resp.status_code == 400

    def test_post_with_session_token_passes_csrf(self, csrf_env, monkeypatch):
        import app.rag.tasks as tasks

        monkeypatch.setattr(
            tasks,
            "run_generation_pipeline",
            lambda query, **kw: _ok_result(query),
        )
        _, client = csrf_env

        page = client.get("/api/rag/")
        token = _csrf_token(page.get_data(as_text=True))
        resp = client.post(
            "/api/rag/query/agent",
            json={"query": "penalty"},
            headers={"X-CSRFToken": token},
        )
        assert resp.status_code == 200
        assert resp.get_json()["answer"] == "answer for penalty"

    def test_csrf_disabled_env_still_allows_tokenless_posts(self, monkeypatch):
        """The test-suite convention (WTF_CSRF_ENABLED=false) keeps working."""
        _app, client, ctx = _setup_test_env()  # disables CSRF by default
        try:
            import app.rag.tasks as tasks

            monkeypatch.setattr(
                tasks,
                "run_generation_pipeline",
                lambda query, **kw: _ok_result(query),
            )
            resp = client.post("/api/rag/query/agent", json={"query": "penalty"})
            assert resp.status_code == 200
        finally:
            ctx.pop()
