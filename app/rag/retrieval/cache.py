"""Thread-safe LRU+TTL cache for deterministic retrieval results.

Extracted from the module-level ``OrderedDict`` in ``app/rag/tasks.py`` so the
cache is an injectable, testable value object instead of global mutable state.

The cache memoizes the deterministic, LLM-free retrieval step
(``QueryClassifier -> HybridRetriever.retrieve``) so repeated identical queries
skip the Qdrant round-trip within the TTL.  TTL is lazy-reaped on read (no
sweeper thread); LRU eviction enforces ``max_size`` on write.

``cachetools`` is an optional dep — if it were the backing store we'd fall back
to stdlib here.  Stdlib ``OrderedDict`` + ``threading.Lock`` = zero deps.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Hashable
from typing import Any


class RetrievalCache:
    """Thread-safe LRU+TTL cache.

    Args:
        max_size: Maximum number of entries before the least-recently-used is
            evicted (0 disables the cache entirely).
        ttl_seconds: Time-to-live for each entry.  Expired entries are
            lazily removed on read.
    """

    def __init__(self, max_size: int = 512, ttl_seconds: float = 600) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: Hashable) -> bool:
        return key in self._data

    def get(self, key: Hashable) -> Any | None:
        """Return the cached value for *key*, or ``None`` (miss / expired).

        Fresh hits are promoted to most-recently-used (LRU).  Expiry is checked
        on read so stale entries are lazily reaped without a sweeper.
        """
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if now >= expires_at:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key: Hashable, value: Any) -> None:
        """Store *value* under *key* with TTL, evicting the LRU entry if over cap."""
        with self._lock:
            self._data[key] = (time.monotonic() + self._ttl, value)
            if len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        """Drop every cached entry."""
        with self._lock:
            self._data.clear()
