"""Stable, bounded cache-key helpers.

Python's builtin ``hash()`` is salted per process (``PYTHONHASHSEED``), so its
values differ between runs and cannot be relied on as stable cache keys. These
helpers produce deterministic SHA-256 keys and enforce a simple FIFO size cap
so the in-memory caches stay bounded.
"""

from __future__ import annotations

import hashlib
from typing import Any

# Default maximum number of entries for the in-memory caches.
DEFAULT_CACHE_SIZE = 1000


def stable_key(text: str) -> str:
    """Return a stable, process-independent SHA-256 digest of ``text``.

    Unlike ``hash()``, the same input always produces the same key, on any
    process and any run, and collisions are cryptographically unlikely.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evict_if_full(cache: dict[str, Any], limit: int = DEFAULT_CACHE_SIZE) -> None:
    """Drop the oldest (FIFO) entry when ``cache`` has reached ``limit`` entries.

    Call this *before* inserting a new entry so the cache never exceeds ``limit``.
    """
    if len(cache) >= limit:
        del cache[next(iter(cache))]
