# ADR-0012: One shared model runtime for RAG encoders

- **Date:** 2026-10-20
- **Status:** Accepted
- **Context:** `app/rag/` dense embedding and cross-encoder reranking

## Context

Five call sites built their own `SentenceTransformer` / `CrossEncoder` with a
near-copy of the same lazy import and torch-thread cap:

- `EmbeddingService._get_encoder`
- `DenseRetriever._get_encoder`
- `Reranker._get_encoder`
- `EnsembleReranker._get_encoder`
- `RemoteEmbedClient._get_local_encoder` / `RemoteRerankClient._get_local_encoder`

Each copy cached only on its own instance. `build_hybrid_retriever` is cached
per collection, and the multi-domain router builds one stack per domain, so
each collection loaded its own copy of the same mpnet/CE weights — several
hundred MB apiece on the 8 GB deployment class the torch cap was written for.
Adding a model kind meant editing five copies, and the copies had already
drifted on failure policy (`ImportError` only vs. all exceptions).

## Decision

Add `app/rag/model_runtime.py` as the **single model construction seam**:

```python
get_sentence_transformer(model_name) -> Any | None
get_cross_encoder(model_name) -> Any | None
clear_model_cache() -> None
```

The module owns the process-wide `(kind, model_name)` cache, the lazy import,
the `cap_torch_threads()` call before construction, and the remembered
"package absent" state. The contract:

- **Injection still wins.** Every class keeps its `encoder=` constructor
  argument; tests and callers that supply an encoder never touch the cache.
- **Missing package → `None`**, remembered, so callers degrade as before.
- **Load failure propagates.** A present-but-broken model is not silently
  downgraded. The remote clients already wrap construction in their fallback
  path; local callers keep their existing failure mode.
- **`clear_model_cache()`** is for harnesses that need a cold start. Re-ingest
  deliberately does not clear it — the weights did not change.

## Consequences

- **Positive:** one copy of each model per process instead of one per
  collection; model construction policy has one home.
- **Positive:** the runtime is unit-testable with a fake `sentence_transformers`
  module (no downloads), including cache, thread-cap, absence and load-error
  behaviour.
- **Risk:** encoders are now shared across threads. Both HF wrappers are used
  read-only after construction (`encode` / `predict`), which is the same usage
  the previous per-instance copies had; the runtime itself only guards
  construction.
- **Rejected:** an `lru_cache` on each `_get_encoder` — still one model per
  class instance, still five copies of the import/thread-cap policy.

See `docs/RAG_ARCHITECTURE_IMPROVEMENT_PLAN.md` (Candidate 1) and
`tests/test_model_runtime.py`.
