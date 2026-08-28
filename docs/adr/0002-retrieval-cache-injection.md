# ADR-0002: RetrievalCache Injection Contract

- **Date:** 2026-08-27
- **Status:** Accepted
- **Context:** RAG retrieval cache (`app/rag/tasks.py`)
- **Decision:** Promote the module-level `OrderedDict` cache to an injectable
  `RetrievalCache` class; keep a singleton for backward compatibility.

## Context

`run_retrieval_pipeline` memoized retrieval results in a module-level
`OrderedDict` (`_RETRIEVAL_CACHE`) guarded by a `threading.Lock`. Tests
invalidated it via `clear_retrieval_cache()` (a global-clear function). The
cache logic was untestable in isolation — tests had to spin up a Flask app
context just to exercise `run_retrieval_pipeline` and observe the LRU/TTL
semantics.

## Decision

Extract `RetrievalCache` into `app/rag/retrieval/cache.py` (a thread-safe
LRU+TTL value object with `get()`, `put()`, `clear()`). A module-level
`_default_cache` singleton backs the existing `clear_retrieval_cache()`
shim so all existing tests pass unchanged.

`run_retrieval_pipeline` gains an optional `cache: RetrievalCache | None = None`
parameter:

```
result = run_retrieval_pipeline(query, cache=RetrievalCache(max_size=0))  # bypass
result = run_retrieval_pipeline(query)  # uses _default_cache
```

## Consequences

- **Positive:** Cache is testable with 9 pure unit tests (no Flask/DB/Qdrant).
  Tests can inject a zero-size cache for guaranteed no-cache behavior.
- **Positive:** The `_cache_get`/`_cache_put` private shims were removed
  (dead after the `cache` parameter landed). `clear_retrieval_cache` survives
  as the only backward-compat function.
- **Risk:** None. The singleton is the zero-diff path for all callers that
  don't pass `cache`.
