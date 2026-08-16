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
os.environ["SKIP_FSO_STARTUP_SYNC"] = "1"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"  # noqa: S105

# Phase 8: PDF Assembly Engine tests configuration
os.environ["DISABLE_PDF_GENERATION"] = "1"  # Disable actual PDF generation for testing


import pytest  # noqa: E402


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
