"""Tests for the Agent A Phase 2 retryable embedding client.

Pins the retry + circuit-breaker contract: exponential-backoff retries on
transient failures (429/503/timeouts), immediate raise on permanent errors,
and the circuit opening after a failure threshold, failing fast while open,
and recovering after cooldown.  Injectable sleep/monotonic fakes keep the
tests deterministic (no real sleeping).
"""

from __future__ import annotations

import contextlib

import httpx

from app.rag.retryable_embedding_client import (
    CircuitOpenError,
    RetryableEmbeddingClient,
)

_NOOP_SLEEP = lambda _secs: None  # noqa: E731 - deterministic tests
_CLOCK = {"now": 1000.0}


def _monotonic() -> float:
    return _CLOCK["now"]


def _advance(seconds: float) -> None:
    _CLOCK["now"] += seconds


def _client(embedder, **kwargs):
    kwargs.setdefault("sleep_fn", _NOOP_SLEEP)
    kwargs.setdefault("monotonic_fn", _monotonic)
    return RetryableEmbeddingClient(embedder, **kwargs)


class _FakeEmbedder:
    """Embedder whose calls can fail N times then succeed."""

    def __init__(self, failures=0, error=None, vectors=None):
        self.failures = failures
        self.error = error
        self.vectors = vectors or [[0.1, 0.2, 0.3]]
        self.calls = 0

    def embed_text(self, text):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error or httpx.ConnectError("network down")
        return self.vectors[0]

    def embed_batch(self, texts):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error or httpx.ConnectError("network down")
        return self.vectors * len(texts)


class TestRetry:
    def test_success_on_first_attempt(self):
        embedder = _FakeEmbedder(failures=0)
        client = _client(embedder, max_attempts=3)
        assert client.embed_text("hello") == [0.1, 0.2, 0.3]
        assert embedder.calls == 1
        assert client.circuit_state()["total_retries"] == 0

    def test_retries_transient_then_succeeds(self):
        embedder = _FakeEmbedder(failures=2)  # two transient failures
        client = _client(embedder, max_attempts=3, backoff_base=0.5)
        result = client.embed_text("hello")
        assert result == [0.1, 0.2, 0.3]
        assert embedder.calls == 3  # original + 2 retries
        assert client.circuit_state()["total_retries"] == 2
        assert client.circuit_state()["open"] is False

    def test_raises_after_max_attempts(self):
        embedder = _FakeEmbedder(failures=99)  # always fails
        client = _client(embedder, max_attempts=3)
        try:
            client.embed_text("hello")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "3 attempts" in str(exc)
        assert embedder.calls == 3

    def test_non_transient_error_raises_immediately(self):
        embedder = _FakeEmbedder(failures=1, error=ValueError("bad payload"))
        client = _client(embedder, max_attempts=3)
        try:
            client.embed_text("hello")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        assert embedder.calls == 1  # no retry on permanent errors

    def test_http_429_retryable(self):
        class _RateLimited:
            def __init__(self):
                self.calls = 0

            def embed_text(self, text):
                self.calls += 1
                if self.calls == 1:
                    raise httpx.HTTPStatusError(
                        "rate limited",
                        request=httpx.Request("POST", "http://x"),
                        response=httpx.Response(429),
                    )
                return [0.5]

        client = _client(_RateLimited(), max_attempts=3)
        assert client.embed_text("q") == [0.5]
        assert client.circuit_state()["total_retries"] == 1

    def test_embed_batch_routes_through_retry(self):
        embedder = _FakeEmbedder(failures=1)
        client = _client(embedder, max_attempts=3)
        vectors = client.embed_batch(["a", "b"])
        assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
        assert embedder.calls == 2


