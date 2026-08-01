"""Performance profiling utilities for the Legal Paragraph Detection Engine."""

from __future__ import annotations

import threading
import time
import types


class PerformanceProfiler:
    """Simple thread-safe profiler for processing operations.

    Records per-operation durations so callers can inspect average and total
    processing times.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._operations: dict[str, list[float]] = {}

    def record(self, operation: str, duration: float) -> None:
        """Record a single operation duration (seconds)."""
        with self._lock:
            self._operations.setdefault(operation, []).append(duration)

    def time(self, operation: str) -> _PerformanceTimer:
        """Return a context manager that records elapsed time for ``operation``."""
        return _PerformanceTimer(self, operation)

    def get_stats(self, operation: str | None = None) -> dict[str, float | int]:
        """Return timing statistics for one operation or all operations."""
        with self._lock:
            if operation is not None:
                durations = self._operations.get(operation, [])
                return _summarize(operation, durations)

            stats: dict[str, float | int] = {}
            for name, durations in self._operations.items():
                stats.update(_summarize(name, durations))
            return stats

    def clear(self) -> None:
        """Clear all recorded timings."""
        with self._lock:
            self._operations.clear()


class _PerformanceTimer:
    """Context manager that records elapsed time on exit."""

    def __init__(self, profiler: PerformanceProfiler, operation: str) -> None:
        self._profiler = profiler
        self._operation = operation
        self._started: float | None = None

    def __enter__(self) -> _PerformanceTimer:
        self._started = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        duration = time.perf_counter() - (self._started or time.perf_counter())
        self._profiler.record(self._operation, duration)


def _summarize(name: str, durations: list[float]) -> dict[str, float | int]:
    """Compute count/total/min/max/average for a list of durations."""
    if not durations:
        return {f"{name}.count": 0, f"{name}.total": 0.0, f"{name}.avg": 0.0}
    return {
        f"{name}.count": len(durations),
        f"{name}.total": sum(durations),
        f"{name}.min": min(durations),
        f"{name}.max": max(durations),
        f"{name}.avg": sum(durations) / len(durations),
    }
