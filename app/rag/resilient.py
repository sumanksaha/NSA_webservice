"""Resilient RAG pipeline — circuit breaker + retry for the full pipeline.

Wraps :func:`app.rag.tasks.run_generation_pipeline` (retrieve → generate →
verify) behind a circuit breaker that prevents cascading failures when
Qdrant or the LLM API is down.  Follows the retry + circuit-breaker pattern
from ``app/rag/retryable_embedding_client.py``.

When the circuit is **open**, calls fail fast with :class:`CircuitOpenError`
instead of hammering a dead service.  After ``cooldown_seconds``, the next
call is allowed through as a **half-open probe** — if it succeeds the
circuit closes; if it fails the cooldown resets.

The pipeline falls back to stub generation (no retrieval) when the circuit
is open, so users always get a degraded-but-functional response rather than
a hard error.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    """Raised when the circuit breaker is open (fail fast)."""


@dataclass
class CircuitState:
    """Mutable circuit-breaker state."""

    open: bool = False
    failure_count: int = 0
    last_failure: float = 0.0
    last_success: float = 0.0


class ResilientRAGPipeline:
    """Full RAG pipeline with circuit breaking + fallback.

    Args:
        pipeline_fn: The actual pipeline callable (default:
            :func:`app.rag.tasks.run_generation_pipeline`).
        fallback_fn: Called when the circuit is open — returns a
            degraded response (e.g. stub generation).  Defaults to a
            stub-only generator.
        failure_threshold: Consecutive failures that open the circuit.
        cooldown_seconds: How long the circuit stays open before
            attempting a half-open probe.
    """

    def __init__(
        self,
        pipeline_fn: Callable[..., dict[str, Any]] | None = None,
        fallback_fn: Callable[[str], dict[str, Any]] | None = None,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._state = CircuitState()

        if pipeline_fn is not None:
            self.pipeline_fn = pipeline_fn
        else:
            from app.rag.tasks import run_generation_pipeline
            self.pipeline_fn = run_generation_pipeline

        self.fallback_fn = fallback_fn or self._default_fallback

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        query: str,
        top_k: int = 10,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run the pipeline through the circuit breaker.

        Returns the pipeline result dict.  On circuit-open or pipeline
        failure, falls back to :attr:`fallback_fn`.
        """
        if self._circuit_open():
            logger.warning("Circuit OPEN — using fallback for query=%r", query)
            return self._safe_fallback(query, **kwargs)

        try:
            result = self.pipeline_fn(query=query, top_k=top_k, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()
            logger.warning("Pipeline failed (%d/%d): %s",
                           self._state.failure_count, self.failure_threshold, exc)
            return self._safe_fallback(query, **kwargs)

    def circuit_state(self) -> dict[str, Any]:
        """Return a serializable snapshot of the circuit state."""
        return {
            "open": self._state.open,
            "failure_count": self._state.failure_count,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "last_failure": self._state.last_failure,
            "last_success": self._state.last_success,
            "degraded_mode": self._state.open,
        }

    def reset(self) -> None:
        """Manually close the circuit and clear failure counters."""
        self._state = CircuitState()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _circuit_open(self) -> bool:
        """Check if the circuit is open (and possibly close it on cooldown)."""
        if not self._state.open:
            return False
        # Check if cooldown has elapsed => half-open probe.
        if time.monotonic() - self._state.last_failure >= self.cooldown_seconds:
            logger.info("Circuit HALF-OPEN — probing after cooldown")
            self._state.open = False
        return self._state.open

    def _on_success(self) -> None:
        """Reset failure state on success."""
        self._state.failure_count = 0
        self._state.last_success = time.monotonic()
        self._state.open = False

    def _on_failure(self) -> None:
        """Increment failure count; open circuit if threshold reached."""
        self._state.failure_count += 1
        self._state.last_failure = time.monotonic()
        if self._state.failure_count >= self.failure_threshold:
            self._state.open = True
            logger.warning("Circuit OPENED after %d consecutive failures",
                           self._state.failure_count)

    def _safe_fallback(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Run the fallback, catching any errors."""
        try:
            return self.fallback_fn(query, **kwargs)
        except Exception as exc:
            logger.error("Fallback pipeline also failed: %s", exc)
            return {
                "query": query,
                "answer": "",
                "error": f"All pipeline stages failed: {exc}",
                "groundedness_score": 0.0,
                "hallucination_detected": True,
                "citations": [],
                "retrieved_chunks": [],
            }

    def _default_fallback(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Default degraded response using stub LLM (no retrieval)."""
        from app.rag.generation import GroundedGenerationService

        try:
            service = GroundedGenerationService()
            rag_response = service.generate(query, [])
            return {
                "query": rag_response.query,
                "answer": rag_response.answer,
                "citations": [],
                "retrieved_chunks": [],
                "groundedness_score": rag_response.groundedness_score,
                "hallucination_detected": rag_response.hallucination_detected,
                "llm_model": rag_response.llm_model,
                "token_usage": rag_response.token_usage,
                "debug": {"degraded_mode": True, "reason": "circuit_open"},
            }
        except Exception as exc:
            return {
                "query": query,
                "answer": "",
                "error": str(exc),
                "groundedness_score": 0.0,
                "hallucination_detected": True,
                "citations": [],
                "retrieved_chunks": [],
            }
