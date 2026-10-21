# ADR-0016: Move the benchmark entry to a neutral module

- **Date:** 2026-10-20
- **Status:** Accepted
- **Scope:** `app/rag/evaluation/`

## Context

`app/rag/evaluation/benchmark.py` owns `DecompositionBenchmark`, and
`app/rag/evaluation/decomposition_metrics.py` computes requirement-level
metrics over its entries. The dependency ran both ways:

- `benchmark.py` imported `decomposition_metrics` inside
  `record_prediction()` and `evaluate()` (call time);
- `decomposition_metrics.py` imported `QueryBenchmark` from `benchmark.py`
  under `TYPE_CHECKING`.

The runtime graph was not circular because the metrics import was guarded, but
the type graph was: each module was part of the other's interface. Moving or
renaming either module would break the other's type checking, and the entry
shape was effectively owned by whichever side imported it first.

## Decision

`app/rag/evaluation/benchmark_entry.py` now owns `QueryBenchmark`. It is a
dependency-free data shape (stdlib `dataclasses`/`typing` only).

- `benchmark.py` imports it and re-exports the class, so existing callers of
  `from app.rag.evaluation.benchmark import QueryBenchmark` keep working.
- `decomposition_metrics.py` imports it directly at runtime; the
  `TYPE_CHECKING` import of `benchmark` is gone.
- `app/rag/evaluation/__init__.py` keeps exporting `QueryBenchmark` through
  `benchmark.py`.

`DecompositionBenchmark` remains in `benchmark.py`: it is evaluator behaviour,
not entry data, and the metrics still receive a benchmark object through the
existing `requirement_level_report` duck-typed interface.

## Consequences

- **Positive:** the evaluation type graph is acyclic:
  `benchmark -> benchmark_entry`, `decomposition_metrics -> benchmark_entry`,
  and `benchmark -> decomposition_metrics` only at call time.
- **Positive:** `QueryBenchmark` has one owner; the metrics no longer need a
  type-checking-only reach into the evaluator.
- **Positive:** all public import paths are preserved.
- **Boundary:** this moves data, not scoring logic. `DecompositionBenchmark`,
  `record_prediction`, and `evaluate` stay where they are.

Regression coverage: `tests/test_rag_evaluation_imports.py` pins the neutral
owner and asserts the metrics module does not import `benchmark`. Existing
`tests/test_rag_phase3_requirements.py` and
`tests/test_rag_phase4_measurement.py` cover the benchmark behaviour.

See `docs/RAG_ARCHITECTURE_IMPROVEMENT_PLAN.md` (Candidate 6).
