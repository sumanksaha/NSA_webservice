# Plan Audit — NSA Webservice

**Date:** 2026-08-17 · **Verdict:** ✅ READY (with caveats)

> **Note:** This audit assesses the project's architectural readiness, conventions completeness, and pre-flight configuration. The project is mature (v0.8.0, ~1,757 tests) with most phases implemented. Key gaps are in **documentation conventions** and a few **pending phases** (15, 18, 19, 20). The existing `AGENTS.md`, `plan.md`, and `task.md` provide strong project context, so no `seed-conventions` or `elaborate-spec` step is needed to begin work.

---

## 1. Principles Alignment

| Check                    | Status | Note                                                                                                                                                                                                                                                                   |
| ------------------------ | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Vertical slices          | ✅     | 23 Flask blueprints are feature-organized (one per domain), not layer-sliced. The `DocumentCaseManager` extracts shared CRUD for CaseFile/Adjudication verticals. RAG has 6 vertical sub-packages (ingestion, retrieval, generation, verification, evaluation, agent). |
| Scope bounded            | ✅     | `AGENTS.md` §1 defines domain split (CaseFile = sample-based, Adjudication = non-sample). `plan.md` §1 has explicit ✅ / ⚠️ / ❌ status tables. `task.md` lists targeted phases with explicit file targets.                                                            |
| Success criteria defined | ✅     | Every phase in `task.md` ends with explicit acceptance criteria + test counts (e.g., "tests/test_x.py — N/N pass"). AGENTS.md tracks test counts per module.                                                                                                           |
| HARD GATE identified     | ⚠️     | `NEO4J_ALLOW_WRITE=1` is the primary fail-closed gate. Render deploy env vars need confirmation (task.md ENV-10/ENV-11). No `CLAUDE.md` for agent-specific hard gates.                                                                                                 |
| Domain language          | ✅     | Canonical key contract in `app/shared/case_keys.py` (e.g., `SHARED_FSO_NAME`, `DATE_SAMPLE_DRAW`, `SECTION_55`). Legal domain terms (FBO, FSO, KMC, FSSAI, hygienic/nonsample_licence tracks).                                                                         |

## 2. Conventions Completeness

