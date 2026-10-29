"""Tests for app.llm.simple_complete — stub only, no network, no cost."""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from app.llm.simple_complete import SimpleLLMClient, STUB_TEXT
from app.shared.llm_client import ALLOWED_MODELS, reset_llm_call_count


@contextmanager
def _env(**kwargs):
    """Temporarily set env vars, restoring them on exit."""
    saved = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_complete_returns_stub_when_no_key():
    with _env(OPENROUTER_API_KEY="", OPENAI_API_KEY="", LLM_ENABLED="false"):
        client = SimpleLLMClient()
        assert client.use_stub is True
        out = client.complete("What is 2 + 2?")
        assert out == STUB_TEXT


def test_complete_returns_custom_stub():
    with _env(OPENROUTER_API_KEY="sk-test", LLM_ENABLED="false"):
        client = SimpleLLMClient(stub_response="custom stub answer")
        out = client.complete("anything")
        assert out == "custom stub answer"


def test_complete_stub_when_llm_disabled_even_with_key():
    with _env(OPENROUTER_API_KEY="sk-test", LLM_ENABLED="false"):
        client = SimpleLLMClient()
        assert client.use_stub is True
        assert client.complete("hello") == STUB_TEXT


def test_complete_bad_model_raises():
    with _env(OPENROUTER_API_KEY="", LLM_ENABLED="false"):
        try:
            SimpleLLMClient(model="not-an-allowed-model")
        except RuntimeError as exc:
            assert "allow-list" in str(exc)
        else:
            raise AssertionError("expected RuntimeError for disallowed model")


def test_complete_default_model_is_allowed():
    with _env(OPENROUTER_API_KEY="", LLM_ENABLED="false"):
        client = SimpleLLMClient()
        assert client.model == "minimax/minimax-m3.1-flash"
        assert client.model in ALLOWED_MODELS


def test_complete_budget_blocks_live_call():
    reset_llm_call_count()
    with _env(
        OPENROUTER_API_KEY="sk-test",
        LLM_ENABLED="true",
        MAX_LLM_CALLS="0",
        SIMPLE_LLM_USE_STUB="false",
    ):
        client = SimpleLLMClient()
        assert client.use_stub is False
        try:
            client.complete("What is 2 + 2?")
        except RuntimeError as exc:
            assert "MAX_LLM_CALLS" in str(exc) or "budget" in str(exc).lower()
        else:
            raise AssertionError("expected RuntimeError when call budget is 0")