class TestCircuitBreaker:
    def test_opens_after_failure_threshold(self):
        embedder = _FakeEmbedder(failures=99)
        client = _client(embedder, max_attempts=1, failure_threshold=3, cooldown_seconds=60)
        for _ in range(3):
            with contextlib.suppress(RuntimeError):
                client.embed_text("q")
        state = client.circuit_state()
        assert state["open"] is True
        assert state["consecutive_failures"] == 3

    def test_fails_fast_while_open(self):
        embedder = _FakeEmbedder(failures=99)
        client = _client(embedder, max_attempts=1, failure_threshold=2, cooldown_seconds=60)
        for _ in range(2):
            with contextlib.suppress(RuntimeError):
                client.embed_text("q")
        # Circuit is open -> the embedder must NOT be called again.
        calls_before = embedder.calls
        try:
            client.embed_text("q")
            raise AssertionError("expected CircuitOpenError")
        except CircuitOpenError:
            pass
        assert embedder.calls == calls_before  # fail fast

    def test_recovers_after_cooldown(self):
        embedder = _FakeEmbedder(failures=99)
        client = _client(embedder, max_attempts=1, failure_threshold=2, cooldown_seconds=10)
        for _ in range(2):
            with contextlib.suppress(RuntimeError):
                client.embed_text("q")
        assert client.circuit_open() is True
        _advance(11)  # cooldown elapsed
        assert client.circuit_open() is False  # half-open probe allowed
        with contextlib.suppress(RuntimeError):
            client.embed_text("q")  # still failing -> re-opens
        assert client.circuit_state()["consecutive_failures"] == 3

    def test_success_resets_circuit(self):
        embedder = _FakeEmbedder(failures=2)
        client = _client(embedder, max_attempts=2, failure_threshold=2, cooldown_seconds=60)
        for _ in range(2):
            with contextlib.suppress(RuntimeError):
                client.embed_text("q")
        assert client.circuit_open() is True
        _advance(61)
        # Next attempt succeeds -> breaker resets.
        embedder.failures = 0
        client.embed_text("q")
        state = client.circuit_state()
        assert state["open"] is False
        assert state["consecutive_failures"] == 0

    def test_reset_closes_circuit_manually(self):
        embedder = _FakeEmbedder(failures=99)
        client = _client(embedder, max_attempts=1, failure_threshold=1, cooldown_seconds=60)
        with contextlib.suppress(RuntimeError):
            client.embed_text("q")
        assert client.circuit_open() is True
        client.reset()
        assert client.circuit_open() is False
        assert client.circuit_state()["consecutive_failures"] == 0

    def test_observing_state_never_resets_breaker(self):
        """circuit_open()/circuit_state() are pure — polling must not probe."""
        embedder = _FakeEmbedder(failures=99)
        client = _client(embedder, max_attempts=1, failure_threshold=1, cooldown_seconds=60)
        with contextlib.suppress(RuntimeError):
            client.embed_text("q")
        assert client.circuit_open() is True
        _advance(61)  # cooldown elapsed...
        # ...but merely observing does NOT transition to half-open:
        assert client.circuit_state()["open"] is False
        with contextlib.suppress(RuntimeError):
            client.embed_text("q")
        # The probe call re-opened the circuit with a fresh cooldown.
        assert client.circuit_open() is True
        assert client.circuit_state()["consecutive_failures"] == 2

    def test_non_transient_errors_never_open_circuit(self):
        """Permanent errors raise immediately and don't trip the breaker."""
        embedder = _FakeEmbedder(failures=99, error=ValueError("bad payload"))
        client = _client(embedder, max_attempts=1, failure_threshold=2, cooldown_seconds=60)
        for _ in range(3):
            with contextlib.suppress(ValueError):
                client.embed_text("q")
        state = client.circuit_state()
        assert state["open"] is False
        assert state["consecutive_failures"] == 0  # breaker only counts transient

    def test_plain_callable_embedder(self):
        calls = []

        def _embed(texts):
            calls.append(texts)
            return [[0.7, 0.8] for _ in texts]

        client = _client(_embed, max_attempts=1)
        assert client.embed_text("single") == [0.7, 0.8]
        assert calls == [["single"]]
        vectors = client.embed_batch(["a", "b"])
        assert vectors == [[0.7, 0.8], [0.7, 0.8]]
        assert calls[-1] == ["a", "b"]