| Check                         | Status | Note                                                                                                                                                                                                          |
| ----------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AGENTS.md` exists            | ✅     | 450 lines — comprehensive project context, architecture decisions, directory map, test inventory, env var reference, deletion history, deepening tasks.                                                       |
| `CLAUDE.md` exists            | ❌     | Does not exist. `AGENTS.md` serves the same purpose. No separate agent instructions file.                                                                                                                     |
| `CONVENTIONS.md` exists       | ❌     | Does not exist. Conventions are documented inline in `AGENTS.md` §3 ("Key Patterns for Agents") and `pyproject.toml` (ruff/black/mypy config).                                                                |
| `specs/` directory layout     | ⚠️     | `specs/PLAN-AUDIT_LATEST.md` and `specs/tech-architecture/tech-stack.md` now exist (created by this audit). The rest of the `specs/` tree (e.g., `specs/features/`, `specs/security/`) is not yet structured. |
| Commit conventions documented | ⚠️     | `pyproject.toml` configures tooling. Git workflow mode is not explicitly documented (no `CONVENTIONS.md`). No Conventional Commits reference found in `AGENTS.md` or `plan.md`.                               |
| Git workflow mode identified  | ⚠️     | Not documented. `AGENTS.md` mentions GitHub Actions CI. The repo has `upgradation` branch (from `AGENTS.md` CI config). Solo PR vs team PR mode is unspecified — assume `team-pr` given the CI.               |
| Python version pinned         | ✅     | `requires-python = ">=3.12"` in `pyproject.toml`. CI uses Python 3.12. Render deploys Python.                                                                                                                 |
| Linting config                | ✅     | `ruff` (pyproject.toml), `black` (pyproject.toml), `mypy` (pyproject.toml with overrides), `bandit` (pyproject.toml), ESLint/Prettier (package.json for JS).                                                  |
| Test framework configured     | ✅     | `pytest` with `pytest-cov`, `pytest-xdist`, `pytest-flask`. Config in `pyproject.toml` `[tool.pytest.ini_options]`. Test paths: `["tests", "legal_paragraph_detection_engine/tests"]`.                        |

## 3. Pre-flight Answers

| Question                    | Answer                                                                                                                                                                                                            |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Test command**            | `pytest tests/ -v` (or `pytest --cov=app --cov-report=xml tests/` for CI). CI runs: `pytest --cov=app --cov-report=xml --cov-report=term-missing -v --tb=short -x legal_paragraph_detection_engine/tests/ tests/` |
| **Build command**           | `pip install -e .` (from `requirements.txt` which does `setuptools>=70,<84`, `wheel`, then `-e .`). Render CI also installs Playwright deps. No separate "build" beyond `pip install -e .`.                       |
| **Lint command**            | `ruff check .` (lint) + `black --check --line-length=120 --target-version=py312 .` (format) + `npm run lint:js` (JS). Bandit: `bandit --configfile pyproject.toml -r app/ -s B101,B311,B324`.                     |
| **Typecheck command**       | `mypy --config-file pyproject.toml --ignore-missing-imports app/` (CI sets `continue-on-error: true` — transitional).                                                                                             |
| **CI platform**             | GitHub Actions — `.github/workflows/`: `validation.yml`, `deploy.yml`, `docker-build.yml`, `lint.yml`, `pip-audit.yml`, `release.yml`, `ce-v2-regression.yml`.                                                    |
| **Solo or team?**           | Team (multiple branches: `main`, `upgradation`, `feat/**`, `fix/**` in CI config). GitHub Actions with PR triggers. Assume `team-pr` workflow.                                                                    |
| **Language + framework**    | Python 3.12 + Flask 2.x (server-rendered Jinja2, no React). RAG subsystem uses LangGraph (lazily imported). KG uses Neo4j Aura. OCR uses PaddleOCR + Tesseract + OpenCV.                                          |
| **Greenfield or existing?** | Existing — mature codebase at v0.8.0 with ~1,757 tests, 27+ Alembic migrations, 23 blueprints. No `seed-conventions` needed; conventions are already established and documented in `AGENTS.md`.                   |

## 4. Open Gaps

| #   | Gap                                                                                                                                      | Action                                                                                      |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| 1   | `CLAUDE.md` missing                                                                                                                      | `AGENTS.md` covers this — no action needed unless agent-specific instructions are required. |
| 2   | `CONVENTIONS.md` missing                                                                                                                 | Document git workflow mode, commit conventions, and any team-specific patterns.             |
| 3   | `specs/` directory layout incomplete (only PLAN-AUDIT + tech-stack created)                                                              | Create `specs/features/` structure for upcoming phases (15, 18, 19, 20, Rust refactor).     |
| 4   | Render deploy env vars pending confirmation (task.md ENV-10)                                                                             | Verify `RAG_QDRANT_URL`, `RAG_QDRANT_API_KEY`, and remote inference endpoints on Render.    |
| 5   | M5 checkpoint `PostgresSaver` requires `RAG_AGENT_CHECKPOINTER=postgres` + `DATABASE_URL` — test environment must have this or `memory`. | Ensure test fixtures set `RAG_AGENT_CHECKPOINTER=memory` (or `none`) in CI.                 |
| 6   | `NEO4J_ALLOW_WRITE` gate — CI/test must NEVER set this.                                                                                  | Confirmed: test suite does not set `NEO4J_ALLOW_WRITE`. The gate prevents KG wipes in CI.   |

## 5. Pending Phases (from `plan.md` §1)

| Phase | Feature                    | Status | Gap Summary                                                                                         |
| ----- | -------------------------- | ------ | --------------------------------------------------------------------------------------------------- |
| 15    | Analytics Dashboard        | ❌     | Not started — no `app/analytics/` package. Needs SQL aggregates + Chart.js/Leaflet.                 |
| 18    | Multi-User RBAC & Comments | ⚠️     | Models + migration + admin UI done; `@role_required`, comment API/UI, `tests/test_rbac.py` pending. |
| 19    | AI Case Intelligence       | ❌     | Not started — needs composite scoring from Phases 11 + 12 outputs.                                  |
| 20    | Plugin Architecture        | ❌     | Not started — OCR and rules are hardcoded, no plugin registry.                                      |

## 6. Verdict

✅ **READY** — The project is well-architected and documented. `AGENTS.md` serves as a comprehensive reference (450 lines). The `specs/PLAN-AUDIT_LATEST.md` and `specs/tech-architecture/tech-stack.md` files have been created to formalize project context. All prerequisites for `develop-tdd` (test commands) and `deploy` (CI configuration) are satisfied. Pending phases (15, 18, 19, 20) and conventions gaps (`CLAUDE.md`, `CONVENTIONS.md`) are documented but do not block immediate work.

**Recommended next skill:** `survey-context` (to read existing specs for upcoming phases) or proceed directly to implementation tasks given the readiness of `AGENTS.md`.
