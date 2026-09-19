"""Ops-safety + durability tests for the RAG UI audit gaps #2 and #5 (2026-08-23).

Gap #2 — stub-mode visibility: ``GroundedLLMClient.use_stub`` is public and
``GET /api/rag/health`` reports ``llm.mode`` ("stub"|"live") + the configured
model so deployments can assert live-LLM operation.  The UI flags
``llm_model == "stub-…"`` answers with a banner (client-side, untested here).

Gap #5 — HITL durability: the 202 ``awaiting_review`` payloads carry a
``durable`` flag, a once-per-process warning is logged when HITL runs on the
in-memory checkpointer, and health exposes ``agent_hitl_durable``.

All heavy work is monkeypatched — no Qdrant / network / langgraph required.
"""

from __future__ import annotations

import pytest

from tests.test_rag_routes import _setup_test_env


@pytest.fixture()
def app_env(monkeypatch):
    """Test app + client, with LLM-key env vars cleared for determinism."""
    for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "RAG_USE_STUB_LLM", "RAG_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    app, client, ctx = _setup_test_env()
    # create_app() loads .env (dotenv) which may re-seed the key env vars —
    # delete again AFTER app creation so GroundedLLMClient (which reads
    # os.environ directly) resolves to stub mode deterministically.
    for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "RAG_USE_STUB_LLM", "RAG_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    yield app, client
    ctx.pop()


@pytest.fixture(autouse=True)
def _fresh_durability_warning():
    """Reset the once-per-process HITL warning flag around every test."""
    import app.rag.agent.service as agent_service

    agent_service._hitl_durability_warned = False
    yield
    agent_service._hitl_durability_warned = False


# ---------------------------------------------------------------------- #
# Gap #2 — stub-mode visibility
# ---------------------------------------------------------------------- #


