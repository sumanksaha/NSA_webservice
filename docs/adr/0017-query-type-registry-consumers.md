# ADR-0017: Finish the query-type registry migration

- **Date:** 2026-10-20
- **Status:** Accepted
- **Scope:** `app/rag/query_type_registry.py`, `app/rag/retrieval/query_classifier.py`, `app/rag/retrieval/legal_query_classifier.py`, `app/rag/retrieval/query_understanding.py`

## Context

`app/rag/query_type_registry.py` owns the canonical query-type ids, every
legacy alias, the emitted surface spellings, directives, budgets, and the set
of types the legal classifier may emit. The generation layer and the legal
classifier already read it.

Two classifiers still carried parallel vocabularies:

- `query_classifier.py` declared `QueryType` separately, plus a hand-written
  `CLASSIFIER_TRIAGE` and a `_LABEL_TO_TYPE` table keyed by legacy labels
  (`"section"`, `"provision"`, `"general"`).
- `legal_query_classifier.py` keyed `_TYPE_PATTERNS` by surface strings
  (`"cross-reference"`, `"direct provision"`, `"insufficient-evidence"`) and
  keyed `QUERY_TYPE_CONFIGS` by the same surface strings. The registry's
  canonical ids and the classifier's spellings could drift independently.
- `query_understanding.normalize_query_type()` repeated the registry's
  hyphen/space surgery and did not resolve legacy aliases such as `duty` or
  `amendment_query`.

`tests/test_query_type_registry.py` already pinned that every `QueryType`
value resolves. It did not pin that the classifier pattern keys themselves
were canonical or that the config table was projected from the registry.

## Decision

### Classifier patterns use canonical ids

`_QUERY_PATTERNS` and `_TYPE_PATTERNS` are now keyed by canonical ids:
`section_lookup`, `provision_search`, `general_qa`, `cross_reference`,
`direct_provision`, `insufficient_evidence`.

At import time:

- every `QueryType` value is resolved through `registry.resolve()`;
- the canonical -> runtime map (`_RUNTIME_TYPE_BY_CANONICAL`) is derived from
  the enum, not hand-written;
- every classifier pattern label is asserted to be canonical.

`CLASSIFIER_TRIAGE` is derived by projecting the registry's canonical ids and
every alias onto that runtime map. `_LABEL_TO_TYPE` is derived from the
canonical pattern labels and the same map.

### The legal classifier emits declared surfaces

Pattern matching still returns the exact strings frozen evaluation artifacts
expect (`direct provision`, `cross-reference`, `insufficient-evidence`), but
the strings now come from `QueryTypeSpec.emitted`. Changing a surface is a
one-line registry migration rather than three literal edits.

`QUERY_TYPE_CONFIGS` is projected from a canonical-keyed config table through
`REGISTRY[canonical].emitted`. The config table is asserted to cover exactly
`CLASSIFIER_TARGETS`.

`get_config()` resolves the caller's spelling through the registry first, so
canonical ids (`cross_reference`), emitted surfaces (`cross-reference`), and
benchmark spellings (`Cross-reference`) all reach the same config. Legacy
`duty` reaches the `obligation` config.

### QueryUnderstanding normalizes through the registry

`normalize_query_type()` now calls `registry.resolve()` and returns the
canonical id. It keeps the historical `general_qa` -> `general` routing bucket
and preserves unknown values in normalized form so they stay visible.

## Consequences

- **Positive:** there is one vocabulary. Classifier labels, config keys,
  runtime enum values, and `QueryUnderstanding` normalization all agree with
  the registry.
- **Positive:** alias handling is fixed at every boundary:
  `get_config("duty")` and `normalize_query_type("duty")` both reach
  `obligation`; `cross_reference` and `cross-reference` reach the same config.
- **Positive:** drift fails at import time or in
  `tests/test_query_type_registry.py`, not in a silent default.
- **Boundary:** `QueryType` remains the legacy runtime routing enum. It is not
  deleted because callers and persisted values use its member names; it is now
  a compatibility view whose canonical owner is the registry.
- **No behaviour change:** the legal classifier's emitted surfaces and the
  rule-based classifier's outputs are unchanged.

Regression coverage lives in `tests/test_query_type_registry.py` and
`tests/test_query_understanding.py`.

See `docs/RAG_ARCHITECTURE_IMPROVEMENT_PLAN.md` (Candidate 7).
