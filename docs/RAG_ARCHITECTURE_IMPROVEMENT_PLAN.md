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
| Orphan fragment modules | 34 | `evaluation/_part*.py`, `feedback/_cmp_part*.py`, `tests/_test_workflow_*` |

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

### Candidate 4 — Chunk access seam ✅

**Module:** `app/rag/retrieval/chunk.py` · **ADR-0014**

Eleven modules re-implemented the same `isinstance(chunk, dict)` branch for
reading and writing chunk fields, with drifted defaults (`None` vs `""`) and
score coercion. `field`, `text`, `chunk_id`, `score`, `set_field` and
`set_score` now own that policy once; payload dicts and `RetrievedChunk`
objects are pinned to the same behaviour at every stage.

**Fixed drift:** `EvidenceSet.chunk_ids` previously only worked for object
chunks, so serialized payloads lost their ids; score coercion now guards
malformed values in every consumer.

**Wired:** `evidence_selector`, `decision_selector`, `legal_reranker`,
`parent_reconstruction`, `validation`, `provision_metadata`, `legal_identity`,
`provision_extractor/isolator`, `chunk_quality`, and
`evaluation/textmatch` (the last two keep their public names as thin
delegates). `RetrievedChunk`'s interface is unchanged; `canonical_unit_id`
stays in the selector because it owns legal parse rules.

### Candidate 5 — Delete orphan codegen fragments ✅

**Scope:** `app/rag/evaluation/`, `app/rag/feedback/`, `tests/` · **ADR-0015**

34 one-shot generator/fragment modules assembled `workflow_evaluator.py`,
`workflow_comparator.py` and `workflow_improvement_engine.py`, then stayed in
the package with no production or test importer. One of them,
`_write_evaluator.py`, wrote its target source at import time and corrupted a
live evaluation.

Deleted the tracked `_part*`, `_insert_*`, `_cmp_part*`, `_impr_part*`,
`_write_evaluator`, `_test_improvement` / `_test_impr` and
`tests/_test_workflow_*` fragments, plus the ignored `_fix_*.py` artifacts.
Kept the completed modules they produced.

**Leverage:** 34 modules of accidental surface disappear; there is no behaviour
to preserve because nothing imported them.
**Locality:** the only remaining codegen concern is the guard, not the
generators. `scripts/check_import_side_effects.py` stays as the detector and
`module_health_check.py` keeps the underscore-prefix import skip; both comments
now describe the deleted incident in the past tense.
**Config:** removed the broad `"app/rag/evaluation/*"` ruff exception block
that existed only for these generators.

### Candidate 6 — Neutral benchmark entry ✅

**Module:** `app/rag/evaluation/benchmark_entry.py` · **ADR-0016**

`benchmark.py` called `decomposition_metrics` at scoring time, while
`decomposition_metrics.py` imported `QueryBenchmark` from `benchmark.py` under
`TYPE_CHECKING`. The runtime graph was safe, but the type graph was circular.

`QueryBenchmark` moved to a dependency-free `benchmark_entry.py`.
`benchmark.py` re-exports it, so existing imports keep working;
`decomposition_metrics.py` imports the neutral class at runtime and no longer
mentions `benchmark` at all.

**Boundary:** `DecompositionBenchmark` and the scoring methods stay in
`benchmark.py`; only the shared data shape moved.

### Candidate 7 — Query-type registry consumers ✅

**Modules:** `app/rag/retrieval/query_classifier.py`,
`app/rag/retrieval/legal_query_classifier.py`,
`app/rag/retrieval/query_understanding.py` · **ADR-0017**

The registry existed but two classifiers still carried parallel vocabularies.
`query_classifier` had hand-written `CLASSIFIER_TRIAGE` / `_LABEL_TO_TYPE`
tables keyed by legacy labels; `legal_query_classifier` keyed its patterns and
config table by surface spellings; `normalize_query_type()` repeated the
registry's string surgery.

Now both pattern tables are keyed by canonical ids. The legal classifier emits
each type's declared `surface`, and `QUERY_TYPE_CONFIGS` is projected through
the registry. `QueryType` remains the legacy runtime enum, but its canonical
map, triage table, and classifier label map are derived at import time and
fail loudly if any value is unregistered. `get_config()` and
`normalize_query_type()` resolve aliases through the registry, so `duty` and
`amendment_query` land on `obligation` and `amendment`.

**Boundary:** `QueryType` is not deleted; callers and persisted values use its
member names. Only the vocabulary that used to drift is derived.

### Housekeeping

- `tests/test_ensemble_reranker.py`: the cached `FakeHybrid` had drifted behind
  the pipeline signature (`form_query`, `rrf_k`). Fixed with `**kwargs`; both
  previously-failing wiring tests now pass and act as regression coverage.
- New tests: `tests/test_model_runtime.py` (14), `tests/test_qdrant_seams.py`
  (11) and `tests/test_chunk_access.py` (10).

## Verification

```
python -m pytest tests/test_model_runtime.py tests/test_qdrant_seams.py
python -m pytest tests/test_embedding_service.py tests/test_dense_retriever.py \
  tests/test_reranker.py tests/test_ensemble_reranker.py tests/test_remote_embedder.py \
  tests/test_remote_reranker.py tests/test_qdrant_client.py tests/test_section_scope_arm.py \
  tests/test_food_intent_retrieval.py
python -m pytest tests/test_chunk_access.py tests/test_evidence_selector.py \
  tests/test_decision_selector.py tests/test_food_intent_retrieval.py \
  tests/test_provision_extractor.py tests/test_evidence_structure_integration.py \
  tests/test_chunk_quality.py tests/test_eval_framework.py
python -m pytest tests/test_workflow_improvement.py tests/test_check_import_side_effects.py
python -m pytest tests/test_rag_evaluation_imports.py tests/test_rag_phase3_requirements.py tests/test_rag_phase4_measurement.py
python -m pytest tests/test_query_type_registry.py
python -m pytest tests/test_query_classifier.py tests/test_legal_query_classifier_part_a.py tests/test_query_understanding.py
python scripts/check_import_side_effects.py
python -m ruff check app/rag/evaluation app/rag/feedback scripts/check_import_side_effects.py scripts/guard_import_side_effects.py
```

Expected: 300 passed (Candidates 1–3), then 238 passed (C4), the
workflow/import-guard suite green (C5), 63 passed for the evaluation boundary
tests (C6), and 32 passed for the query-type registry suite (C7). The broader
classifier suite is 61 passed with four pre-existing, unrelated failures
(`section_numbers` parser key, two authority/enforcement tie expectations, and
form-reference detection); C7 changes no emitted classifier surface. Ruff
check is clean on every touched file; the new modules and test files are
format-clean. The import-side-effect scanner reports ``all clean`` after the
fragment deletion.

## Remaining backlog

None from this pass. The registry migration is complete; the four classifier
test failures above are pre-existing and belong to a separate accuracy pass.
