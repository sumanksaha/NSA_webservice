# ADR-0015: Delete the orphan codegen fragments

- **Date:** 2026-10-20
- **Status:** Accepted
- **Scope:** `app/rag/evaluation/`, `app/rag/feedback/`, `tests/`

## Context

`workflow_evaluator.py`, `workflow_comparator.py` and
`workflow_improvement_engine.py` were assembled by one-shot scripts that wrote
their source in stages. The finished modules stayed, but 34 generator/fragment
files stayed with them:

- `app/rag/evaluation/`: `_part1..8.py`, `_insert_*.py`,
  `_write_evaluator.py`, `_test_improvement.py` (tracked) and `_fix_order.py`
  (ignored);
- `app/rag/feedback/`: `_cmp_part1..5.py`, `_impr_part1..6.py`,
  `_test_impr.py` (tracked) and `_fix_comparator.py`, `_fix_extra_forms.py`
  (ignored);
- `tests/`: `_test_workflow_part1.py`, `_test_workflow_part2a..c.py` and the
  stale generated `_test_workflow_improvement.py`.

None had a production or test importer. They only inflated the RAG surface and
kept the completed target modules looking regenerable when they are not.

The family also carried a real defect. `_write_evaluator.py` wrote its target
on import with a hardcoded absolute path; importing it replaced a 9 KB
`workflow_evaluator.py` with 38 bytes. `scripts/guard_import_side_effects.py`
wrapped the offenders in `__main__` guards, but the underlying generators
remained.

## Decision

Delete the 34 fragment modules. Keep the completed `workflow_evaluator.py`,
`workflow_comparator.py`, `workflow_improvement_engine.py` and the maintained
`tests/test_workflow_improvement.py`.

Also:

- remove the broad `"app/rag/evaluation/*"` ruff exception block that existed
  only for the generators;
- keep `scripts/check_import_side_effects.py` as the detector for import-time
  source writers;
- keep `scripts/guard_import_side_effects.py` as the one-shot repair tool, with
  its incident description in the past tense;
- keep the underscore-prefix import skip in `scripts/module_health_check.py`
  as a defence against new scaffolding.

## Consequences

- **Positive:** 34 modules of accidental surface disappear with no runtime
  behaviour to preserve (no caller existed).
- **Positive:** there is one source of truth per target module; future edits
  cannot be silently overwritten by re-running a fragment.
- **Positive:** the ruff configuration no longer needs a package-wide exception
  for codegen style.
- **Boundary:** this removes generators, not the generated modules. The
  finished evaluator/comparator/engine code is unchanged.
- **Risk:** low. A stale external script that ran `_part*.py` manually would
  fail, but nothing in the repository did.

See `docs/RAG_ARCHITECTURE_IMPROVEMENT_PLAN.md` (Candidate 5).
