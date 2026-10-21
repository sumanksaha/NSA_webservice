"""LLM client for grounded RAG generation.

Provides a thin wrapper around an OpenAI-compatible API (via ``httpx``,
following the pattern in ``app/ai_assistant/service.py``) that returns
a structured ``GroundedLLMResponse`` with raw text for downstream
citation tracking and sanitization.

When the ``OPENROUTER_API_KEY`` environment variable is unset (or
``RAG_USE_STUB_LLM=true``), a deterministic stub is used so the
pipeline can be tested without network access.

The model must be in ``app.shared.llm_client.ALLOWED_MODELS`` (default
``minimax/minimax-m3.1-flash:free``, OpenRouter) —
a ``RAG_LLM_MODEL`` value outside the allow-list raises instead of
silently switching providers.  Real calls additionally require the
``LLM_ENABLED=true`` kill switch and a positive ``MAX_LLM_CALLS`` budget
(see :mod:`app.shared.llm_client`); otherwise ``call()`` serves the stub.
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
        text: Generated answer text (may contain ``[n]`` citation markers).
        model: Model name used for the call.
        usage: Token usage dict (prompt_tokens, completion_tokens,
            total_tokens, reasoning_tokens, cost in USD).
        latency: Wall-clock seconds for the API call.
        error: Error message if the call failed (None on success).
        finish_reason: Provider stop reason (``stop``, ``length``, ...).

    """

    text: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    latency: float = 0.0
    error: str | None = None
    finish_reason: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.text)


#: Stable prefix on the guardrail error.  Telemetry and tests match this
#: instead of parsing the prose message.
EMPTY_CONTENT_ERROR = "llm_empty_content"


