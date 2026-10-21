# ADR-0013: One Qdrant construction/filter seam and point conversion

- **Date:** 2026-10-20
- **Status:** Accepted
- **Context:** `app/rag/qdrant_client.py`, `app/rag/retrieval/dense_retriever.py`,
  `app/rag/retrieval/result.py`

## Context

Two modules constructed a Qdrant client and converted caller filters, and the
copies had drifted:

- `QdrantStore._get_client` read `current_app.config` directly and would raise
  outside a Flask app context; `DenseRetriever._get_client` had the same
  app-context requirement. The config seam (`cfg`, Pattern A) exists precisely
  so Celery/ingestion paths resolve `RAG_QDRANT_URL` from env.
- `QdrantStore._build_filter` wrapped a pre-shaped boolean filter
  (`{"must": ...}`) as a literal `{"key": "must", ...}` clause and did not
  expand list values. `DenseRetriever._build_filter` handled both. The store
  path (sparse/hybrid/scroll/delete) was therefore subtly less capable than
  the dense path.

Separately, `tasks.py` owned `_point_to_chunk`, and a retrieval stage
(`food_view._fetch_siblings`) imported it from the orchestrator — a genuine
`food_view → tasks` import cycle for a pure data conversion.

## Decision

- `build_qdrant_client()` in `app/rag/qdrant_client.py` is the only client
  construction rule: lazy import, `RAG_QDRANT_URL` / `RAG_QDRANT_API_KEY` via
  `cfg` (works inside and outside an app context), `None` when unavailable.
  `QdrantStore._get_client` and `DenseRetriever._get_client` delegate to it;
  injected clients still win.
- `build_filter()` is the only flat-dict → Qdrant-filter rule: empty → `{}`,
  pre-shaped filters pass through, list values expand to one `must` clause
  each. Both `_build_filter` methods remain as thin delegating wrappers because
  `section_scope` and the tests cross that seam.
- `RetrievedChunk.from_qdrant_point(point, chunk_id=None, payload=None)` in
  `app/rag/retrieval/result.py` owns the point/dict → chunk conversion.
  `tasks._point_to_chunk` is deleted; the `food_view → tasks` edge is gone.

## Consequences

- **Positive:** one filter rule for dense, sparse, hybrid, scroll and delete;
  the store gains list expansion and boolean pass-through.
- **Positive:** query-side retrieval no longer needs a Flask app context.
- **Positive:** stage modules can build `RetrievedChunk`s without importing the
  orchestrator (cycle removed).
- **Risk:** none material — both changes are behaviour-preserving for the
  previously-supported flat single-value filters; the only behaviour change is
  that previously-broken shapes now work.

See `docs/RAG_ARCHITECTURE_IMPROVEMENT_PLAN.md` (Candidates 2–3) and
`tests/test_qdrant_seams.py`.
