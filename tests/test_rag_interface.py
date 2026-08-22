"""Tests for the RAG Query Interface UI (app/rag/routes.py → query_ui).

Covers:
- GET /api/rag/ renders the query form template for authenticated users
- GET /api/rag/ returns 404 when RAG_ENABLED=false
- GET /api/rag/ redirects unauthenticated users to login
- The rag_query.js static file is served at /static/js/rag_query.js
- The nav link appears in base.html for authenticated users
- The query form contains expected DOM elements (textarea, domain selector, submit)
- The HITL review/approve/reject section is present in the template
- Template receives the domains list from the route

No Qdrant, sentence-transformers, or network required — all tests use
Flask test client against in-memory SQLite, following the pattern from
test_rag_routes.py / test_ai_assistant.py.
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

    user = User(username="raguser", password_hash="pbkdf2:sha256$test$dummy")
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

    user = User(username="raguser", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()  # No session_transaction — unauthenticated
    return app, client, app_context


class TestRAGQueryRoute:
    """Tests for the GET /api/rag/ route."""

    def test_route_renders_query_form(self):
        """Authenticated user sees the query form (200)."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Legal RAG" in html
            assert "ragQuery" in html  # textarea id
        finally:
            ctx.pop()

    def test_disabled_returns_404(self):
        """When RAG_ENABLED=false, the route returns 404."""
        app, client, ctx = _setup_test_env()
        try:
            app.config["RAG_ENABLED"] = False
            resp = client.get("/api/rag/", follow_redirects=True)
            assert resp.status_code == 404
        finally:
            ctx.pop()

    def test_unauthenticated_redirects(self):
        """Unauthenticated user is redirected to login."""
        _app, unauth_client, ctx = _setup_unauthenticated_client()
        try:
            resp = unauth_client.get("/api/rag/", follow_redirects=False)
            assert resp.status_code in (302, 303)
        finally:
            ctx.pop()

    def test_template_contains_domain_selector(self):
        """The template renders the domain dropdown with entries from collections."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            html = resp.data.decode()
            assert "ragDomain" in html  # domain selector id
            # At least one domain option should be present (from DOMAIN_COLLECTIONS)
            assert "fssai" in html.lower() or "All domains" in html
        finally:
            ctx.pop()

    def test_template_contains_agent_toggle(self):
        """The template renders the agent pipeline toggle checkbox."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            html = resp.data.decode()
            assert "ragUseAgent" in html  # agent toggle id
            assert "Use agent pipeline" in html
        finally:
            ctx.pop()

    def test_template_contains_hitl_review_section(self):
        """The template contains the HITL review/approve/reject section."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            html = resp.data.decode()
            assert "ragReview" in html  # review section id
            assert "ragApproveBtn" in html
            assert "ragRejectBtn" in html
            assert "Human Review Required" in html
        finally:
            ctx.pop()

    def test_template_contains_results_area(self):
        """The template contains the results rendering area."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            html = resp.data.decode()
            assert "ragResults" in html  # results container id
            assert "ragSubmitBtn" in html  # submit button id
        finally:
            ctx.pop()


class TestRAGQueryStaticAssets:
    """Tests for static assets served by the RAG query UI."""

    def test_rag_query_js_is_served(self):
        """The rag_query.js file is served at the expected URL."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/static/js/rag_query.js")
            assert resp.status_code == 200
            js = resp.data.decode()
            assert "RagQueryUI" in js  # global export
            assert "query/agent" in js  # endpoint reference
        finally:
            ctx.pop()

    def test_rag_query_ui_init_called(self):
        """The template includes a script block that calls RagQueryUI.init()."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            html = resp.data.decode()
            assert "RagQueryUI.init()" in html
            assert "rag_query.js" in html
        finally:
            ctx.pop()


