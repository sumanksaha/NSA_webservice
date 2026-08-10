"""LLM client for grounded RAG generation.

Provides a thin wrapper around an OpenAI-compatible API (via ``httpx``,
following the pattern in ``app/ai_assistant/service.py``) that returns
a structured ``GroundedLLMResponse`` with raw text for downstream
citation tracking and sanitization.

When the ``OPENROUTER_API_KEY`` environment variable is unset (or
``RAG_USE_STUB_LLM=true``), a deterministic stub is used so the
pipeline can be tested without network access.

The default model is ``poolside/laguna-s-2.1:free`` (OpenRouter free tier).
The ``RAG_LLM_MODEL`` env var overrides it so you can switch providers without
code changes (e.g. ``RAG_LLM_MODEL=google/gemini-2.0-flash-exp:free``).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GroundedLLMResponse:
    """Raw response from the LLM.

    Attributes:
        text: Generated text (may contain ``[n]`` citation markers).
        model: Model name used for the call.
        usage: Token usage dict (prompt_tokens, completion_tokens, total_tokens).
        latency: Wall-clock seconds for the API call.
        error: Error message if the call failed (None on success).
    """

    text: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    latency: float = 0.0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.text)


class GroundedLLMClient:
    """Call an LLM to generate a grounded response.

    Uses ``httpx`` (already a project dependency) for HTTP calls to
    OpenAI-compatible endpoints, following the pattern in
    ``app/ai_assistant/service.py``.

    The model is hardcoded to ``poolside/laguna-s-2.1:free`` (OpenRouter) —
    this is the sole model used for grounded RAG generation.  The base URL
    defaults to the OpenRouter endpoint.

    Args:
    model: Optional override for the model name.  Defaults to the
        ``RAG_LLM_MODEL`` env var, or ``poolside/laguna-s-2.1:free``
        if unset.
        api_key: Optional override for the API key.  Falls back to the
            ``OPENROUTER_API_KEY`` then ``OPENAI_API_KEY`` env var.
        base_url: Optional OpenAI-compatible API base URL.  Defaults to
            the OpenRouter endpoint.
        stub_response: Text to return when the stub is active.
    """

    #: Default model for grounded RAG generation (OpenRouter free tier).
    DEFAULT_MODEL = "poolside/laguna-s-2.1:free"

    #: OpenRouter's OpenAI-compatible endpoint.
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        stub_response: str | None = None,
    ) -> None:
        self.model = model or os.environ.get("RAG_LLM_MODEL") or self.DEFAULT_MODEL
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL") or self.DEFAULT_BASE_URL
        self._stub = stub_response or os.environ.get("RAG_STUB_RESPONSE")
        self._use_stub = not self._api_key or os.environ.get("RAG_USE_STUB_LLM", "").lower() == "true"

        if self._use_stub and not self._stub:
            logger.warning("LLM client running in STUB mode — responses are not realistic")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        **extra: Any,
    ) -> GroundedLLMResponse:
        """Generate a response from the LLM.

        Args:
            system_prompt: System message / instructions.
            user_prompt: User message with query + context.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.
            **extra: Additional kwargs (ignored in stub mode).

        Returns:
            A :class:`GroundedLLMResponse`.
        """
        if self._use_stub:
            return self._stub_call()

        return self._real_call(
            system_prompt,
            user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra,
        )

    # ------------------------------------------------------------------ #
    # Internal — Stub
    # ------------------------------------------------------------------ #

    def _stub_call(self) -> GroundedLLMResponse:
        """Return a canned response for testing."""
        text = self._stub or (
            "Based on the provided context, the relevant legal provisions "
            "have been cited [1]. This is a stub response for testing purposes."
        )
        return GroundedLLMResponse(
            text=text,
            model=f"stub-{self.model}",
            usage={"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            latency=0.001,
        )

    # ------------------------------------------------------------------ #
    # Internal — Real API (httpx)
    # ------------------------------------------------------------------ #

    def _real_call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        **extra: Any,
    ) -> GroundedLLMResponse:
        """Call the real LLM API via httpx."""
        start = time.perf_counter()

        url = self._base_url.rstrip("/") + "/chat/completions"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body.update(extra)

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                import httpx

                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(url, headers=headers, json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    choice = data["choices"][0]
                    message = choice.get("message", {})
                    text = message.get("content")
                    if text is None:
                        # Some OpenRouter reasoning models (e.g. poolside/laguna-s-2.1:free)
                        # may return content=None while emitting the response in
                        # the reasoning field — fall back to it.
                        text = message.get("reasoning") or ""
                    usage = data.get("usage", {})

                    latency = time.perf_counter() - start
                    return GroundedLLMResponse(
                        text=text,
                        model=self.model,
                        usage={
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        },
                        latency=latency,
                    )
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2**attempt)
                else:
                    break

        latency = time.perf_counter() - start
        return GroundedLLMResponse(
            error=f"LLM request failed after 3 attempts: {last_exc}",
            model=self.model,
            latency=latency,
        )
