"""Pytest session bootstrap.

The ``app`` package runs ``create_app()`` at module-import time, which binds
the SQLAlchemy engine to the default dev DB (``instance/app.db``) and creates
tables via the startup fallback. Test fixtures that later override
``SQLALCHEMY_DATABASE_URI`` to ``sqlite:///:memory:`` do NOT take effect,
so tests silently read/write the developer's DB — and fail with schema drift
when the dev DB predates model changes (e.g. ``user.is_admin``).

Setting ``DATABASE_URL`` here, before any test module imports ``app``, pins
the whole session to an isolated temp SQLite DB instead. ``load_dotenv`` does
not override an already-set env var, so this cannot be clobbered.
"""

import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = tempfile.mkdtemp(prefix="nsa_test_db_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TEST_DB_DIR) / 'test.db'}"


import pytest


@pytest.fixture(autouse=True, scope="module")
def _reset_db_per_module():
    """Drop and recreate all tables before each test module.

    Prevents DB state pollution: test modules that create records (FSO,
    CaseFile, User, etc.) without tearing down their tables leave data
    that conflicts with subsequent modules (UNIQUE constraints, etc.).
    For example, ``test_ai_assistant.py`` seeds ``FSO(fso_name="Test
    Officer")`` without dropping tables in teardown, and then
    ``test_annexure.py`` tries to insert the same name → IntegrityError.
    """
    from app import app
    from app.extensions import db

    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
    yield


@pytest.fixture(autouse=True)
def _pop_leaked_flask_app_context():
    """Pop any Flask app context a test leaked by pushing without popping.

    Several legacy ``_setup_test_env()`` helpers push an app context and rely
    on the caller to pop it — most callers never do. A leaked context makes
    ``has_app_context()`` True for the REST of the session, so configuration
    reads (``cfg`` / the old resolvers alike) silently hit the last-created
    app's seeded config instead of the current test's monkeypatched env.
    Legitimate ``with app.app_context():`` blocks pop themselves before this
    teardown runs, so only genuine leaks are caught here.
    """
    yield
    try:
        # Flask 3.x: has_app_context / has_request_context live in the top-level
        # ``flask`` package, NOT ``flask.globals``. Importing them from
        # ``flask.globals`` raises ImportError (silently swallowed by the
        # ``except`` below), which means leaked contexts were NEVER popped —
        # causing test-isolation failures when a prior module left an app
        # context active (e.g. test_ai_assistant.py's _setup_test_env pushes
        # but never pops), so cfg/config reads hit stale cached values.
        from flask import has_app_context, has_request_context
        from flask.globals import app_ctx, request_ctx

        while has_request_context():
            request_ctx.pop()
        while has_app_context():
            app_ctx.pop()
    except Exception:  # pragma: no cover - nothing to pop / Flask internals
        pass


os.environ["SKIP_FSO_STARTUP_SYNC"] = "1"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"

# Skip default admin seeding at create_app() time — test fixtures create their
# own users (including "admin") and the import-time seed conflicts with them
# (UNIQUE constraint on user.username). See app/__init__.py admin-seed guard.
os.environ["SKIP_ADMIN_SEED"] = "1"

# Phase 18 RBAC gate is deny-by-default for users without seeded roles; the
# dedicated RBAC suite (tests/test_rbac.py) tests blueprint_allowed() directly.
# Route suites exercise notepad/inspection/etc. behavior, not permissions, so
# enforcement stays off session-wide. Set DISABLE_RBAC=0 in a test body to opt in.
os.environ["DISABLE_RBAC"] = "1"

# Phase 8: PDF Assembly Engine tests configuration
os.environ["DISABLE_PDF_GENERATION"] = "1"  # Disable actual PDF generation for testing


def pytest_collection_modifyitems(config, items):
    """Auto-mark integration/RAG/E2E tests as slow.

    These tests require Qdrant, network inference, or heavy computation and
    are deselected by the CI fast-tests job (pytest -m "not slow").
    The slow marker is declared in pyproject.toml.
    """
    slow_keywords = (
        "rag",
        "qdrant",
        "embedding",
        "retrieval",
        "e2e",
        "integration",
        "hybrid",
        "benchmark",
        "benchmarks",
        "enrichment",
        "evaluate",
        "evaluation",
        "reingest",
        "batch",
        "remote",
        "neo4j",
        "ce_v2",
    )

    for item in items:
        fspath = str(item.fspath).lower()
        if any(k in fspath or k in item.keywords for k in slow_keywords):
            item.add_marker(pytest.mark.slow)


import pytest


@pytest.fixture(autouse=True)
def _rag_stub_llm_env(monkeypatch):
    """Pin RAG grounded-generation to deterministic stub LLM mode.

    The RAG generation/hallucination suites are designed to run offline
    (``GroundedLLMClient`` stub mode) regardless of the developer's ``.env``
    — which may set a real ``OPENAI_API_KEY`` with
    ``RAG_USE_STUB_LLM=false`` (observed 2026-08-09), silently turning
    stub-oriented tests into slow, non-deterministic live API calls.  Tests
    that intentionally exercise the real path can re-set the flag in their
    own body (``monkeypatch.setenv``) since the client reads env at call time.
    """
    monkeypatch.setenv("RAG_USE_STUB_LLM", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RAG_STUB_RESPONSE", raising=False)
    # GroundedLLMClient now hardcodes poolside/laguna-s-2.1:free, so no
    # model-related env cleanup is needed beyond the stub mode above.
    monkeypatch.delenv("RAG_LLM_MODEL", raising=False)
    # Pin full-enrichment off so ingestion tests use the cheap default
    monkeypatch.setenv("RAG_FULL_ENRICHMENT", "false")


@pytest.fixture(autouse=True)
def _rag_remote_inference_env(monkeypatch):
    """Isolate retrieval tests from remote-inference endpoints in ``.env``.

    A developer's ``.env`` may set ``RAG_RERANKER_ENDPOINT`` /
    ``RAG_EMBED_ENDPOINT`` (hosted CE / dense inference via Modal, Space, or
    TEI).  The retrieval unit tests inject fake clients and must never try to
    reach a real endpoint — observed 2026-08-16: ``.env`` pointing at a
    not-yet-deployed Modal URL turned ``test_dense_retriever.py`` search
    tests into live 404 calls.  Tests that intentionally exercise the remote
    wiring re-set their own env in the test body (e.g.
    ``tests/test_remote_reranker.py``, ``tests/test_remote_embedder.py``).
    """
    for var in (
        "RAG_RERANKER_ENDPOINT",
        "RAG_RERANKER_TOKEN",
        "RAG_RERANKER_MODE",
        "RAG_RERANKER_REMOTE_FALLBACK",
        "RAG_EMBED_ENDPOINT",
        "RAG_EMBED_TOKEN",
        "RAG_EMBED_TIMEOUT",
        "RAG_EMBED_REMOTE_FALLBACK",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def test_db_uri(tmp_path):
    """Per-test isolated SQLite URI for ``create_app(db_uri=...)``.

    Test modules that need a database fully isolated from the session-wide
    temp DB (e.g. backup/restore round-trips) pass this URI to the app
    factory, which binds a dedicated engine to it.
    """
    return f"sqlite:///{tmp_path / 'test.db'}"
