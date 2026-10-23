"""Central LLM spend controls — the ONLY place the app reads LLM kill-switch
and budget settings, validates answer models, or resolves an answer client.

Fail-closed controls (both default to "no calls"):

* ``LLM_ENABLED`` (default ``false``) — hard kill switch, read at CALL time
  so tests and ops can toggle it without a restart:
  - RAG answer path (:func:`get_llm_client` / ``GroundedLLMClient.call``):
    serves the deterministic no-op stub — zero network calls.
  - AI assistant / decision chat transport: raises ``RuntimeError``
    (routes already translate that to 503).
  - Photo scoring: records a failed assessment (fail-closed audit row).
* ``MAX_LLM_CALLS`` (default ``0``) — per-process cap on REAL (non-stub)
  LLM calls; ``0`` blocks every call.  To permit LLM calls you must set
  BOTH ``LLM_ENABLED=true`` AND ``MAX_LLM_CALLS=<positive budget>``.
* ``ALLOWED_MODELS`` — the answer model must be the default
  ``minimax/minimax-m3.1-flash:free`` (free, via OpenRouter) or another
  allow-listed slug: laguna (retires 2026-10-31; retained for cached eval
  baselines) plus the free swap candidates under evaluation (see
  ``evaluation/MODEL_SWAP_FREE_PLAN.md``).  Any other id raises; there is no
  fallback provider or model.  (Decision
  models keep their own allow-list in ``app.rag.retrieval.decision_selector``
  — today ``typesafe/jev-1.13`` only; ``inception/mercury-decide:free`` was
  dropped 2026-10-15 after the head-to-head and remains solely the
  photo-scoring default, a separate list.)
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: Answer-model allow-list (all OpenRouter).  Default is minimax-m3.1-flash
#: (swapped 2026-10-19 after the free-candidate paired A/B — see
#: evaluation/MODEL_SWAP_FREE_PLAN.md).  Laguna retires 2026-10-31; it stays
#: here for cached eval baselines and should be dropped after that date.
ALLOWED_MODELS: frozenset[str] = frozenset({
    "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "nvidia/nemotron-3.5-lightning:free",
    "thinkingmachines/inkling-small:free",
    "google/gemma-4-26b-a4b-it:free",
    "minimax/minimax-m3.1-flash:free",
    # Paid GLM 5.3 Flash — first paid candidate, added 2026-10-19 for the
    # cost-gated swap eval (evaluation/MODEL_SWAP_FREE_PLAN.md §8).
    "z-ai/glm-5.3-flash",
    # (anthropic/claude-sonnet-5.5 and openai/gpt-6.1-sol were probed
    # 2026-10-19 and deliberately not retained — §9.)
})

#: Default answer-model resolution (single source of truth).
DEFAULT_ANSWER_MODEL = "minimax/minimax-m3.1-flash:free"

#: The answer model is served by OpenRouter — no other base URL is permitted.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ---------------------------------------------------------------------------
# Call accounting state
# ---------------------------------------------------------------------------
_LLM_CALL_COUNT = 0
_LLM_CALL_COUNT_LOCK = threading.Lock()


def llm_enabled() -> bool:
    """Return True only when the hard kill switch (``LLM_ENABLED``) is on.

    Read at call time (not import time) so a deployment or a test can toggle
    the switch without reloading modules.
    """
    return os.environ.get("LLM_ENABLED", "false").strip().lower() == "true"


def max_llm_calls() -> int:
    """Return the per-process REAL-call budget (``MAX_LLM_CALLS``).

    ``0`` (the default) blocks every real LLM call — a second, independent
    lock behind the kill switch.  Read at call time like :func:`llm_enabled`.
    """
    return int(os.environ.get("MAX_LLM_CALLS", "0"))


def validate_model(model: str) -> None:
    """Raise RuntimeError if *model* is not in ``ALLOWED_MODELS``."""
    if model not in ALLOWED_MODELS:
        raise RuntimeError(
            f"Model '{model}' is not in the allow-list {sorted(ALLOWED_MODELS)}. "
            "Refusing to make an LLM call — there is no fallback model or provider."
        )


def _increment_call_count() -> None:
    """Count one REAL LLM call and enforce the per-run budget.

    Called only on the real (non-stub) path, immediately before the HTTP
    transport.  Raises when the budget is exhausted (or is 0, which blocks
    every call); the raise happens BEFORE any network request.
    """
    global _LLM_CALL_COUNT
    cap = max_llm_calls()
    with _LLM_CALL_COUNT_LOCK:
        _LLM_CALL_COUNT += 1
        count = _LLM_CALL_COUNT
    if cap <= 0:
        raise RuntimeError(
            f"LLM call blocked: MAX_LLM_CALLS={cap} permits no real LLM calls. "
            "Set MAX_LLM_CALLS to a positive budget (and LLM_ENABLED=true) to allow them."
        )
    if count > cap:
        raise RuntimeError(
            f"LLM call budget exhausted: {count} calls > MAX_LLM_CALLS={cap}."
        )


def _log_llm_call(
    provider: str,
    model: str,
    call_site: str,
    *,
    outcome: str = "request",
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    """Log one outgoing LLM request (or its response token usage).

    Every real LLM request in the app is logged here with a UTC timestamp,
    provider, model and call site; response events add token counts when
    the provider supplies them.
    """
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    if outcome == "request":
        logger.info(
            "LLM_CALL ts=%s provider=%s model=%s call_site=%s",
            ts,
            provider,
            model,
            call_site,
        )
    else:
        logger.info(
            "LLM_RESPONSE ts=%s provider=%s model=%s call_site=%s "
            "prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            ts,
            provider,
            model,
            call_site,
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )


def get_llm_client(
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
):
    """The single approved factory for the app's answer-model client.

    Enforces, in order:

    1. Strict model allow-list (:data:`ALLOWED_MODELS`) — raises on any
       mismatch, even while the kill switch is off (a misconfigured
       ``RAG_LLM_MODEL`` must surface, never silently serve).
    2. Provider verification when live: the base URL must be OpenRouter
       (Laguna's provider) and the key must come from ``OPENROUTER_API_KEY``.
    3. Hard kill switch: when ``LLM_ENABLED=false`` the returned client is a
       no-op stub — ``call()`` serves deterministic canned text and makes
       zero network requests.

    There is no fallback provider and no fallback model: failures surface.
    """
    resolved_model = model or os.environ.get("RAG_LLM_MODEL") or DEFAULT_ANSWER_MODEL
    validate_model(resolved_model)

    if not api_key:
        api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not base_url:
        base_url = os.environ.get("OPENAI_BASE_URL") or OPENROUTER_BASE_URL

    if llm_enabled():
        host = (urlparse(base_url).hostname or "").lower()
        if not host.endswith("openrouter.ai"):
            raise RuntimeError(
                f"Base URL {base_url!r} is not OpenRouter; Laguna S 2.1 is served by "
                "OpenRouter only. Refusing to construct an LLM client."
            )
        if not os.environ.get("OPENROUTER_API_KEY") and os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "API key mismatch: OPENAI_API_KEY is set but OPENROUTER_API_KEY is not. "
                "Laguna S 2.1 is served by OpenRouter — set OPENROUTER_API_KEY."
            )

    # Import lazily so a missing optional dependency never blocks process
    # boot.  Resolve through the grounded_service module so callers/tests can
    # monkeypatch ``app.rag.generation.grounded_service.GroundedLLMClient``.
    from app.rag.generation.grounded_service import GroundedLLMClient

    client = GroundedLLMClient(model=resolved_model, api_key=api_key, base_url=base_url)
    if not llm_enabled():
        logger.info(
            "get_llm_client: LLM_ENABLED=false — returning no-op stub client (zero network)"
        )
    return client


def llm_call_count() -> int:
    """Return the number of REAL LLM calls made so far in this process."""
    with _LLM_CALL_COUNT_LOCK:
        return _LLM_CALL_COUNT


def reset_llm_call_count() -> int:
    """Reset the call counter; return the previous value."""
    global _LLM_CALL_COUNT
    with _LLM_CALL_COUNT_LOCK:
        previous = _LLM_CALL_COUNT
        _LLM_CALL_COUNT = 0
        return previous


def set_max_llm_calls(limit: int) -> int:
    """Set ``MAX_LLM_CALLS`` at runtime via the environment; return the previous value."""
    previous = max_llm_calls()
    os.environ["MAX_LLM_CALLS"] = str(limit)
    return previous
