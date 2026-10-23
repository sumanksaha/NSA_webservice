"""LLM kill switch, model allow-list, and call-budget tests.

Covers the hard spend controls added after the DeepSeek incident:

* ``LLM_ENABLED`` unset (production default) → a full run of the app's
  LLM entry points makes ZERO network calls (HTTP layer mocked with a
  tripwire that fails the test if so much as one client is constructed).
* A non-allowed model name raises — no silent provider/model switch.
* When Laguna fails at the provider, the failure surfaces as an error and
  the request body keeps Laguna on every retry (no DeepSeek fallback).
* ``MAX_LLM_CALLS`` stops the run with an error when exceeded.
* The decision pathway refuses a deepseek RAG_DECISION_MODEL (root-cause
  regression pin: the jev-router router previously resolved to deepseek).
"""

from __future__ import annotations

import pytest

import httpx
from typing import ClassVar

from app.shared.llm_client import (
    get_llm_client,
    llm_call_count,
    llm_enabled,
    reset_llm_call_count,
    validate_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _TripwireClient:
    """Any instantiation means an attempted network call — fail loudly."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("network call attempted while LLM is disabled")


class _FakeResp:
    """Minimal httpx.Response stand-in recorded by _RecordingClient."""

    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self.headers: dict = {}
        self.request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=self.request, response=self)

    def json(self):
        return self._payload


class _RecordingClient:
    """Fake httpx.Client recording every request body; answers via `responder`."""

    sent: ClassVar[list] = []
    responder = staticmethod(lambda body: _FakeResp(500))

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        _RecordingClient.sent.append({"url": url, "body": json})
        return _RecordingClient.responder(json)


_SUCCESS_BODY = {
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    "model": "poolside/laguna-s-2.1:free",
}


@pytest.fixture(autouse=True)
def _reset_budget():
    reset_llm_call_count()
    yield
    reset_llm_call_count()


# ---------------------------------------------------------------------------
# Step 6a — LLM_ENABLED unset ⇒ zero network calls across the entry points
# ---------------------------------------------------------------------------


class TestKillSwitchZeroNetwork:
    def test_disabled_full_run_makes_zero_network_calls(self, monkeypatch):
        """With LLM_ENABLED unset, every entry point runs with zero HTTP calls."""
        monkeypatch.delenv("LLM_ENABLED", raising=False)
        monkeypatch.setenv("MAX_LLM_CALLS", "0")
        monkeypatch.setattr(httpx, "Client", _TripwireClient)

        assert llm_enabled() is False

        # 1. RAG answer path — factory + client + full generation service.
        client = get_llm_client()
        resp = client.call("sys", "usr")
        assert resp.success and "stub" in resp.model

        from app.rag.generation.grounded_service import GroundedGenerationService
        from app.rag.retrieval.result import RetrievedChunk

        service = GroundedGenerationService()
        chunk = RetrievedChunk(chunk_id="c1", score=1.0, text="Section 2.4.6 [1] of the Act.")
        rag_resp = service.generate("What is the penalty?", [chunk])
        assert rag_resp.answer  # stub answer, not an error

        # 2. AI assistant — disabled: 503-bound RuntimeError, no HTTP.
        from flask import Flask

        from app.ai_assistant.service import AIAssistantService

        app = Flask(__name__)
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "sk-test"
        with app.app_context():
            assistant = AIAssistantService()
            assert assistant.is_enabled() is False
            with pytest.raises(RuntimeError, match="kill switch"):
                assistant.summarize_text("some legal text")

        # 3. Photo scoring — records a failed assessment, no HTTP.
        from app.inspection.photo_scoring import PhotoEvidenceScorer

        result = PhotoEvidenceScorer().score_url("https://example.com/photo.jpg")
        assert result.error and "kill switch" in result.error

        # No real LLM call was counted anywhere.
        assert llm_call_count() == 0

    def test_health_reports_stub_when_disabled(self, monkeypatch):
        monkeypatch.delenv("LLM_ENABLED", raising=False)
        client = get_llm_client()
        assert client.use_stub is True


# ---------------------------------------------------------------------------
# Step 6b — a non-allowed model name raises
# ---------------------------------------------------------------------------


class TestModelAllowList:
    @pytest.mark.parametrize(
        "bad_model",
        [
            "deepseek/deepseek-v4.1-flash",
            "openai/gpt-4o-mini",
            "typesafe/jev-router",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
    )
    def test_disallowed_model_raises_everywhere(self, bad_model):
        with pytest.raises(RuntimeError, match="allow-list"):
            validate_model(bad_model)
        with pytest.raises(RuntimeError, match="allow-list"):
            get_llm_client(model=bad_model)
        from app.rag.generation.llm_client import GroundedLLMClient

        with pytest.raises(RuntimeError, match="allow-list"):
            GroundedLLMClient(model=bad_model)

    def test_env_var_disallowed_model_raises_even_when_disabled(self, monkeypatch):
        """A stray RAG_LLM_MODEL must surface, even with the kill switch off."""
        monkeypatch.delenv("LLM_ENABLED", raising=False)
        monkeypatch.setenv("RAG_LLM_MODEL", "deepseek/deepseek-v4.1-flash")
        with pytest.raises(RuntimeError, match="allow-list"):
            get_llm_client()

    def test_allowed_model_passes(self):
        validate_model("poolside/laguna-s-2.1:free")


# ---------------------------------------------------------------------------
# Step 6c — Laguna failure surfaces as an error; NO fallback to DeepSeek
# ---------------------------------------------------------------------------


class TestNoFallbackOnFailure:
    def _arm_live(self, monkeypatch, max_calls: str = "1000"):
        monkeypatch.setenv("LLM_ENABLED", "true")
        monkeypatch.setenv("MAX_LLM_CALLS", max_calls)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("RAG_USE_STUB_LLM", "false")
        monkeypatch.delenv("RAG_LLM_MODEL", raising=False)

    def test_rag_failure_keeps_default_model_on_every_attempt(self, monkeypatch):
        self._arm_live(monkeypatch)
        monkeypatch.setattr("app.rag.generation.llm_client.GroundedLLMClient.MAX_ATTEMPTS", 2)
        monkeypatch.setattr("app.rag.generation.llm_client.GroundedLLMClient.BACKOFF_BASE_S", 0.01)
        monkeypatch.setattr("app.rag.generation.llm_client.GroundedLLMClient.BACKOFF_MAX_S", 0.02)
        _RecordingClient.sent.clear()
        _RecordingClient.responder = staticmethod(lambda body: _FakeResp(500))
        monkeypatch.setattr(httpx, "Client", _RecordingClient)

        from app.rag.generation.llm_client import GroundedLLMClient

        client = GroundedLLMClient()
        resp = client.call("sys", "usr")

        # Failure surfaces as an error — no answer text, no silent switch.
        assert resp.error and "failed after" in resp.error
        assert resp.text == ""
        assert resp.model == "minimax/minimax-m3.1-flash"
        # Every attempted request named the default answer model; none mentioned deepseek.
        assert len(_RecordingClient.sent) == 2  # MAX_ATTEMPTS
        for record in _RecordingClient.sent:
            assert record["body"]["model"] == "minimax/minimax-m3.1-flash"
            assert "deepseek" not in str(record).lower()

    def test_ai_assistant_failure_surfaces_error_no_model_switch(self, monkeypatch):
        from flask import Flask

        self._arm_live(monkeypatch)
        # 500 is not retried (no sleeps), so this test is fast by construction.
        _RecordingClient.sent.clear()
        _RecordingClient.responder = staticmethod(lambda body: _FakeResp(500))
        monkeypatch.setattr(httpx, "Client", _RecordingClient)

        from app.ai_assistant.service import AIAssistantService

        app = Flask(__name__)
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "sk-test"
        with app.app_context():
            assistant = AIAssistantService()
            assert assistant.is_enabled() is True
            with pytest.raises(httpx.HTTPStatusError):
                assistant.summarize_text("text")

        assert _RecordingClient.sent
        for record in _RecordingClient.sent:
            assert record["body"]["model"] == "minimax/minimax-m3.1-flash"
            assert "deepseek" not in str(record).lower()

    def test_decision_model_deepseek_refused(self, monkeypatch):
        """Root-cause pin: RAG_DECISION_MODEL may never name deepseek."""
        monkeypatch.setenv("RAG_DECISION_MODEL", "deepseek/deepseek-v4.1-flash")
        from app.rag.retrieval.decision_selector import DecisionModelClient

        with pytest.raises((RuntimeError, ValueError), match="sanctioned"):
            DecisionModelClient()


# ---------------------------------------------------------------------------
# Step 6d — MAX_LLM_CALLS budget
# ---------------------------------------------------------------------------


class TestCallBudget:
    def _arm_live(self, monkeypatch, max_calls: str):
        monkeypatch.setenv("LLM_ENABLED", "true")
        monkeypatch.setenv("MAX_LLM_CALLS", max_calls)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("RAG_USE_STUB_LLM", "false")
        monkeypatch.delenv("RAG_LLM_MODEL", raising=False)

    def test_zero_budget_blocks_every_real_call(self, monkeypatch):
        self._arm_live(monkeypatch, max_calls="0")
        _RecordingClient.sent.clear()
        _RecordingClient.responder = staticmethod(lambda body: _FakeResp(200, _SUCCESS_BODY))
        monkeypatch.setattr(httpx, "Client", _RecordingClient)

        from app.rag.generation.llm_client import GroundedLLMClient

        client = GroundedLLMClient()
        with pytest.raises(RuntimeError, match="MAX_LLM_CALLS"):
            client.call("s", "u")
        assert _RecordingClient.sent == []  # blocked BEFORE any network I/O

    def test_budget_stops_run_when_exceeded(self, monkeypatch):
        self._arm_live(monkeypatch, max_calls="1")
        _RecordingClient.sent.clear()
        _RecordingClient.responder = staticmethod(lambda body: _FakeResp(200, _SUCCESS_BODY))
        monkeypatch.setattr(httpx, "Client", _RecordingClient)

        from app.rag.generation.llm_client import GroundedLLMClient

        client = GroundedLLMClient()
        first = client.call("s", "u")
        assert first.success
        assert llm_call_count() == 1
        with pytest.raises(RuntimeError, match="budget exhausted"):
            client.call("s", "u")
        assert len(_RecordingClient.sent) == 1  # second call never hit the wire
