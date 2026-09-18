# Codebase Review — 2026-09-12

Two-axis review of the entire codebase: **Standards** (does the code follow this
repo's documented standards and smell baselines?) and **Spec** (does the code
match what the repo's own specs/docs claim?). All remediation listed below was
completed and verified the same day; each item notes its outcome.

Scope at review time: `app/` 334 files · 65k LOC, `kg/` 6.4k, `evaluation/`
18.3k, `scripts/` 12.5k, `tests/` 56.4k — ~162k LOC total, 190 test files.

## EOL / dependency status (direct answer)

**No end-of-life packages.** All 49 runtime deps are current against the
Python 3.12 target. The real EOL exposure was **repo hygiene and doc drift**,
not packages — items 1–3 in the Spec section below.

The two package-level oddities were both resolved as *documentation, not
migration*:

- **Dual psycopg**: v2 (`psycopg2-binary`) is genuinely used by `scripts/`
  batch upserts (`execute_values`) and the SQLAlchemy URI; v3
  (`psycopg-binary`) by the LangGraph checkpointer. Both declared
  deliberately, annotated in `pyproject.toml`.
- **Duplicate `httpx`** entry: removed from `pyproject.toml`.

## Standards axis

Sources: `CONTRIBUTING.md` (PEP 8, Black, Ruff, type hints),
`pyproject.toml` tool config, smell baseline.

| # | Finding | Severity | Outcome |
|---|---------|----------|---------|
| S1 | Auth/RBAC wiring + blueprint registration lived in the 725-line factory (`app/__init__.py`) while `app/shared/rbac.py` existed as the intended policy home — shotgun surgery on every concern change | hard | **Fixed**: `require_login` + `enforce_rbac` moved into `app/shared/rbac.py::register_auth_gates`; blueprint registration extracted to `app/blueprints.py`. `public_endpoints` stays in the factory (CI gate greps `app/__init__.py` for `health.health`). |
| S2 | Five RRF implementations (`app/rag/retrieval/rrf.py`, `kg/hybrid.py`, `evaluation/fusion.py`, `evaluation/rerank_legal.py`, `scripts/enrichment/evaluate_retrieval.py`) | judgement call | **Resolved by measurement**: 3 of 5 already delegate to the shared core; the 2 remaining are self-contained offline experiment fixtures. Seam decision documented in `app/rag/retrieval/rrf.py`. |
| S3 | Long functions: `create_app` 508, `run_generation_pipeline` 291, `run_retrieval_pipeline` 161, `generate_all` 179, `upload_evidence` 177 | hard | **Fixed (test-first)**: see remediation section. |
| S4 | Dead code: `_legacy_plan` (`query_planner.py`) had zero callers | hard | **Deleted** (verified unreferenced in app/ and tests/). |
| S5 | Logging inconsistency: 14 `print()` in runtime code (`app/ocr_pipeline/*`, `app/metadata_extractor/*`); 53 runtime `assert`s (vanish under `python -O`) | judgement call | **Open** — listed under Follow-ups. |
| S6 | `pyrightconfig.json` pointed `venvPath` at a personal Windows user directory; `pythonVersion` 3.11 vs 3.12 target | hard (portability) | **Fixed**: repo-relative venv resolution, 3.12 aligned. |
| S7 | No local pytest runner; manifests split (`requirements-dev.txt` real list vs `[dev]` extra "once configured") | minor | **Fixed**: `[dev]` extra added to pyproject (17 pkgs, matches old file exactly); `requirements-dev.txt` → shim (`-e ".[dev]"`, filename kept for CI cache keys); `scripts/test.sh` mirrors CI shards. |
| S8 | Positive: `app/services` 100% return-annotation coverage; 1 bare `except:` total; hash-chained audit cleanly extracted (`app/services/audit.py`); test ratio 0.35 LOC | — | — |

## Spec axis

Sources: `specs/tech-architecture/tech-stack.md`, `README.md`, the repo's own
debt inventory (`REFACTORING_PLAN.md`, `ARCHITECTURE_SHALLOW_MODULE_FINDINGS.md`,
`technical_debt_implementation_plan.md`).

| # | Finding | Severity | Outcome |
|---|---------|----------|---------|
| P1 | tech-stack.md advertised sentence-transformers/fastembed/PaddleOCR as installed stack; Flask major version wrong (doc, not code, was EOL) | hard | **Fixed**: spec reconciled — Flask 3.1.3, Modal remote inference + Qdrant-side BM25 as the real RAG stack, optional deps marked lazy fallbacks, reconciliation note added. |
| P2 | `agents.md` (84KB reference) vs `AGENTS.md` (machine config) case-collision at repo root | hard | **Fixed**: reference moved to `docs/agent-reference.md`; inbound refs updated across 7 docs; `docs/INDEX.md` lists both. |
| P3 | 20 root litter artifacts: 5 one-off `.bat` scripts, 8 `rbac_diag/verify*.txt` dumps, `temp_edit.py`, `commit_fixes.sh`, `fix_summary.md`, `fast_upsert_done.flag`, stale zip | hard | **Deleted** (each verified unreferenced first). |
| P4 | `docs/adr-001-asciidoc-template-source-of-truth.md` outside `docs/adr/` while `docs/adr/0001` exists with a different subject | minor | **Open** — needs a subject-matter call. |
| P5 | Two sources of truth for deps (pyproject vs requirements) | hard | **Fixed** (see S7). |
| P6 | `RAG_HALLUCINATION_DETECTOR` verification block + `§2.8` answerability gate shipped with stale tests | hard | **Fixed**: 8 broken tests in `test_rag_generation.py` repaired (fixture-only changes; pre-existing breakage proven via `git stash`), stale `RAG_RETRIEVAL_CACHE` default test updated. |
| P7 | `run_generation_pipeline` wrote `sub_queries` to `retrieval_data` but never surfaced it in the response — decomposition metadata discarded | judgement call | **Fixed**: surfaced in response; pinned test updated. |
| P8 | `citation_validation` only runs when the answer contains citations (stub-mode users never hit it) | judgement call | **Pinned by characterization test**; behavioral change deferred (needs product call). |

## Remediation detail (test-first refactors)

Each split was gated by characterization tests written **before** the change;
code moved verbatim; behavior preserved (only log prefixes changed).

| Function | Before → after | New coverage |
|----------|---------------|--------------|
| `create_app` (`app/__init__.py`) | 508 → 405 (DB bootstrap → `app/db_bootstrap.py`, earlier commit) | boot-tested fresh + admin-seed paths |
| `run_generation_pipeline` (`app/rag/tasks.py`) | 291 → 92 orchestrator + `_generate_resolve_evidence` 79 / `_generate_apply_kg_context` 79 / `_generate_verify_response` 82 | `tests/test_rag_generation_characterization.py` (10 tests) |
| `run_retrieval_pipeline` (`app/rag/tasks.py`) | 161 → 90 (`_retrieval_classify` / `_retrieval_fetch`) | existing e2e/cache suites + pollution fix |
| `generate_all` (`app/adjudication/routes.py`) | 179 → 26 + 3 stage helpers | `tests/test_adjudication_generate_all.py` (6 tests — was zero-coverage) |
| `upload_evidence` (`app/inspection/photo_service.py`) | 177 → 72 + 3 stage helpers | `tests/test_upload_evidence_characterization.py` (7 tests) |

Test-pollution fix along the way: `test_ensemble_reranker.py` now clears the
`build_hybrid_retriever` lru_cache (the identifier-route test leaves fakes in
the shared cache — batch-order flake confirmed pre-existing via `git stash`).

## Final verification

- All suites touching refactored code: green (249-test targeted batch — RBAC 44,
  CI gates 49, ASGI 53, generation 56, adjudication 36, photo 20,
  route-collisions 2; each split re-verified immediately after the change).
- Full fast shard across all 187 test files: **1,690 passed / 49 failed**, and
  all 49 failures are pre-existing or environmental, none in files touched by
  this review — root causes verified individually:
  - Missing optional deps in the local venv: `torch` (hard-negative reranking),
    `neo4j` (KG remediation), `easyocr` (legal OCR).
  - Stale tests vs deliberate default flips: `test_phase17_sync` (Supabase sync
    config), `test_shared_config`.
  - Batch-order/environment-sensitive: `test_rust_normalizers` /
    `test_rust_search_fuzzy` parity (fail in full-batch runs only),
    `test_validation`/`test_timeline`/`test_search`/`test_sparse_retriever`
    (16 confirmed identical under `git stash` control with unmodified modules).
- `py_compile` on every touched module; TOML validated.

## Follow-ups (deliberately not done)

0. **Full-suite baseline repair**: the 49 pre-existing failures above predate
   this review (proven via stash controls / missing-deps root causes). Fixing
   them is its own workstream: install optional deps in `venv/`, reconcile the
   stale sync/config tests, and quarantine or fix the Rust parity batch-order
   flakes.
1. **print→logger sweep** (S5): 14 sites across `app/ocr_pipeline/*`,
   `app/metadata_extractor/*`; also convert runtime `assert`s to real checks
   where they guard behavior (`python -O` strips them).
2. **Citation-validation gating** (P8): decide whether stub-mode answers should
   run the validator; the current only-when-cited behavior is pinned by test.
3. **ADR numbering** (P4): fold `docs/adr-001-asciidoc-template-source-of-truth.md`
   into `docs/adr/` under a distinct number or archive it.
4. Remaining long functions are now all ≤90 lines except `create_app` (405) —
   further splitting is blueprint-registration restructuring, best done with
   the CI-gate constraint in mind.