class TestRAGQueryNavLink:
    """Tests for the nav link in base.html."""

    def test_nav_link_present_when_rag_enabled(self):
        """The 'Legal RAG' nav link appears when RAG_ENABLED=true."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/case_file_generator/", follow_redirects=True)
            html = resp.data.decode()
            assert "Legal RAG" in html
        finally:
            ctx.pop()

    def test_nav_link_hidden_when_rag_disabled(self):
        """The 'Legal RAG' nav link is absent when RAG_ENABLED=false."""
        app, client, ctx = _setup_test_env()
        try:
            app.config["RAG_ENABLED"] = False
            resp = client.get("/case_file_generator/", follow_redirects=True)
            html = resp.data.decode()
            # The link is gated by {% if config.get('RAG_ENABLED', True) %}
            # so it should not appear when false.
            assert "Legal RAG" not in html
        finally:
            ctx.pop()


class TestRAGQueryFormElements:
    """Detailed DOM element checks on the query form."""

    def test_submit_button_present(self):
        """The Ask button with id ragSubmitBtn is rendered."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            html = resp.data.decode()
            assert "ragSubmitBtn" in html
            assert "Ask" in html
        finally:
            ctx.pop()

    def test_topk_input_present(self):
        """The top-K numeric input is rendered with default value 10."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            html = resp.data.decode()
            assert "ragTopK" in html
            assert 'value="10"' in html
        finally:
            ctx.pop()

    def test_query_textarea_has_placeholder(self):
        """The query textarea has the expected placeholder text."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            html = resp.data.decode()
            assert "legal question" in html.lower()
        finally:
            ctx.pop()

    def test_template_extends_base(self):
        """The template extends base.html (navigation, auth gate)."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/api/rag/")
            html = resp.data.decode()
            # base.html renders the nav bar
            assert "nav-link" in html
        finally:
            ctx.pop()


class TestRAGQueryFlow:
    """Integration tests verifying the JS submit→render→resume flow."""

    def test_js_references_agent_endpoint(self):
        """The JS file posts to /api/rag/query/agent for the main query."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/static/js/rag_query.js")
            js = resp.data.decode()
            assert '/api/rag/query/agent"' in js or '/api/rag/query/agent\'' in js
        finally:
            ctx.pop()

    def test_js_references_resume_endpoint(self):
        """The JS file posts to /api/rag/query/agent/resume for HITL."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/static/js/rag_query.js")
            js = resp.data.decode()
            assert "/api/rag/query/agent/resume" in js
        finally:
            ctx.pop()

    def test_js_handles_202_status(self):
        """The JS handles HTTP 202 (awaiting_review) responses."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/static/js/rag_query.js")
            js = resp.data.decode()
            assert "202" in js
        finally:
            ctx.pop()

    def test_js_renders_citations(self):
        """The JS renders citation cards with section labels and snippets."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/static/js/rag_query.js")
            js = resp.data.decode()
            assert "citations" in js
            assert "section_number" in js
            assert "snippet" in js
        finally:
            ctx.pop()

    def test_js_renders_groundedness(self):
        """The JS renders the groundedness score badge."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/static/js/rag_query.js")
            js = resp.data.decode()
            assert "groundedness" in js
        finally:
            ctx.pop()

    def test_js_renders_retrieved_chunks(self):
        """The JS renders the retrieved context chunks section."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/static/js/rag_query.js")
            js = resp.data.decode()
            assert "retrieved_chunks" in js
            assert "rag-chunk" in js
        finally:
            ctx.pop()

    def test_js_approve_reject_buttons(self):
        """The JS wires approve and reject buttons for HITL review."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/static/js/rag_query.js")
            js = resp.data.decode()
            assert "ragApproveBtn" in js
            assert "ragRejectBtn" in js
            assert "approved" in js
        finally:
            ctx.pop()

    def test_js_handles_network_errors(self):
        """The JS has user-friendly error messages for network failures."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/static/js/rag_query.js")
            js = resp.data.decode()
            assert "Network error" in js
            assert "USER_FRIENDLY_ERRORS" in js
        finally:
            ctx.pop()
