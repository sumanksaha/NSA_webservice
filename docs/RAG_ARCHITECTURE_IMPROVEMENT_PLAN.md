# RAG Module — Architecture Deepening (2026-10-20)

Architecture pass over `app/rag/` using the codebase-design vocabulary from
`CONTEXT.md` (**module**, **interface**, **depth**, **seam**, **adapter**,
**leverage**, **locality**). The goal was depth at the seams the RAG pipeline
actually crosses, not a file move.

## Method

1. Built the intra-package import graph (183 modules, 344 edges) to find
   cycles, fan-in hubs and layering breaks.
2. Scanned every function ≥ 12 lines for duplicated bodies and duplicate
   names, then read the surviving copies to separate harmless look-alikes from
   **drifted seams** (two copies with different behaviour).
3. Ranked candidates by the deletion test, leverage and blast radius.

## What the module looks like

`app/rag/` is already deep in the places that were deepened before
(`QueryUnderstanding`, `FoodView`, `Provision currency`, `Retry recovery`,
`retrieval/factory.py`, `retrieval/stages.py`). The remaining drag is not big
shallow modules; it is **construction logic duplicated at five or six call
sites**, where the copies drift because nothing forces agreement.

Measured smells:

| Smell | Count | Example |
| --- | --- | --- |
| `SentenceTransformer` / `CrossEncoder` loaders | 5 sites (+2 remote fallbacks) | `EmbeddingService`, `DenseRetriever`, both rerankers |
| Qdrant client constructors | 2 | store + dense retriever, different config resolution |
| Filter converters | 2 (drifted) | store dropped list expansion and boolean pass-through |
| Chunk field readers | 6+ | `_chunk_field`, `_ctext`, `_chunk_id`, `_cid`, `_f`, `_take` |
| Import cycles | 3 | `food_view ↔ tasks`, `retrieval ↔ reference_*`, `benchmark ↔ decomposition_metrics` |
| Orphan fragment modules | ~20 | `evaluation/_part*.py`, `feedback/_cmp_part*.py` |

## Implemented

### Candidate 1 — `ModelRuntime` (shared encoder cache) ✅

**Module:** `app/rag/model_runtime.py` · **ADR-0012**

Before, each per-collection retriever/reranker loaded its own copy of the same
mpnet/CE weights. The runtime owns construction: lazy import, torch-thread cap,
one process-wide `(kind, model_name)` cache, remembered package absence, and a
cold-start `clear_model_cache()`. Constructor injection still wins, so tests
never touch the registry.

**Leverage:** every collection shares one copy of each model.
**Locality:** model-loading policy and failure semantics live in one file.
**Wired:** `EmbeddingService`, `DenseRetriever`, `Reranker`, `EnsembleReranker`,
`RemoteEmbedClient`, `RemoteRerankClient`.

### Candidate 2 — Qdrant construction/filter seam ✅

**Module:** `app/rag/qdrant_client.py` · **ADR-0013**

`build_qdrant_client()` is now the single client rule (config via `cfg`, so
Celery/ingestion works without a Flask app context). `build_filter()` is the
single flat-dict → filter rule: pre-shaped boolean filters pass through and
list values expand. Both `_build_filter` methods delegate, preserving the seam
`section_scope` and the tests already cross.

**Fixed drift:** the store path (sparse/hybrid/scroll/delete) now supports the
filter shapes the dense path already supported.

### Candidate 3 — Point → chunk conversion ✅

**Module:** `app/rag/retrieval/result.py` · **ADR-0013**

`RetrievedChunk.from_qdrant_point()` replaces `tasks._point_to_chunk`. The
`food_view → tasks` import cycle (a stage importing the orchestrator for a pure
data conversion) is gone. `tasks.py` also lost a malformed module docstring
(a parenthesised string expression, so the module had no `__doc__`).

### Housekeeping

- `tests/test_ensemble_reranker.py`: the cached `FakeHybrid` had drifted behind
  the pipeline signature (`form_query`, `rrf_k`). Fixed with `**kwargs`; both
  previously-failing wiring tests now pass and act as regression coverage.
- New tests: `tests/test_model_runtime.py` (14) and `tests/test_qdrant_seams.py`
  (11).

## Verification

```
python -m pytest tests/test_model_runtime.py tests/test_qdrant_seams.py
python -m pytest tests/test_embedding_service.py tests/test_dense_retriever.py \
  tests/test_reranker.py tests/test_ensemble_reranker.py tests/test_remote_embedder.py \
  tests/test_remote_reranker.py tests/test_qdrant_client.py tests/test_section_scope_arm.py \
  tests/test_food_intent_retrieval.py
```

Expected: 300 passed (25 new + 275 affected). Ruff check + format clean on every
touched file.

## Remaining backlog (not done in this pass)

### C4 — Chunk access seam (medium value, low risk)

Six modules each read chunk fields with a different object/dict policy:

- `retrieval/evidence_selector.py` — `_chunk_id` (None), `_chunk_text` (dict-aware)
- `retrieval/decision_selector.py` — `_chunk_id` (None), `_take`
- `retrieval/legal_reranker.py` — `_cid` (`""`), `_ctext`
- `retrieval/parent_reconstruction.py` — `_cid`, `_ctext`, `_f`
- `retrieval/validation.py` — `_ctext`
- `provision_extractor/isolator.py`, `legal_identity.py`, `provision_metadata.py` — `_chunk_field`

Proposal: `app/rag/retrieval/chunk.py` with `field`, `text`, `chunk_id`,
`score`, `set_score` and dict/object parity pinned once. Migrate the callers;
keep `canonical_unit_id` in the selector until its parse rules move.
Acceptance: one implementation of each accessor; parity tests for dict and
`RetrievedChunk` inputs; no change to `RetrievedChunk`'s interface.

### C5 — Delete the orphan fragment modules (high value, zero risk)

`evaluation/_part2..8.py`, `_insert_*.py`, `feedback/_cmp_part*.py`,
`feedback/_impr_part*.py`, `feedback/_fix_*.py` have no production importer.
They are leftovers from script-generated file assembly and inflate the RAG
surface by ~20 modules. Deletion test: no caller, so complexity vanishes.
Caveat: `scripts/check_import_side_effects.py` lists `_part1`, `_fix_order`,
`_write_evaluator` as import targets — update or retire that dev script in the
same change.

### C6 — Break the evaluation cycle (low value)

`evaluation/benchmark.py ↔ decomposition_metrics.py` import each other
(one side under `TYPE_CHECKING` at import time, one at call time). Move the
shared `QueryBenchmark` shape to a neutral module or a protocol. Low priority:
both are offline harnesses, not the request path.

### C7 — `QueryUnderstanding` consumers (in flight, do not duplicate)

`app/rag/query_type_registry.py` (currently untracked) is the in-progress
canonical query-type vocabulary. The next pass should finish migrating
`query_classifier` / `legal_query_classifier` onto it rather than adding a
parallel registry in this refactor.