def _usage_dict(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Provider usage → the dict carried on the response.

    ``cost`` and ``reasoning_tokens`` used to be dropped, which made a run's
    spend and its thinking budget invisible to the caller.
    """
    usage = usage or {}
    details = usage.get("completion_tokens_details") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
        "completion_tokens": usage.get("completion_tokens", 0) or 0,
        "total_tokens": usage.get("total_tokens", 0) or 0,
        "reasoning_tokens": details.get("reasoning_tokens", 0) or 0,
        "cost": usage.get("cost", 0.0) or 0.0,
    }


def answer_text(choice: dict[str, Any]) -> tuple[str, str | None]:
    """Return ``(text, error)`` for one ``choices[0]`` entry.

    The answer channel is ``message.content``.  Reasoning models can return
    ``content=None`` and put everything in ``message.reasoning``; the old
    fallback served that trace as the answer, so a run that never wrote an
    answer looked like an ordinary low-scoring answer.  Unless
    ``RAG_LLM_REASONING_FALLBACK`` is enabled, this is now an error.
    """
    from app.shared.config import cfg

    message = choice.get("message") or {}
    content = message.get("content")
    finish = choice.get("finish_reason")
    if (content or "").strip():
        return content, None

    trace = message.get("reasoning") or ""
    if cfg.llm_reasoning_fallback and trace.strip():
        logger.warning(
            "LLM returned no content (finish_reason=%r); serving the reasoning "
            "trace because RAG_LLM_REASONING_FALLBACK=true",
            finish,
        )
        return trace, None

    return "", (
        f"{EMPTY_CONTENT_ERROR}: no answer content (finish_reason={finish!r}, "
        f"reasoning_chars={len(trace)}). The model spent the whole budget on "
        "reasoning. Raise RAG_LLM_MAX_TOKENS, set "
        "RAG_LLM_REASONING_MAX_TOKENS, or opt into "
        "RAG_LLM_REASONING_FALLBACK=true."
    )



class GroundedLLMClient:
    """Call an LLM to generate a grounded response.

    Uses ``httpx`` (already a project dependency) for HTTP calls to
    OpenAI-compatible endpoints, following the pattern in
    ``app/ai_assistant/service.py``.

    The model defaults to ``minimax/minimax-m3.1-flash:free`` (OpenRouter) —
    the answer model for grounded RAG generation.  The base URL
    defaults to the OpenRouter endpoint.

    Args:
    model: Optional override for the model name.  Defaults to the
        ``RAG_LLM_MODEL`` env var, or ``minimax/minimax-m3.1-flash:free``
        if unset.
        api_key: Optional override for the API key.  Falls back to the
            ``OPENROUTER_API_KEY`` then ``OPENAI_API_KEY`` env var.
        base_url: Optional OpenAI-compatible API base URL.  Defaults to
            the OpenRouter endpoint.
        stub_response: Text to return when the stub is active.

    """

    #: Default model for grounded RAG generation (OpenRouter free tier).
    DEFAULT_MODEL = "minimax/minimax-m3.1-flash:free"

    #: OpenRouter's OpenAI-compatible endpoint.
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

    #: Total attempts for one logical call (1 initial + N retries).
    MAX_ATTEMPTS = int(os.environ.get("RAG_LLM_MAX_ATTEMPTS", "6"))

    #: Backoff bounds in seconds.  429s need real breathing room: the old
    #: 1s/2s ladder exhausted its retries inside a rate-limit window and
    #: surfaced as an empty answer rather than an error.
    BACKOFF_BASE_S = float(os.environ.get("RAG_LLM_BACKOFF_BASE_S", "2.0"))
    BACKOFF_MAX_S = float(os.environ.get("RAG_LLM_BACKOFF_MAX_S", "60.0"))

    #: HTTP statuses worth retrying.  4xx outside this set (401/403/400/404)
    #: are configuration or request bugs — retrying them just burns the quota.
    RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        stub_response: str | None = None,
    ) -> None:
        from app.shared.llm_client import ALLOWED_MODELS

        self.model = model or os.environ.get("RAG_LLM_MODEL") or self.DEFAULT_MODEL
        if self.model not in ALLOWED_MODELS:
            raise RuntimeError(
                f"Model '{self.model}' is not in the allow-list "
                f"{sorted(ALLOWED_MODELS)}. Refusing to construct an LLM client."
            )
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL") or self.DEFAULT_BASE_URL
        self._stub = stub_response or os.environ.get("RAG_STUB_RESPONSE")
        self._use_stub = not self._api_key or os.environ.get("RAG_USE_STUB_LLM", "").lower() == "true"

        if self._use_stub and not self._stub:
            logger.warning("LLM client running in STUB mode — responses are not realistic")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @property
    def use_stub(self) -> bool:
        """Whether calls are served by the deterministic stub.

        True when the LLM kill switch is off (``LLM_ENABLED=false``), no API
        key is configured, or ``RAG_USE_STUB_LLM=true``.  Ops signal:
        ``/api/rag/health`` reports this as ``llm.mode`` so deployments can
        assert live-LLM operation (RAG UI audit gap #2).
        """
        from app.shared.llm_client import llm_enabled

        return self._use_stub or not llm_enabled()

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        **extra: Any,
    ) -> GroundedLLMResponse:
        """Generate a response from the LLM.

        Args:
            system_prompt: System message / instructions.
            user_prompt: User message with query + context.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens. ``None`` uses
                ``RAG_LLM_MAX_TOKENS`` (default 4096).
            **extra: Additional request fields (passed to the body). When no
                ``reasoning`` field is given, ``RAG_LLM_REASONING_MAX_TOKENS``
                caps the thinking budget so the answer has room to start.

        Returns:
            A :class:`GroundedLLMResponse`.

        """
        if self.use_stub:
            # Kill switch off (LLM_ENABLED=false), stub flag set, or no API
            # key: deterministic canned answer, zero network calls.
            return self._stub_call()

        # Real path: enforce the per-run budget and log the outgoing request.
        from app.shared.config import cfg
        from app.shared.llm_client import _increment_call_count, _log_llm_call

        if max_tokens is None or max_tokens <= 0:
            max_tokens = cfg.llm_max_tokens
        if "reasoning" not in extra:
            budget = cfg.llm_reasoning_max_tokens
            if budget > 0:
                extra["reasoning"] = {"max_tokens": budget}

        _increment_call_count()
        _log_llm_call(
            provider="openrouter",
            model=self.model,
            call_site=f"{self.__class__.__module__}.{self.__class__.__name__}.call",
        )

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
        from app.shared.config import cfg

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
        last_status: int | None = None
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                import httpx

                # TLS certificate verification stays ON (httpx default, enforced
                # via certifi). Disabling it would let a MITM read the API key
                # and every prompt; it was the only verify=False in app/ and
                # tripped Bandit B501 (High) in the Security Scan gate.
                with httpx.Client(timeout=cfg.llm_timeout_s) as client:
                    resp = client.post(url, headers=headers, json=body)
                    if resp.status_code in self.RETRYABLE_STATUS:
                        # Rate limit / transient server error.  Honour the
                        # server's own pacing hint when it sends one.
                        last_status = resp.status_code
                        last_exc = httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}",
                            request=resp.request,
                            response=resp,
                        )
                        if attempt < self.MAX_ATTEMPTS - 1:
                            time.sleep(self._backoff_delay(attempt, resp.headers.get("Retry-After")))
                            continue
                        break
                    resp.raise_for_status()
                    data = resp.json()
                    choice = data["choices"][0]
                    text, error = answer_text(choice)
                    usage = _usage_dict(data.get("usage"))

                    latency = time.perf_counter() - start
                    # Call accounting: record the provider-reported token usage.
                    from app.shared.llm_client import _log_llm_call

                    _log_llm_call(
                        provider="openrouter",
                        model=self.model,
                        call_site=f"{self.__class__.__module__}.{self.__class__.__name__}._real_call",
                        outcome="response" if error is None else "empty_content",
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        total_tokens=usage["total_tokens"],
                    )

                    return GroundedLLMResponse(
                        text=text,
                        model=self.model,
                        usage=usage,
                        latency=latency,
                        error=error,
                        finish_reason=choice.get("finish_reason"),
                    )
            except Exception as exc:
                # A non-retryable HTTP status (401/403/400/404 …) is a config
                # or request bug.  Fail fast instead of burning the retry
                # budget — and report it distinctly from a rate limit.
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in self.RETRYABLE_STATUS:
                    latency = time.perf_counter() - start
                    return GroundedLLMResponse(
                        error=f"LLM request failed (non-retryable HTTP {status}): {exc}",
                        model=self.model,
                        latency=latency,
                    )
                last_exc = exc
                if attempt < self.MAX_ATTEMPTS - 1:
                    time.sleep(self._backoff_delay(attempt))
                else:
                    break

        latency = time.perf_counter() - start
        suffix = f" (last HTTP {last_status})" if last_status else ""
        return GroundedLLMResponse(
            error=f"LLM request failed after {self.MAX_ATTEMPTS} attempts{suffix}: {last_exc}",
            model=self.model,
            latency=latency,
        )

    def _backoff_delay(self, attempt: int, retry_after: str | None = None) -> float:
        """Seconds to wait before retry *attempt*.

        Exponential in ``attempt``, capped at ``BACKOFF_MAX_S``, with a small
        deterministic jitter so concurrent workers do not resynchronise onto
        the same tick.  A server-sent ``Retry-After`` (seconds form) wins.
        """
        if retry_after:
            try:
                return min(float(retry_after), self.BACKOFF_MAX_S)
            except (TypeError, ValueError):
                pass
        base = min(self.BACKOFF_BASE_S * (2**attempt), self.BACKOFF_MAX_S)
        # Deterministic jitter: avoids stampede without needing a RNG import.
        jitter = (attempt % 4) * 0.25
        return min(base + jitter, self.BACKOFF_MAX_S)