class TestLLMModeVisibility:
    def test_use_stub_property_true_without_key(self, app_env, monkeypatch):
        from app.rag.generation.llm_client import GroundedLLMClient

        assert GroundedLLMClient().use_stub is True

    def test_use_stub_property_false_with_key(self, app_env, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        from app.rag.generation.llm_client import GroundedLLMClient

        assert GroundedLLMClient().use_stub is False

    def test_health_reports_stub_mode_by_default(self, app_env):
        _, client = app_env
        resp = client.get("/api/rag/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["llm"]["mode"] == "stub"
        assert isinstance(data["llm"]["model"], str) and data["llm"]["model"]

    def test_health_reports_live_mode_with_key(self, app_env, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        _, client = app_env
        resp = client.get("/api/rag/health")
        assert resp.get_json()["llm"]["mode"] == "live"


# ---------------------------------------------------------------------- #
# Remote-inference wiring visibility (2026-09-19)
# ---------------------------------------------------------------------- #


class TestRemoteInferenceWiringVisibility:
    """``/api/rag/health`` must reveal whether the CE / embedder call Modal.

    The endpoints are dashboard-only env vars on Render (absent from
    render.yaml), and the pipeline degrades silently when they are missing,
    so this is the only login-free way to prove production is wired.
    """

    @staticmethod
    def _wire_modal(app) -> None:
        app.config["RAG_RERANKER_ENDPOINT"] = "https://ws--rerank.modal.run"
        app.config["RAG_RERANKER_MODE"] = "tei"
        app.config["RAG_RERANKER_TIMEOUT"] = 60.0
        app.config["RAG_RERANKER_REMOTE_FALLBACK"] = False
        app.config["RAG_EMBED_ENDPOINT"] = "https://ws--embed.modal.run"
        app.config["RAG_EMBED_REMOTE_FALLBACK"] = False

    def test_health_reports_local_source_when_no_endpoints(self, app_env):
        app, client = app_env
        app.config["RAG_RERANKER_ENDPOINT"] = ""
        app.config["RAG_EMBED_ENDPOINT"] = ""
        data = client.get("/api/rag/health").get_json()
        for seam in ("reranker", "embedder"):
            assert data[seam]["source"] == "local"
            assert data[seam]["provider"] is None
            assert data[seam]["endpoint"] is None  # authenticated client

    def test_health_reports_remote_source_with_full_detail_when_authenticated(self, app_env):
        app, client = app_env  # ``client`` is pre-authenticated
        self._wire_modal(app)
        data = client.get("/api/rag/health").get_json()

        rr = data["reranker"]
        assert rr["source"] == "remote"
        assert rr["provider"] == "modal"
        assert rr["endpoint"] == "https://ws--rerank.modal.run"
        assert rr["mode"] == "tei"
        assert rr["timeout"] == 60.0
        assert rr["remote_fallback"] is False
        assert isinstance(rr["model"], str)
        assert isinstance(rr["ensemble"], bool)

        em = data["embedder"]
        assert em["source"] == "remote"
        assert em["provider"] == "modal"
        assert em["endpoint"] == "https://ws--embed.modal.run"
        assert em["remote_fallback"] is False
        assert isinstance(em["model"], str) and em["model"]
        assert isinstance(em["qdrant_bm25"], bool)

    def test_health_hides_urls_and_paths_from_anonymous_callers(self, app_env):
        """Modal web endpoints have no auth of their own — a public health
        page must prove the wiring without advertising the URLs."""
        app, _ = app_env
        self._wire_modal(app)
        anon = app.test_client()  # no session
        resp = anon.get("/api/rag/health")
        assert resp.status_code == 200  # still public
        data = resp.get_json()
        for seam in ("reranker", "embedder"):
            assert data[seam]["source"] == "remote"
            assert data[seam]["provider"] == "modal"
            assert "endpoint" not in data[seam]
            assert "model" not in data[seam]
        assert "modal.run" not in resp.get_data(as_text=True)

    def test_health_never_echoes_tokens(self, app_env):
        app, client = app_env
        self._wire_modal(app)
        app.config["RAG_RERANKER_TOKEN"] = "secret-rr"
        app.config["RAG_EMBED_TOKEN"] = "secret-em"
        body = client.get("/api/rag/health").get_data(as_text=True)
        assert "secret-rr" not in body
        assert "secret-em" not in body


# ---------------------------------------------------------------------- #
# Gap #5 — HITL durability signal
# ---------------------------------------------------------------------- #


def _interrupt_result() -> dict:
    from types import SimpleNamespace

    return {
        "__interrupt__": [
            SimpleNamespace(
                value={
                    "message": "Review the grounded answer before release.",
                    "query": "penalty",
                    "answer": "draft answer",
                }
            )
        ]
    }


class TestHitlDurabilitySignal:
    def test_checkpointer_is_durable_default_memory(self, app_env):
        from app.rag.agent.graph import checkpointer_is_durable

        assert checkpointer_is_durable() is False

    def test_checkpointer_is_durable_postgres(self, app_env):
        app, _ = app_env
        app.config["RAG_AGENT_CHECKPOINTER"] = "postgres"
        from app.rag.agent.graph import checkpointer_is_durable

        assert checkpointer_is_durable() is True

    def test_health_exposes_hitl_fields(self, app_env):
        _, client = app_env
        data = client.get("/api/rag/health").get_json()
        assert data["agent_hitl"] is False  # default off
        assert data["agent_checkpointer"] == "memory"
        assert data["agent_hitl_durable"] is False

    def test_202_payload_flags_non_durable_memory(self, app_env, monkeypatch):
        app, client = app_env
        app.config["RAG_USE_AGENT_PIPELINE"] = True
        app.config["RAG_AGENT_HITL"] = True
        import app.rag.agent.graph as graph_mod

        monkeypatch.setattr(graph_mod, "run_agent", lambda state, **kw: _interrupt_result())

        resp = client.post("/api/rag/query/agent", json={"query": "penalty"})
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "awaiting_review"
        assert data["durable"] is False

    def test_202_payload_flags_durable_postgres(self, app_env, monkeypatch):
        app, client = app_env
        app.config["RAG_USE_AGENT_PIPELINE"] = True
        app.config["RAG_AGENT_HITL"] = True
        app.config["RAG_AGENT_CHECKPOINTER"] = "postgres"
        import app.rag.agent.graph as graph_mod

        monkeypatch.setattr(graph_mod, "run_agent", lambda state, **kw: _interrupt_result())

        resp = client.post("/api/rag/query/agent", json={"query": "penalty"})
        assert resp.status_code == 202
        assert resp.get_json()["durable"] is True

    def test_resume_202_payload_carries_durable_flag(self, app_env, monkeypatch):
        app, client = app_env
        app.config["RAG_AGENT_HITL"] = True
        import app.rag.agent.graph as graph_mod

        monkeypatch.setattr(graph_mod, "resume_agent", lambda tid, **kw: _interrupt_result())

        resp = client.post(
            "/api/rag/query/agent/resume",
            json={"thread_id": "t-1", "approved": True},
        )
        assert resp.status_code == 202
        assert resp.get_json()["durable"] is False


def test_durability_warning_logged_once_per_process(app_env, monkeypatch):
    """The non-durable HITL warning fires exactly once per process."""
    app, client = app_env
    app.config["RAG_USE_AGENT_PIPELINE"] = True
    app.config["RAG_AGENT_HITL"] = True
    import app.rag.agent.graph as graph_mod
    import app.rag.agent.service as agent_service

    monkeypatch.setattr(graph_mod, "run_agent", lambda state, **kw: _interrupt_result())

    # Spy on the service core's logger (caplog is unreliable here: the app's
    # logging config can suppress propagation for this logger).
    warnings_seen: list[str] = []
    original_warning = agent_service.logger.warning

    def _spy(msg, *args, **kwargs):
        warnings_seen.append(str(msg))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(agent_service.logger, "warning", _spy)

    client.post("/api/rag/query/agent", json={"query": "q1"})
    client.post("/api/rag/query/agent", json={"query": "q2"})

    durability_warnings = [w for w in warnings_seen if "paused threads are LOST on process restart" in w]
    assert len(durability_warnings) == 1  # warned once despite two paused runs

    # A fresh process (flag reset) warns again.
    agent_service._hitl_durability_warned = False
    client.post("/api/rag/query/agent", json={"query": "q3"})
    durability_warnings = [w for w in warnings_seen if "paused threads are LOST on process restart" in w]
    assert len(durability_warnings) == 2
