"""Simple LLM query→answer, independent of the legal RAG pipeline.

A thin OpenAI-compatible (OpenRouter) chat client that answers a free-text
query from the model's own weights — no retrieval, no citations, no coupling
to ``app.rag``.  Reuses the shared spend controls in
``app.shared.llm_client`` (model allow-list, kill switch, per-process budget,
call logging) and falls back to a deterministic stub when live calls are
disabled or no API key is configured.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.shared.llm_client import (
    ALLOWED_MODELS,
    DEFAULT_ANSWER_MODEL,
    OPENROUTER_BASE_URL,
    _increment_call_count,
    _log_llm_call,
    llm_enabled,
    max_llm_calls,
    validate_model,
)

DEFAULT_MODEL = DEFAULT_ANSWER_MODEL
DEFAULT_BASE_URL = OPENROUTER_BASE_URL
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 4096
STUB_TEXT = "Stub answer (live LLM disabled or no API key configured)."


class SimpleLLMClient:
    """Plain query→answer LLM client, independent of the legal RAG pipeline.

    Args:
        model: Model slug.  Must be in ``ALLOWED_MODELS``; defaults to the
            ``SIMPLE_LLM_MODEL`` env var or ``minimax/minimax-m3.1-flash``.
        api_key: API key override; falls back to ``OPENROUTER_API_KEY`` /
            ``OPENAI_API_KEY``.
        base_url: OpenAI-compatible base URL; defaults to OpenRouter.
        stub_response: Canned text returned in stub mode (tests / offline).
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        stub_response: str | None = None,
    ) -> None:
        self.model = model or os.environ.get("SIMPLE_LLM_MODEL") or DEFAULT_MODEL
        validate_model(self.model)
        self._api_key = (
            api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        )
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL
        self._stub = stub_response or os.environ.get("SIMPLE_LLM_STUB_RESPONSE")
        self._use_stub = (
            bool(self._stub)
            or not self._api_key
            or os.environ.get("SIMPLE_LLM_USE_STUB", "").lower() == "true"
        )

    @property
    def use_stub(self) -> bool:
        """True when calls are served by the deterministic stub (no network)."""
        return self._use_stub or not llm_enabled()

    def complete(
        self,
        query: str,
        *,
        system_prompt: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int | None = None,
    ) -> str:
        """Answer *query* and return the raw answer text.

        No retrieval context is added — the model answers from its own weights.

        Returns stub text when live-LLM is disabled or no API key is
        configured (no network call).  Raises ``RuntimeError`` only when
        the per-process call budget is exhausted.  Network failures return
        an error string instead of raising.
        """
        if max_tokens is None or max_tokens <= 0:
            max_tokens = DEFAULT_MAX_TOKENS

        if self.use_stub:
            return self._stub or STUB_TEXT

        _increment_call_count()
        _log_llm_call(
            provider="openrouter",
            model=self.model,
            call_site="app.llm.simple_complete.SimpleLLMClient.complete",
        )
        return self._real_call(query, system_prompt, temperature, max_tokens)

    def _real_call(
        self,
        query: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """POST to the OpenAI-compatible /chat/completions endpoint."""
        url = self._base_url.rstrip("/") + "/chat/completions"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                    or "You are a helpful assistant. Answer concisely and directly.",
                },
                {"role": "user", "content": query},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, headers=headers, json=body)
                resp.raise_for_status()
            data = resp.json()
            message = data["choices"][0].get("message") or {}
            return message.get("content") or ""
        except Exception as exc:  # noqa: BLE001 — surface failures, never fall back silently
            return f"LLM request failed: {exc}"
