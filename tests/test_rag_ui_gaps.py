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
    import app.rag.agent.routes as agent_routes

    agent_routes._hitl_durability_warned = False
    yield
    agent_routes._hitl_durability_warned = False


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
    import app.rag.agent.routes as agent_routes

    monkeypatch.setattr(graph_mod, "run_agent", lambda state, **kw: _interrupt_result())

    # Spy on the route module's logger (caplog is unreliable here: the app's
    # logging config can suppress propagation for this logger).
    warnings_seen: list[str] = []
    original_warning = agent_routes.logger.warning

    def _spy(msg, *args, **kwargs):
        warnings_seen.append(str(msg))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(agent_routes.logger, "warning", _spy)

    client.post("/api/rag/query/agent", json={"query": "q1"})
    client.post("/api/rag/query/agent", json={"query": "q2"})

    durability_warnings = [w for w in warnings_seen if "paused threads are LOST on process restart" in w]
    assert len(durability_warnings) == 1  # warned once despite two paused runs

    # A fresh process (flag reset) warns again.
    agent_routes._hitl_durability_warned = False
    client.post("/api/rag/query/agent", json={"query": "q3"})
    durability_warnings = [w for w in warnings_seen if "paused threads are LOST on process restart" in w]
    assert len(durability_warnings) == 2
