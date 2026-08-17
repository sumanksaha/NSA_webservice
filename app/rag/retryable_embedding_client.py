"""Retryable embedding client with circuit breaker (Agent A, Phase 2 — Day 8).

Wraps any embedder (local ``EmbeddingService`` or a remote HTTP embedding
API) with:

- **Retry with exponential backoff** — the ``AIAssistantService`` httpx
  pattern: transient failures (429/408/503, connection/timeout errors) are
  retried up to ``max_attempts`` with ``backoff_base * 2**attempt`` sleeps;
  non-transient errors propagate immediately.
- **Circuit breaker** — after ``failure_threshold`` consecutive failures the
  circuit opens for ``cooldown_seconds``: calls fail fast with
  :class:`CircuitOpenError` instead of hammering a dead service.  A success
  (after cooldown, half-open probe) resets the breaker.

Both ``sleep_fn`` and ``monotonic_fn`` are injectable so tests stay
deterministic (no real sleeping).  ``embed_text`` / ``embed_batch`` /
``embed_chunks`` route through the same guarded call path, duck-typing the
``EmbeddingService`` interface.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: HTTP statuses treated as transient (retryable). Matches the
#: AIAssistantService retry set plus gateway timeouts.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class CircuitOpenError(RuntimeError):
    """Raised when the circuit breaker is open (fail fast)."""


class RetryableEmbeddingClient:
    """Wrap an embedder with retry + circuit breaking.

    Args:
        embedder: Object exposing ``embed_text`` / ``embed_batch`` /
            ``embed_chunks`` (e.g. :class:`EmbeddingService`), or a plain
            callable ``fn(texts: list[str]) -> list[list[float]]``.
        max_attempts: Maximum attempts per call (default 3).
        backoff_base: Base seconds for exponential backoff (default 0.5).
        failure_threshold: Consecutive failures that open the circuit.
        cooldown_seconds: How long the circuit stays open.
        sleep_fn: Injectable sleep (defaults to ``time.sleep``).
        monotonic_fn: Injectable clock (defaults to ``time.monotonic``).
    """

    def __init__(
        self,
        embedder: Any,
        *,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        sleep_fn: Callable[[float], None] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self._embedder = embedder
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = max(0.0, backoff_base)
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._sleep = sleep_fn or time.sleep
        self._monotonic = monotonic_fn or time.monotonic

        # Circuit state.
        self._consecutive_failures = 0
        self._open_until: float | None = None  # monotonic timestamp
        self._total_calls = 0
        self._total_retries = 0
        self._total_failures = 0

    # ------------------------------------------------------------------ #
    # Circuit breaker state
    # ------------------------------------------------------------------ #

    def circuit_open(self) -> bool:
        """True when the circuit is currently open (fail fast).

        Pure observation: does NOT transition state.  The half-open probe
        transition happens inside :meth:`_guarded` when a call is actually
        attempted after cooldown, so merely polling :meth:`circuit_state`
        never silently resets the breaker.
        """
        if self._open_until is None:
            return False
        return self._monotonic() < self._open_until

    def circuit_state(self) -> dict[str, Any]:
        """JSON-safe observability snapshot of the breaker."""
        return {
            "open": self.circuit_open(),
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self._failure_threshold,
            "cooldown_seconds": self._cooldown_seconds,
            "total_calls": self._total_calls,
            "total_retries": self._total_retries,
            "total_failures": self._total_failures,
        }

    def reset(self) -> None:
        """Manually close the circuit and clear counters."""
        self._consecutive_failures = 0
        self._open_until = None

    # ------------------------------------------------------------------ #
    # Public embedding API (duck-typing EmbeddingService)
    # ------------------------------------------------------------------ #

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text -> vector, with retry + circuit breaking.

        Object embedders return a flat vector directly; a plain-callable
        embedder receives ``[text]`` and returns a one-item list, which
        ``_call_embedder`` unwraps — so this method always returns a vector.
        """
        return self._guarded(lambda: self._call_embedder("embed_text", text))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts -> list[vectors], with retry + breaking."""
        return self._guarded(lambda: self._call_embedder("embed_batch", texts))

    def embed_chunks(self, chunks: list[Any]) -> list[list[float]]:
        """Embed chunk objects (or strings) -> list[vectors]."""
        return self._guarded(lambda: self._call_embedder("embed_chunks", chunks))

    def validate_vector_size(self, expected: int | None = None) -> bool:
        """Delegate to the wrapped embedder when it supports it."""
        validator = getattr(self._embedder, "validate_vector_size", None)
        if validator is None:
            return True  # cannot validate -> assume compatible
        return bool(validator(expected))

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _call_embedder(self, method: str, arg: Any) -> Any:
        """Dispatch to the wrapped embedder, normalizing return shapes.

        Object embedders follow the ``EmbeddingService`` contract
        (``embed_text`` -> flat vector, ``embed_batch``/``embed_chunks`` ->
        list of vectors).  A plain callable always returns a list of vectors,
        so the single-text path is unwrapped here to keep the two shapes
        consistent.
        """
        if callable(self._embedder) and not hasattr(self._embedder, method):
            texts = [arg] if isinstance(arg, str) else list(arg)
            vectors = self._embedder(texts) or []
            if isinstance(arg, str):
                if not vectors:
                    raise RuntimeError("embedder returned no vectors for a single text")
                return vectors[0]
            return vectors
        fn = getattr(self._embedder, method)
        return fn(arg)

    def _guarded(self, fn: Callable[[], Any]) -> Any:
        """Run ``fn`` with retry + circuit breaking. Never retries non-transient errors."""
        if self.circuit_open():
            raise CircuitOpenError("embedding circuit is open — failing fast")
        # Cooldown may have elapsed since the last observation: clear the open
        # marker here (when a call is actually attempted) to allow the probe.
        self._open_until = None

        self._total_calls += 1
        last_exc: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                result = fn()
                self._record_success()
                return result
            except CircuitOpenError:
                raise
            except Exception as exc:
                last_exc = exc
                if not self._is_transient(exc):
                    # Permanent errors (bad input, auth) raise immediately and
                    # never count toward the breaker — a client-side fault must
                    # not trip the circuit and block legitimate calls.
                    self._total_failures += 1
                    raise
                # Each failed transient attempt counts once towards the breaker.
                self._record_failure()
                if attempt < self._max_attempts - 1:
                    self._total_retries += 1
                    self._sleep(self._backoff_base * (2**attempt))

        raise RuntimeError(f"embedding failed after {self._max_attempts} attempts: {last_exc}") from last_exc

    def _is_transient(self, exc: Exception) -> bool:
        """Classify an exception as transient (retryable)."""
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is not None:
            return int(status) in RETRYABLE_STATUSES
        # Connection / timeout / DNS / transport errors are transient.
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return True
        name = type(exc).__name__.lower()
        # httpx.TransportError family: ConnectError, ReadTimeout, ConnectTimeout…
        return any(token in name for token in ("timeout", "connection", "connect", "network", "transport"))

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._open_until = None

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        self._total_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._open_until = self._monotonic() + self._cooldown_seconds
            logger.warning(
                "embedding circuit OPEN after %d consecutive failures (cooldown %.1fs)",
                self._consecutive_failures,
                self._cooldown_seconds,
            )


# End of retryable_embedding_client.py
