"""Unit tests for the RetrievalCache class (app/rag/retrieval/cache.py).

Extracted from the module-level OrderedDict in app/rag/tasks.py so the cache
is an injectable, testable value object instead of global mutable state.

These tests are pure — no Flask app context, no Qdrant, no DB. They exercise
the LRU + TTL semantics directly.
"""

from __future__ import annotations

import time

from app.rag.retrieval.cache import RetrievalCache


class TestCacheBasic:
    def test_get_returns_none_for_missing_key(self):
        cache = RetrievalCache(max_size=10, ttl_seconds=60)
        assert cache.get("nope") is None

    def test_put_then_get_returns_value(self):
        cache = RetrievalCache(max_size=10, ttl_seconds=60)
        cache.put("q1", "value-1")
        assert cache.get("q1") == "value-1"

    def test_get_missing_key_does_not_populate(self):
        cache = RetrievalCache(max_size=10, ttl_seconds=60)
        cache.get("absent")
        assert cache.get("absent") is None
        assert len(cache) == 0


class TestCacheTTL:
    def test_entry_expires_after_ttl(self):
        """An entry past its TTL is treated as a miss and lazily removed."""
        cache = RetrievalCache(max_size=10, ttl_seconds=0.05)
        cache.put("k", "v")
        assert cache.get("k") == "v"  # fresh
        time.sleep(0.06)
        assert cache.get("k") is None  # expired

    def test_fresh_entry_within_ttl_survives(self):
        cache = RetrievalCache(max_size=10, ttl_seconds=60)
        cache.put("k", "v")
        assert cache.get("k") == "v"


class TestCacheLRU:
    def test_lru_eviction_over_max_size(self):
        """When max_size is exceeded, the least-recently-used entry is evicted."""
        cache = RetrievalCache(max_size=2, ttl_seconds=60)
        cache.put("a", 1)
        cache.put("b", 2)
        # Touch 'a' so 'b' becomes the LRU
        assert cache.get("a") == 1
        # Insert 'c' — 'b' should be evicted (it's LRU)
        cache.put("c", 3)
        assert cache.get("b") is None
        assert cache.get("a") == 1
        assert cache.get("c") == 3
        assert len(cache) == 2

    def test_mru_promotion_on_read(self):
        """A get() promotes the entry to most-recently-used."""
        cache = RetrievalCache(max_size=3, ttl_seconds=60)
        cache.put("x", 1)
        cache.put("y", 2)
        cache.put("z", 3)
        # Read 'x' → it's now MRU; 'y' is LRU
        assert cache.get("x") == 1
        cache.put("w", 4)  # should evict 'y' (LRU)
        assert cache.get("y") is None
        assert cache.get("x") == 1
        assert cache.get("z") == 3
        assert cache.get("w") == 4


class TestCacheClear:
    def test_clear_empties_cache(self):
        cache = RetrievalCache(max_size=10, ttl_seconds=60)
        cache.put("a", 1)
        cache.put("b", 2)
        assert len(cache) == 2
        cache.clear()
        assert len(cache) == 0
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_clear_then_reuse(self):
        cache = RetrievalCache(max_size=3, ttl_seconds=60)
        cache.put("a", 1)
        cache.clear()
        cache.put("b", 2)
        assert cache.get("a") is None
        assert cache.get("b") == 2
