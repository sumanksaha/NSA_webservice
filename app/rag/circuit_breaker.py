"""3.9 — Per-domain circuit breaker for KG calls.

Each query type (case_law, provision_search, etc.) gets its own
circuit breaker that trips after N failures and expires after T seconds.
Prevents cascading failures when a specific domain is unhealthy.

Usage:
    breaker = CircuitBreaker.for_query_type("case_law", failure_threshold=3, timeout_seconds=60)
    if breaker.can_execute():
        result = kg_call()
        breaker.record_success()
    else:
        # Circuit open — skip KG call or use fallback
        logger.warning("KG circuit open for case_law domain")
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.shared.config import cfg


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-domain circuit breaker for KG calls.
    
    Attributes:
        failure_threshold: Number of failures before opening.
        timeout_seconds: How long to stay open before attempting half-open.
        last_failure: Timestamp of the most recent failure.
        failure_count: Consecutive failures recorded.
        state: Current circuit state.
    """
    failure_threshold: int = 3
    timeout_seconds: int = 60
    last_failure: float = 0.0
    failure_count: int = 0
    state: CircuitState = CircuitState.CLOSED
    _lock = threading.Lock()

    @classmethod
    def for_query_type(
        cls,
        query_type: str,
        failure_threshold: int | None = None,
        timeout_seconds: int | None = None,
    ) -> CircuitBreaker:
        """Factory method to get/create a breaker for a query type.
        
        Caches breakers per query_type for global reuse.
        """
        failure_threshold = failure_threshold or cfg.kg_failure_threshold or 5
        timeout_seconds = timeout_seconds or cfg.kg_timeout_seconds or 120
        return _BREAKER_REGISTRY.get(query_type) or _BREAKER_REGISTRY.setdefault(
            query_type,
            cls(failure_threshold=failure_threshold, timeout_seconds=timeout_seconds),
        )

    def can_execute(self) -> bool:
        """Check if a call is allowed (circuit closed or half-open)."""
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.HALF_OPEN:
                return True
            # OPEN
            elapsed = time.monotonic() - self.last_failure
            if elapsed >= self.timeout_seconds:
                # Try half-open
                self.state = CircuitState.HALF_OPEN
                return True
            return False

    def record_success(self) -> None:
        """Record a successful call — resets failure count."""
        with self._lock:
            self.failure_count = 0
            self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed call — may open the circuit."""
        with self._lock:
            self.failure_count += 1
            self.last_failure = time.monotonic()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    def __repr__(self) -> str:
        return f"CircuitBreaker(state={self.state.value}, failures={self.failure_count})"


# Global registry per query type
_BREAKER_REGISTRY: dict[str, CircuitBreaker] = defaultdict(
    lambda: CircuitBreaker()
)


def get_breaker(query_type: str) -> CircuitBreaker:
    """Get the circuit breaker for a query type, creating one if needed."""
    return _BREAKER_REGISTRY[query_type]


def reset_all_breakers() -> None:
    """Reset all breakers to CLOSED state (for testing)."""
    for breaker in _BREAKER_REGISTRY.values():
        breaker.state = CircuitState.CLOSED
        breaker.failure_count = 0
