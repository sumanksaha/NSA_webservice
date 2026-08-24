# Implementation Plan — NSA Webservice Roadmap (Phases 0–20)

> **Status:** ✅ Phases 0–14, 16, 20, 21, Phase A + OCR Phases B–E, Deepening D1–D5, S6a–d, S7, S2, S10a–c, S9a, Priority 6 infra, Priority 7, and **Phase 6 deferred** all implemented & verified. **RAG Phase 1–5 ✅** (437 RAG tests + 25 Agent A/integration/benchmark tests; 694 RAG tests total — `pytest --collect-only`, 2026-08-20). **Multi-Domain Phase 1 ✅** (37 tests, 2026-08-20). **P1-4 FSSAI re-ingest ✅** (15 tests, 2026-08-11). **Evaluation Framework ✅** (77 tests, 2026-08-12). **Benchmark v1.0 frozen ✅** (2026-08-12). **Rust PyO3 Normalizers ✅** (2026-08-12). **Remote Inference Layer ✅ + deployed** (Modal, 2026-08-16). **LangGraph Agent Pipeline ✅** (41 tests, 2026-08-16). **M5 checkpointing + HITL ✅** (15 tests, 2026-08-16). **FastAPI Gateway ✅** (50 tests, 2026-08-19). **Config seam** (`app/shared/config.py` `cfg` — AGENTS.md §3.6). **Phase 15 Analytics ✅** (15 tests, 2026-08-22). **CI/CD gates G1–G14 ✅** (deploy gating, staging env, pre-deploy migrations, health check, full security blocking — Bandit+Safety+pip-audit, coverage gate fail_under=60, Docker ASGI path, release automation, Dependabot, workflow hygiene, ce-v2 dispatch-only gate, env parity, deploy serialization, dev-dep scanning; `tests/test_cicd_gates.py` — **46/46 pass**, 2026-08-23). Total: **~1,900 tests** (694 RAG + 57 ASGI + 46 CI/CD gates). Phases 15 is done; 17, 18–19 ⚠️/❌ pending; Phase 18 partial (~30%). ENV-2/3/5/10 open.

> **Generated:** 2026-08-06  
> **Source:** Consolidated from `ROADMAP_ALIGNMENT_REPORT.md`, `IMPLEMENTATION_PLAN.md`, `ENGINEERING_ASSESSMENT.md`, and `technical_debt_implementation_plan.md`  
> **Status:** Phases 0–10 ✅ Complete. Deepening Tasks D1–D5 ✅ Complete. Infrastructure ✅ Complete. Phase 16 ✅ Complete (14 tests pass). Phase A ✅ Complete (OCR services + Celery task + 14 tests pass). Phase 13 ✅ Complete (timeline engine + Gantt UI + global case-picker + entry points across search/evidence/annexure/inspection/audit/version-control/sample — 21 tests pass). OCR Phases B–E ✅ Complete (2026-08-22, 45 tests). Phase 15 Analytics ✅ (15 tests, 2026-08-22). **Phase 21 ✅ Complete** (Food Cell DO Intimation — 15 tests pass). **Phase 12 ✅ Complete** (Legal Validation Engine — 7 rules + engine + 2 routes + workbench/index/editor UI via shared `validation_drawer.js`; 46 tests pass). **CI/CD gates G1–G14 ✅ Complete (2026-08-23)** — deploy gating, staging env, pre-deploy migrations, health check, full security blocking (Bandit+Safety+pip-audit), coverage gate fail_under=60, Docker ASGI path, release automation, Dependabot, workflow hygiene, ce-v2 dispatch-only gate, env parity, deploy serialization, dev-dep scanning; `tests/test_cicd_gates.py` 46/46 pass. S9a ✅ Fully fixed (inspection PUT 409 tuple; `tests/test_concurrency_inspection.py` 4/4 pass). Performance Quick Wins: **7/7 done** (connection pooling ✅, FSO lru_cache ✅, Jinja2 bytecode cache ✅, Flask-Compress ✅, health endpoint ✅, DB indexes ✅, eager loading ✅).

---

## 1. Phase Status Overview

### ✅ Completed (Phases 0–9)

| Phase | Feature                                                                                         | Status | Key Files                                                        |
| ----- | ----------------------------------------------------------------------------------------------- | ------ | ---------------------------------------------------------------- |
| **0** | Architecture (keep Flask) + JS linting (ESLint+Prettier)                                        | ✅     | `package.json`, `eslint.config.js`, `.github/workflows/lint.yml` |
| **1** | Core petition engine — auto-save, Delta storage, validation error display, Facts/Grounds/Prayer | ✅     | `editor.js`, `document_viewer/routes.py`, `petition.html`        |
| **2** | Rich editor — Quill 2.x, image upload, Markdown export                                          | ✅     | `editor.js`, `markdown_export.py`                                |
| **3** | Local DB — Settings/Annexure/Evidence/Version models, FTS5 search, backup/restore               | ✅     | `app/models/`, `app/search/`, `app/utils/backup.py`              |
| **4** | Annexure management — upload, **replace**, A/B/C letters, metadata, duplicate detection         | ✅     | `app/annexure/`                                                  |
| **5** | Evidence — unified model, drag-drop, compression, thumbnails, search                            | ✅     | `app/evidence/`, `unify_photo_evidence` migration                |
| **6** | Cross-reference engine — extraction/linking/renumbering/enclosures                              | ✅     | `app/cross_reference/`                                           |
| **7** | Dynamic TOC — extraction, numbering, bookmarks, live editor panel                               | ✅     | `app/toc_generator/`                                             |
| **8** | PDF assembly engine — headers/footers, QR, signatures, bookmarks, hyperlinks                    | ✅     | `app/pdf_assembly/`                                              |
| **9** | Version control — compare, restore, branching, history UI                                       | ✅     | `app/version_control/`, `app/services/version_control.py`        |

### ✅ / ⚠️ / ❌ Status (Phases 11–21 + RAG & Infra Stack)

| Phase  | Feature                                                                        | Status                              | Gap                                                                                                                                                                                                                                                                                                                                                                                            |
| ------ | ------------------------------------------------------------------------------ | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **10** | Search engine — fuzzy search                                                   | ✅                                  | `rapidfuzz` fallback (`fuzzy_search_fallback`), `fuzzy` API/UI toggle, deps declared in `pyproject.toml` — implemented & verified (56 tests pass)                                                                                                                                                                                                                                              |
| **11** | AI assistant — grammar, legal language, summarize, contradictions, drafting    | ✅                                  | `app/ai_assistant/` (service + routes + tasks + JS sidebar); `httpx`-based `AIAssistantService` (no new deps); `POST /ai-assistant/assist`; editor sidebar; 23 tests pass                                                                                                                                                                                                                      |
| **12** | Legal rule engine — ValidationEngine (score, warnings, errors, suggestions)    | ✅                                  | `app/validation/` — 7 rules + `ValidationEngine` (score `clamp(100−15·err−5·warn)` + grades) + `POST /validation/validate` + `GET /validation/case/<id>`; blueprint registered; workbench + index/editor UI via shared `validation_drawer.js`; 46 tests pass                                                                                                                                   |
| **13** | Timeline engine — auto-generated events, Gantt UI                              | ✅                                  | `app/timeline/` — extraction engine, view/API/refresh routes, vertical + Gantt UI + global case-picker + entry points; 21 tests pass                                                                                                                                                                                                                                                           |
| **14** | Knowledge graph — entity/relationship extraction, traversal API                | ✅                                  | Full engine + Cytoscape.js UI + API + SQL persistence + 21 tests + Neo4j Aura integration (2026-08-10): APOC dynamic labels, 9 uniqueness constraints, 3 property indexes, QStash async + sync fallback route, 15 Neo4j tests pass.                                                                                                                                                            | `app/knowledge_graph/` |
| **15** | Analytics dashboard — aggregate queries, charts, geo map                       | ✅                                  | `app/analytics/` — `GET /analytics/` dashboard (Chart.js + Leaflet FBO map) + `GET /analytics/api/metrics` (summary counts, monthly case trends, inspection compliance, sample pipeline, legal provisions, FSO activity, FBO issues, evidence, geo data); lightweight aggregate SQL; blueprint registered at `/analytics` with base.html nav link. **15 tests pass** (2026-08-22)                                                                                                                                                                                                                                                              |
| **16** | Backup & export — JSON export, case import, scheduled backups                  | ✅                                  | `export_case_as_json()`, `export_case_as_zip()`, `import_case_from_json()` in `app/case_file_generator/services.py`; 3 routes in `routes.py`; Celery beat `daily-db-snapshot` at midnight UTC (`celery_app.py`, `app/utils/backup.py`); settings UI (`settings/backup.html`, `settings/routes.py`). 14 tests in `tests/test_case_backup.py` all pass.                                          |
| **17** | Cloud sync — Supabase bridge, annexure sync, conflict resolution               | ⚠️ R2/B2 + Cloudinary + Sheets done | Supabase bridge, conflict resolution, sync-status UI                                                                                                                                                                                                                                                                                                                                           |
| **18** | Multi-user RBAC — Role model, `@role_required`, comments, approval workflow    | ⚠️                                  | Role/UserRole/Comment models + migration + `is_admin`-based admin UI (`/auth/users`) done; `@role_required` + comment API/UI + role assignment + `tests/test_rbac.py` pending (verified 2026-08-07)                                                                                                                                                                                            |
| **19** | AI case intelligence — evidence strength, traceability, readiness score        | ❌                                  | Not started                                                                                                                                                                                                                                                                                                                                                                                    |
| **20** | Plugin architecture — OCR/AI/rule/PDF provider interfaces                      | ✅                                  | ✅ Complete (2026-08-18) — `app/plugins/` (base ABCs + `PluginRegistry` singleton + 4 providers), all 6 callers refactored, `tests/test_plugins.py` 23/23 pass, config-driven selection (`OCR_PROVIDER`/`AI_PROVIDER`/`RULES_PROVIDER`/`PDF_PROVIDER`), backward-compatible shims, no new deps                                                                                                 |
| **21** | Food Cell — DO Intimation generation, PDF export, forwarding after sample save | ✅                                  | `app/food_cell/` blueprint (`__init__.py`, `routes.py`, `services.py`, `tasks.py`, templates); `DoIntimation` model + `food_cell_forwarded` on `Sample`; post-save Celery hook in `app/sample/routes.py`; integrated with Priority 7 sync chain (Sheets + Airtable + Excel best-effort); `add_food_cell_do_intimation` migration; 15 tests in `tests/test_food_cell_do_intimation.py` all pass |

### RAG & Infrastructure Stack (Sub-phases)

| Sub-Phase                            | Status | Tests | Date       |
| ------------------------------------ | ------ | ----- | ---------- |
| RAG Phase 1 — Corpus pipeline        | ✅     | 117   | 2026-08-08 |
| RAG Phase 2 — Generation             | ✅     | 40    | 2026-08-09 |
| RAG Phase 3 — Verification           | ✅     | 48    | 2026-08-09 |
| RAG Phase 4 — Evaluation             | ✅     | 49    | 2026-08-09 |
| RAG Phase 5 — Integration            | ✅     | 31    | 2026-08-09 |
| RAG Phase 3 Agent A (ingest+e2e)     | ✅     | 25    | 2026-08-09 |
| Multi-Domain Phase 1                 | ✅     | 37    | 2026-08-20 |
| P1-4 FSSAI re-ingest                 | ✅     | 15    | 2026-08-11 |
| Evaluation Framework                 | ✅     | 77    | 2026-08-12 |
| Benchmark v1.0                       | ✅     | 150q  | 2026-08-12 |
| Rust PyO3 Normalizers                | ✅     | 45+   | 2026-08-12 |
| Remote Inference (Modal)             | ✅     | 55    | 2026-08-16 |
| LangGraph Agent Pipeline             | ✅     | 41    | 2026-08-16 |
| M5 Checkpointing + HITL              | ✅     | 15    | 2026-08-16 |
| FastAPI ASGI Gateway                 | ✅     | 50    | 2026-08-19 |
| Config seam (`app/shared/config.py`) | ✅     | —     | 2026-08-22 |
| Phase 6 (full rewrite)               | ❌     | —     | deferred   |

### New (Extraction → Storage → Autopopulation Pipeline)

| Phase   | Feature                                                                                                                  | Status |
| ------- | ------------------------------------------------------------------------------------------------------------------------ | ------ |
| **A–E** | OCR extraction (Vision-LLM + zonal OCR), review/commit workflow, conflict resolution, autopopulation, feedback dashboard | ✅ | Phase A foundation + **Phases B–E complete (2026-08-22)** — review workflow (`/ocr`), conflict-resolution queue, autopopulation prefill + FBO-issue auto-drafting, feedback dashboard with few-shot loop, ZIP bulk upload; EasyOCR plugin extraction bug fixed. Tests: review 14, autopopulation 14, feedback 8, bulk 9 + Phase A 14 |
| **F**   | Food Cell DO Intimation — automated DO letter generation, PDF export, and forwarding after FSO sample save               | ✅     | `app/food_cell/` blueprint; `DoIntimation` model; `generate_and_forward_do_intimation()` service; Celery `send_do_intimation` task; post-save trigger in `app/sample/routes.py`; routes: PDF download, HTML view, status, regenerate; best-effort sync to Sheets + Airtable + Excel; `add_food_cell_do_intimation` migration; 15 tests pass |

---

## 2. Recommended Implementation Order

| Step | Phase        | Action                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Key Files                                                                                                                                                                         |
| ---- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1–8  | Phases 0–10  | ✅ Done — Flask architecture, petition engine, rich editor, local DB, annexures, evidence, cross-ref, TOC, PDF assembly, version control, fuzzy search                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | (complete)                                                                                                                                                                        |
| 9    | **S9a**      | ✅ **Fully fixed (2026-08-06)** — `version_id` + `__mapper_args__` on `Inspection`, `Sample`, `Bill`, `CaseFile`; `StaleDataError` → 409 in case_file/adjudication/bill/sample PUT+DELETE routes. The one-line inspection-PUT bug (`409` inside `jsonify()`) is fixed — `tests/test_concurrency_inspection.py` **4/4 pass**.                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | `app/models/inspection.py`, `app/models/billing.py`, `app/inspection/routes/inspection_routes.py`                                                                                 |
| 10   | **Perf**     | ✅ **7/7 done** — SQLAlchemy pool config ✅ (`app/__init__.py:200-210`), FSO `@lru_cache` ✅ (`fso_data.py:27`), Jinja2 `FileSystemBytecodeCache` ✅ (`__init__.py:285-293`), Flask-Compress ✅ (`extensions.py` + `__init__.py:283`), health endpoint ✅ (`health/routes.py`), DB indexes ✅, **eager loading ✅** (`load_only` column trimming in `DocumentCaseManager._list_cases_query()` for the JSON `/cases` endpoints; `lazy="selectin"` on `Bill.samples` + `bills` backref; `distinct()` on the evidence tag-cloud query).                                                                                                                                                                                                                                                          | `app/__init__.py`, `app/utils/fso_data.py`, `app/extensions.py`, `app/health/routes.py`, `app/shared/document_case_manager.py`, `app/models/billing.py`, `app/evidence/routes.py` |
| 11   | **Phase A**  | ✅ **Done (2026-08-06)** — OCR extraction services + Celery task + persistence tests. `process_document_ocr()` (regex+NER field extraction), `split_pdf_bundle()` (PyMuPDF), `process_ocr_document_async` Celery task (persists `OCRDocument` + `LabTestParameter`). `tests/test_ocr_extraction.py` — **14/14 pass**.                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | `app/services/ocr_extraction.py`, `app/services/page_splitter.py`, `app/ocr_pipeline/tasks.py`, `tests/test_ocr_extraction.py`                                                    |
| 12   | Phase 12     | ✅ Done (2026-08-07) — `app/validation/` (7 rules + engine + routes); blueprint registered; shared `validation_drawer.js` UI (workbench + case/adjudication index + document editor); 46 tests pass                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | new blueprint                                                                                                                                                                     |
| 13   | Phase 13     | ✅ **Done (2026-08-06)** — `TimelineEngine` (extract / refresh / validate_sequence / build_payload), 3 routes (`/timeline/case/<id>`, `/timeline/api/case/<id>`, `/timeline/api/case/<id>/refresh`), vertical-timeline + Gantt UI with document links. case_file events persisted to `timeline_event`; adjudication served ephemerally (FK constraint). Access: global nav case-picker (keyboard-navigable search dropdown), both index-page panels, document-editor button, search results, evidence/annexure/inspection/audit/version-control/sample entry points, sample-detail `case_id`+`timeline_url`. Also wired orphaned `app/audit` routes (audit log viewer was 404) and fixed stale `edit_case_file`/`edit_adjudication` url_for names. `tests/test_timeline.py` — **21/21 pass**. | `app/timeline/engine.py`, `app/timeline/routes.py`, `app/timeline/templates/timeline/index.html`, `app/templates/base.html`, `tests/test_timeline.py`                             |
| 14   | Phase 15     | ✅ **Done (2026-08-22)** — `app/analytics/` blueprint at `/analytics`: Chart.js + Leaflet map + `GET /analytics/api/metrics`; 15 tests pass.                                                                                                                                                                                                                                                                                               | `app/analytics/`, `tests/test_analytics.py`                                                                                                          |
| 15   | Phase 18     | ⚠️ Role/UserRole/Comment models + migration + `is_admin` admin UI done; `@role_required` + comment API/UI + `tests/test_rbac.py` pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `app/models/auth.py`, `app/decorators.py`                                                                                                                                         |
| 16   | Phase 16     | ✅ **Done** — JSON export, ZIP export, case import, daily Celery beat, settings UI, 14 tests pass.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `case_file_generator/routes.py`, `celery_app.py`                                                                                                                                  |
| 17   | Phase 11     | ✅ **Complete (2026-08-08)** — `app/ai_assistant/` (service + routes + tasks + JS sidebar); `httpx`-based `AIAssistantService` (no new deps); `POST /ai-assistant/assist`; editor sidebar; `tests/test_ai_assistant.py` **23/23 pass**, no regressions                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | `app/ai_assistant/`, `app/static/js/ai_assistant.js`, `tests/test_ai_assistant.py`                                                                                                |
| 18   | Phase 19     | ⚠️ Create `app/case_intelligence/` (evidence strength, readiness score)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | new blueprint                                                                                                                                                                     |
| 19   | Phase 14     | ✅ **Done (2026-08-08)** — KnowledgeGraphEngine extracts 8 node types + 6 directed edge types from CaseFile/Adjudication; Cytoscape.js view/API routes; SQL persistence to Entity/Relationship tables (case_file only, idempotent); Neo4j sync adapter available (env-gated, dormant). 21 tests pass.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |                                                                                                                                                                                   | `app/knowledge_graph/` |
| 20   | Phase 20     | ✅ Complete (2026-08-18) — plugin architecture with registry + ABCs, all 6 callers refactored, 23 tests pass, config-driven provider selection (`OCR_PROVIDER`/`AI_PROVIDER`/`RULES_PROVIDER`/`PDF_PROVIDER`), backward-compatible shims, no new deps                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |                                                                                                                                                                                   | new blueprint          |
| 21   | Phase 17     | ⚠️ R2/B2 + Cloudinary + Sheets done; Supabase bridge, conflict resolution, sync-status UI pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | `app/sync/`                                                                                                                                                                       |
| 22   | **Phase 21** | ✅ **Done (2026-08-06)** — Food Cell DO Intimation: food_cell blueprint (services, routes, tasks, templates); DoIntimation model + food_cell_forwarded on Sample; HTML-to-PDF via WeasyPrint + stub fallback; Celery send_do_intimation task; post-save hook in sample/routes.py; best-effort sync to Sheets + Airtable + Excel; migration add_food_cell_do_intimation; 15 tests pass                                                                                                                                                                                                                                                                                                                                                                                                                | food_cell package, models/food_cell.py, sample/routes.py, init, celery_app.py, test_food_cell_do_intimation.py                                                                    |

> **📌 Next 3 steps (highest future impact, smallest effort) — updated 2026-08-23:** **Phase 17** (Supabase bridge + conflict resolution + sync-status UI in `app/sync/`; R2/B2/Cloudinary/Sheets/Airtable/Excel sync already done), **Phase 18** (finish Multi-User RBAC — `@role_required` decorator + comment API/UI + role assignment + `tests/test_rbac.py`; models + migration + `is_admin` admin UI done), **Phase 19** (AI Case Intelligence — evidence strength + readiness score; not started). Plus: **RAG Query Interface UI** (task.md §7 — backend done, frontend form/template/JS/nav-link + 8 tests still planned). Pre-commit hook stack fixed (`.pre-commit-config.yaml` hardened: mypy non-blocking `|| true`, pytest fast-subset filter, ce-v2-gate file-scoped, `minimum_pre_commit_version` pin; 5 new `TestPreCommitConfig` tests). CI/CD gates G1–G14 all ✅ done (see §5).

---

## 5. CI/CD Implementation (G1–G14) ✅ — Completed 2026-08-23

> **Status:** All 14 CI/CD gaps from `docs/CI_CD_RESEARCH.md` are implemented and verified by `tests/test_cicd_gates.py` (**46/46 pass**). Full research + gap analysis in `docs/CI_CD_RESEARCH.md`; this section is the implementation summary.

| Gate | Description | Key Files Modified | Verification |
|------|-------------|--------------------|--------------|
| G1 | Deploy gating | `.github/workflows/deploy.yml` | `TestDeployGating` (4) |
| G2 | Staging environment | `render.yaml`, `deploy.yml` | `TestStagingEnvironment` (11) |
| G3 | Pre-deploy migrations | `render.yaml` (`preDeployCommand`) | `TestRenderHealthAndMigrations` (5) |
| G4 | Health check probe | `render.yaml` (`healthCheckPath`) | `TestRenderHealthAndMigrations` (5) |
| G5 | Full security blocking | `validation.yml` | `TestSecurityGates` (3) |
| G6 | Coverage gate | `pyproject.toml` (`fail_under=60`) | `TestCoverageGate` (2) |
| G7 | Docker ASGI path | `Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh` | `TestDockerConsistency` (4) |
| G8 | Release automation | `.github/workflows/release.yml` | `TestReleaseWorkflow` (4) |
| G9 | Dependabot | `.github/dependabot.yml` | `TestDependabot` (1) |
| G10 | Workflow hygiene | `lint.yml`, `pip-audit.yml` | `TestWorkflowHygiene` (4) |
| G11 | ce-v2 dispatch-only | `ce-v2-regression.yml` | `TestCeV2Gate` (1) |
| G12 | Env parity | `render.yaml` (`envVarGroups`) | `TestEnvParity` (3) |
| G13 | Deploy serialization | `deploy.yml` (concurrency) | `TestDeployGating` (4) |
| G14 | Dev dep scanning | `validation.yml`, `pip-audit.yml` | `TestSecurityGates` (3) |

### Deploy flow

```
1. PR/merge → "Repository Validation" workflow (lint + test-fast + security scan)
2. On green validation → deploy.yml workflow_run triggers:
   a. deploy_staging → staging environment (Render `upgradation` branch)
   b. deploy → production (needs: deploy_staging)
3. Tags (v*.*.*) → release.yml → GitHub Release (auto-notes via gh-release@v2)
```

### Security scanning config

| Scanner | Config | Scope | Threshold | Blocking? |
|---------|--------|-------|-----------|-----------|
| Bandit | `pyproject.toml` | `app/` | HIGH/HIGH | ✅ Yes |
| Safety | — | `requirements.txt` | any | ✅ Yes |
| pip-audit | — | `requirements*.txt` | any | ✅ Yes |

Bandit skips `B101` (assert), `B311` (random), `B324` (hashlib). SARIF upload stays `continue-on-error` so reporting never masks failures.

### Setup required (one-time, per environment)

1. **Render Deploy Hook**: create in Render Dashboard → store as `RENDER_DEPLOY_HOOK_URL` repo secret
2. **Render Staging Deploy Hook**: store as `RENDER_STAGING_DEPLOY_HOOK_URL` repo secret
3. **GitHub `staging` environment**: Settings → Environments → New environment (no required reviewers)
4. **Render staging DB**: `nsa-webservice-staging-db` (free tier, branch: `upgradation`)

---

## 3. File-Level Edit Guide

### 3.1 Files Needing New Models

**File:** `app/models/document.py` (was `app/models.py`, now split into `app/models/` package)

- Add: `TimelineEvent`, `Role`, `UserRole`, `Comment`, `Entity`, `Relationship`

### 3.2 New Blueprints to Create

| Blueprint                | Purpose                 | New Files                                                                                                                                                                                                                 |
| ------------------------ | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/validation/`        | Legal rule engine       | ✅ DONE — `__init__.py`, `engine.py`, `rules.py`, `routes.py`; shared `validation_drawer.js` UI; 46 tests                                                                                                                 |
| `app/timeline/`          | Timeline engine         | ✅ DONE — `__init__.py`, `engine.py`, `routes.py`, `templates/timeline/index.html`; global picker in `base.html` + entry points; 21 tests                                                                                 |
| `app/food_cell/`         | Food Cell DO Intimation | ✅ DONE — `__init__.py`, `routes.py`, `services.py`, `tasks.py`, `templates/food_cell/do_intimation.html`, `templates/food_cell/do_intimation_inline.html`                                                                |
| `app/analytics/`         | Analytics dashboard     | ✅ DONE — `__init__.py`, `routes.py` + `templates/`                                                                                                                                                                                  |
| `app/ai_assistant/`      | AI assistant            | `__init__.py`, `service.py`, `routes.py`, `templates/`, `static/js/`                                                                                                                                                      |
| `app/case_intelligence/` | AI case intelligence    | `__init__.py`, `engine.py`                                                                                                                                                                                                |
| `app/knowledge_graph/`   | Knowledge graph         | `__init__.py`, `engine.py`, `routes.py`, `neo4j_sync.py`, `templates/knowledge_graph/view.html`                                                                                                                           |
| `app/sync/`              | Cloud sync              | `__init__.py`, `supabase_sync.py`, `routes.py`                                                                                                                                                                            |
| `app/plugins/`           | Plugin architecture     | ✅ DONE — `__init__.py` (`register_default_plugins()`), `registry.py` (`PluginRegistry` singleton), `base.py` (4 ABCs + `OCRResult`/`AIResponse`), `ocr_plugins.py`, `ai_plugins.py`, `rule_plugins.py`, `pdf_plugins.py` |

### 3.3 Existing Files to Extend

| File                                | Extension Needed                                                                                        |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `app/__init__.py`                   | Register new blueprints, add health endpoint                                                            |
| `app/case_file_generator/routes.py` | JSON export endpoint                                                                                    |
| `app/adjudication/routes.py`        | StaleDataError handling (S9a), validation                                                               |
| `app/inspection/routes/`            | StaleDataError handling (S9a) — see `inspection_routes.py`                                              |
| `app/sample/routes.py`              | StaleDataError handling (S9a), post-save Food Cell DO Intimation trigger (`send_do_intimation.delay()`) |
| `app/__init__.py`                   | Register new blueprints, add health endpoint, register `food_cell_bp` at `url_prefix="/food-cell"`      |
| `app/search/indexer.py`             | Fuzzy search fallback                                                                                   |
| `app/legal_analysis/routes.py`      | Readiness score (Phase 19) — validation UI already wired client-side via `validation_drawer.js`         |
| `celery_app.py`                     | Beat schedule for backups                                                                               |
| `app/templates/base.html`           | Nav links (analytics, version history)                                                                  |
| `pyproject.toml`                    | `rapidfuzz` + `numpy`, `openai` or `httpx` for Phase 11                                                 |

### 3.4 New Migrations

```
add_timeline_event_table.py
add_role_user_role_comment_tables.py
add_entity_relationship_tables.py
add_ocr_pipeline_models.py  (OCRDocument, LabTestParameter, OCRCorrection, ConflictLog, FieldAuthority)
add_food_cell_do_intimation.py  (Phase 21 — do_intimation table + food_cell_forwarded on sample)
```

### 3.5 Extraction → Storage → Autopopulation Pipeline

| Component                  | New Files                                                                                                          |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `app/ocr_extraction/`      | `__init__.py`, `routes.py`, `service.py`, `schemas/`                                                               |
| `app/conflict_resolution/` | `__init__.py`, `routes.py`, `templates/`                                                                           |
| `app/autopopulation/`      | `__init__.py`, `service.py`, `mappings.py`                                                                         |
| `app/feedback_dashboard/`  | `__init__.py`, `routes.py`, `templates/`                                                                           |
| Services                   | `app/services/ocr_extraction.py`, `page_splitter.py`, `conflict_resolution.py`, `autopopulation.py`, `feedback.py` |
| Celery Tasks               | `app/ocr_extraction/tasks.py`                                                                                      |

**Pipeline Architecture:**

```
Document Upload → Page Splitter → Vision-LLM/Zonal OCR Extraction → Raw Storage →
  Review Form (editable, diff detection) → Conflict Resolution Queue →
  Autopopulation (field mapping → document generation) → Feedback Dashboard (correction analytics)
```

---

## 4. Performance & Scaling Assessment

> Source: `ENGINEERING_ASSESSMENT.md`. Current scores: Architecture 7.0/10, Scalability 4.0/10.

### Critical Bottlenecks (must address before scaling)

| #   | Bottleneck                               | Impact                             | Effort | Recommended Fix                     |
| --- | ---------------------------------------- | ---------------------------------- | ------ | ----------------------------------- |
| 1   | SQLite for production                    | CRITICAL (no write concurrency)    | Medium | Migrate to PostgreSQL               |
| 2   | Synchronous Sheets sync on every request | CRITICAL (50-70% of request time)  | Medium | Celery async + batch sync           |
| 3   | In-memory PDF generation                 | HIGH (50-100MB per PDF)            | High   | Separate PDF service or async queue |
| 4   | N+1 queries in list views                | HIGH (101 queries for 100 samples) | Low    | JOIN / eager loading                |
| 5   | Repeated FSO name queries                | HIGH (every request)               | Low    | Redis cache                         |
| 6   | No Jinja2 template caching               | HIGH                               | Low    | Enable template cache               |
| 7   | Blocking KMC API lookup (15s timeout)    | HIGH                               | Medium | Cache + circuit breaker             |
| 8   | No connection pooling                    | HIGH                               | Low    | SQLAlchemy pool config              |
| 9   | Long transaction boundaries              | HIGH                               | Medium | Separate DB commit from sync        |
| 10  | Sequential PDF generation                | MEDIUM                             | Medium | ThreadPoolExecutor                  |

### Quick Wins (1-2 days, immediate impact)

1. ✅ SQLAlchemy connection pooling (`app/__init__.py:200-210`)
2. ✅ Cache FSO names (`@lru_cache` on `load_fso_names` in `app/utils/fso_data.py:27`)
3. ✅ Enable Jinja2 template caching (`FileSystemBytecodeCache` in `app/__init__.py:285-293`)
4. ✅ Add DB indexes (all models have `__table_args__` with indexes)
5. ✅ Fix N+1 with eager loading — **done (2026-08-06)**: `load_only` column trimming on the wide-table JSON list endpoints (`DocumentCaseManager._list_cases_query()`), `lazy="selectin"` on `Bill.samples` + `bills` backref, and `distinct()` on the evidence tag-cloud query
6. ✅ Flask compression middleware (`compress.init_app(app)` in `app/__init__.py:283`)
7. ✅ Health check endpoint (`GET /health` in `app/health/routes.py`)
8. ✅ Move FSO sync to startup script (done via `sync_fso_from_markdown` in `create_app`)

### Bug Fixes (quick)

1. ✅ Fix S9a inspection-PUT StaleDataError handler (`app/inspection/routes/inspection_routes.py`): `409` is inside `jsonify()` instead of as a tuple. **Fixed 2026-08-06** — `jsonify({...}), 409`; `tests/test_concurrency_inspection.py` 4/4 pass.

### Long-term Improvements (3-7 days each)

1. PostgreSQL migration (from SQLite)
2. Async Sheets sync (Celery + Redis)
3. Separate PDF generation service
4. Redis request caching
5. Database read replicas
6. Request queue for heavy operations
7. Batch Google Sheets sync
8. Streaming Excel generation
9. Circuit breakers for external APIs

### AI Readiness (updated 2026-08-23)

- **Phase 11 ✅** — AI Assistant (`app/ai_assistant/`): `AIAssistantService` (httpx-based, no new deps), 5 actions (summarize, refine, contradictions, annexures, draft prayers), editor sidebar, `POST /ai-assistant/assist`, 23 tests pass.
- **RAG Stack ✅** — Full retrieval-augmented generation pipeline: Qdrant vector store, Modal remote embedding/reranker (all-mpnet-base-v2 768-dim, legal cross-encoder), 27,343 chunks indexed (full corpus), hybrid dense+sparse retrieval (RRF k=60, Qdrant-side BM25), reranking, grounded generation, hallucination detection, evaluation framework, LangGraph self-correcting agent pipeline with M5 checkpointing + human-in-the-loop, FastAPI ASGI gateway at `/api/v2/*`. 694 RAG tests pass.
- **Phase 15 Analytics ✅** — Dashboard with Chart.js charts + Leaflet FBO map; 15 tests pass (2026-08-22).
- **OCR Pipeline (A–E) ✅** — Full extraction → review → autopopulation pipeline (45 tests, 2026-08-22).
- **Phase 19 ⚠️** — AI Case Intelligence not yet started (evidence strength + readiness score).

---

## 5. Dependencies to Add

| Package             | Phase              | Reason                                                                                          |
| ------------------- | ------------------ | ----------------------------------------------------------------------------------------------- |
| `rapidfuzz`         | Phase 10           | ✅ Declared — fuzzy search (also fixes undeclared import in `document_cleaner/normalizers.py`)  |
| `numpy`             | Phase 10           | ✅ Declared — OCR runtime dep (undeclared) — `test_ocr_pipeline` fails to collect on fresh envs |
| `openai` or `httpx` | Phase 11           | LLM integration                                                                                 |
| `redis`             | Phase 1 (infra)    | Caching + Celery broker (already in `pyproject.toml`)                                           |
| `tenacity`          | Phase A            | Retry logic for Cloudinary and extraction pipeline                                              |
| `psycopg2-binary`   | Phase 1 (infra)    | PostgreSQL driver (already in `pyproject.toml`)                                                 |
| `structlog`         | Priority 6 (infra) | ✅ Declared (2026-08-05) — structured JSON logging via `app/utils/logging.py`                   |
| `flasgger`          | Priority 6 (infra) | ✅ Declared (2026-08-05) — auto-generated OpenAPI/Swagger UI at `/apidocs`                      |

### Already Declared (verify usage)

| Package              | Status                                                             |
| -------------------- | ------------------------------------------------------------------ |
| `cloudinary>=1.40.0` | ✅ Declared — Cloudinary photo storage (see `task.md` §Cloudinary) |
| `qrcode>=8.0`        | ✅ Declared — PDF QR code generation                               |
| `pytesseract`        | ✅ Declared — OCR engine                                           |
| `celery>=5.6.3`      | ✅ Declared — async tasks                                          |
| `redis`              | ✅ Declared — Celery broker                                        |
| `pyairtable`         | ✅ Added (2026-08-07) — Airtable API client (Priority 7)           |
| `msal`               | ✅ Added (2026-08-07) — Microsoft OAuth2 (Excel Online, dormant)   |

---

## 6. Cloudinary Integration (already done)

> Source: `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md`. Score: 4.3/5.

**Status:** ✅ Implemented for `InspectionPhoto` (adjudication photos only).

**Backend:** `app/utils/storage.py` (lines 162–269)

- Lazy SDK import → falls back to R2/B2 if env vars missing or SDK absent
- Deterministic public IDs: `inspections/<adjudication_id>/<uuid>`
- Idempotent deletes (mirrors R2 NoSuchKey behavior)
- PDF embedding: Cloudinary HTTPS URLs flow through `embed_photos_as_base64` unchanged

**Out of Scope (separate project):**

- `PhotoEvidence.filepath` (inspection photos) — requires OCR pipeline refactor to work with HTTP URLs

**Next Steps** (see `task.md` — Cloudinary section):

1. Add unit tests for Cloudinary helpers
2. Add retry logic (tenacity)
3. Add credential validation health endpoint

---

## 7. RAG Query Interface UI ✅ — Complete (2026-08-23)

> **Status:** ✅ Complete — the full RAG query web UI exists (`GET /api/rag/` renders the
> query form, `app/static/js/rag_query.js` handles AJAX submission + response rendering,
> nav link in `base.html`). Backend API routes (`POST /api/rag/query` legacy +
> `POST /api/rag/query/agent` LangGraph agent) verified end-to-end. All 8 RAG UI gaps from
> the primary-source audit (`docs/RAG_UI_RESEARCH.md`) resolved (2026-08-23):
> (1) live `use_agent` override on the `/api/rag/query/agent` route, (2) circuit-breaker
> symmetry via `ResilientRAGPipeline` singleton on the Flask route, (3) Phase 3 hallucination
> detector hot path gated behind `RAG_HALLUCINATION_DETECTOR`, (5) HITL durability signal
> via checkpointer introspection, (6) localStorage session history (20-turn cap),
> (7) live-key A/B parity gate (`scripts/ab_agent_vs_legacy.py`), (8) CSRF contract pinned
> (`tests/test_rag_csrf.py`). Tests: `tests/test_rag_query_breaker.py` (5) + `test_rag_detector_hotpath.py` (4) + `test_rag_ui_gaps.py` (11) + `test_ab_parity_gate.py` (13) + `test_rag_csrf.py` (5) = **38 tests**, all pass.

- **Target Files to Edit/Create:**
    - Modify `app/rag/routes.py` — add `GET /` route rendering `rag/query.html`
    - Create `app/rag/templates/rag/query.html` — form + results sections
    - Create `app/static/js/rag_query.js` — AJAX POST to `/api/rag/query/agent`, render `RAGResponse`, handle 202/HITL resume
    - Modify `app/templates/base.html` — add "Legal RAG" nav tab
    - Modify `.env.example` — document `RAG_ENABLED` flag

### Architecture

```
Authenticated User visits /api/rag/ (GET)
        │
        ├── renders rag/query.html (Jinja2, extends base.html)
        │   ├── Query form: textarea + collection selector + submit button
        │   ├── Results section: answer text, citations, groundedness score badge
        │   ├── HITL section: awaiting_review message + resume form (hidden by default)
        │   └── Error section: RAG-disabled / network failure messages (hidden by default)
        │
        ├── user submits query → POST /api/rag/query/agent
        │   ├── RAG_USE_AGENT_PIPELINE=true → LangGraph graph (classify → retrieve
        │   │   → generate → verify → expand-and-retry on groundedness < 0.7)
        │   ├── RAG_AGENT_HITL=true + groundedness borderline → 202 awaiting_review
        │   │   └── POST /api/rag/query/agent/resume {thread_id, approved}
        │   └── RAG_USE_AGENT_PIPELINE=false → delegates to legacy /api/rag/query
        │
        └── RAGQueryLog.pipeline stamped ("legacy" / "agent")
```

### Detailed Implementation Plan

1. **Route (`app/rag/routes.py`):** Add `GET /` route that renders the `rag/query.html` template. The `rag_bp` blueprint is already registered at `url_prefix="/api/rag"`, so the route is reachable at `/api/rag/`. The route passes `RAG_USE_AGENT_PIPELINE`, `RAG_AGENT_HITL`, and `RAG_ENABLED` config values to the template for conditional UI rendering.
2. **Template (`app/rag/templates/rag/query.html`):** Jinja2 template extending `base.html` with:
    - A query input form (textarea, collection selector dropdown populated from `app/rag/collections.py`, submit button)
    - A results section that renders the `RAGResponse` schema (answer text, citations list with links, groundedness score badge, audit trail)
    - CSRF token handling (base.html already has a global CSRF fetch interceptor via `csrf_token` meta tag — no manual token handling needed in JS)
    - Hidden sections for 202 awaiting_review + resume form, and error messages
3. **JavaScript (`app/static/js/rag_query.js`):** IIFE pattern (matching `ai_assistant.js`):
    - `POST` to `/api/rag/query/agent` with `{query, collection}` via `fetch()`
    - On `200`: render `RAGResponse` (answer text, citations list, groundedness score badge)
    - On `202`: show awaiting_review state with `thread_id` + resume form (`{thread_id, approved}`)
    - On `503`: show "RAG disabled" error message
    - CSRF token read from `base.html`'s meta tag (`csrf-token`)
    - All state variables (`agentThread`, `queryText`, `collectionName`) held in closure scope (no global leakage)
4. **Nav link (`app/templates/base.html`):** Add "Legal RAG" tab in the navigation bar, visible to authenticated users (all routes require auth via `require_login` before_request gate — `rag_query` is not in `public_endpoints`).
5. **Config (`.env.example`):** Document `RAG_ENABLED` (default `false`) — gates whether the RAG UI nav link and `GET /` route are accessible. When `false`, the route returns 404 and the nav link is hidden.
6. **Tests (`tests/test_rag_interface.py`):** Test that the `GET /` route renders the template (200), the JS file is served at `/static/js/rag_query.js` (200), and the nav link appears in `base.html` after login.

### Acceptance Criteria

- `GET /api/rag/` renders the query form for authenticated users (302→login for unauthenticated)
- Submitting a query POSTs to `/api/rag/query/agent` and renders the `RAGResponse`
- When `RAG_USE_AGENT_PIPELINE=false`, the query delegates to the legacy pipeline
- When `RAG_AGENT_HITL=true` and the response is 202, the resume form is shown
- `pytest tests/test_rag_interface.py` passes (new test file, 8 tests planned)

---

## 8. Multi-Target Sheets Redundancy Architecture

> **Status:** ✅ Complete (2026-08-07). `app/utils/sync.py` — 12 new restore functions/variables (`restore_from_airtable_csv()`, `restore_from_excel_csv()`, `restore_from_sheets_csv()`, `restore_if_empty()` orchestrating Airtable → Excel → Sheets, `trigger_backup()`, `_restore_from_records()`, `_restore_module()`, `_parse_csv_value()`, `_is_empty_sqlite_db()` fixed to use `db.metadata.tables`, plus `_AIRTABLE_TABLE_MAP`/`_WORKSHEET_MAP`/`_SHEETS_RESTORE_MAP` maps). `app/__init__.py` — fixed Priority 7 config indentation, QStash daily backup schedule at 02:00 UTC (gated behind `ENABLE_BACKUP_SCHEDULE`). `app/settings/routes.py` — restored `backup_restore` route, added `backup_redundant_to_r2` + `backup_redundant_to_r2_status` routes. `tests/test_priority7_redundancy.py` — **43/43 tests pass**, no regressions.
> **Goal:** Eliminate Google Sheets as a single point of failure by adding Airtable and Microsoft Excel Online as parallel real-time sync targets, with R2 CSV exports of each service for redundant restore.

### Architecture

```
Primary:  PostgreSQL (Render)
           │  (per-record push on create/update)
           ├──► Google Sheets       (gspread API)
           ├──► Airtable             (pyairtable API)  ← NEW
           └──► Excel Online          (Microsoft Graph API)  ← DORMANT (no M365 credentials)
           │
           └──► Daily QStash-triggered backup:
                scripts/backup_redundant_sheets.py
                  ├──► Sheets  → CSV → Cloudflare R2
                  ├──► Airtable → CSV → Cloudflare R2  ← NEW
                  └──► Excel  → CSV → Cloudflare R2  ← DORMANT
           │
           └──► On PG failure → SQLite fallback
                └──► Restore chain:
                    1. R2 JSON backup (build_backup_archive format)
                    2. R2 CSV from Sheets (if R2 JSON unavailable)
                    3. R2 CSV from Airtable (if Sheets R2 gone)  ← NEW
                    4. Live API pull (if Airtable R2 gone)      ← NEW (Excel dormant)
                    5. Empty SQLite (graceful degradation)
                    6. Empty SQLite (graceful degradation)
```

### Key Design Decisions

1. **Parallel, not replacement:** All three services (Sheets, Airtable, Excel) run in parallel. No single service is authoritative for data recovery.
2. **`< 1,200 records/base` Airtable handling:** Airtable's free tier limit is 1,200 records per base. The Airtable sync service implements **automatic base rotation** — when the current base nears capacity, a new base with identical schema is created programmatically (via Airtable REST API `/v0/meta/bases`), and subsequent records are routed to the new base. A tracking table (`airtable_base_map`) records which base each record lives in.
3. **R2 CSV = canonical backup:** All three services export to R2 as CSV. The restore chain tries R2 JSON (most complete) first, then falls through to each service's R2 CSV backup in order.
4. **QStash-only scheduling:** No Celery worker required for daily exports. QStash webhook triggers `scripts/backup_redundant_sheets.py` at 1 AM UTC daily.
5. **Best-effort sync:** If any service fails (rate limits, auth expiry, plan limits), the sync is silently skipped — PostgreSQL is the source of truth.

### New Dependencies

| Package      | Purpose                                  | Current Status        |
| ------------ | ---------------------------------------- | --------------------- |
| `pyairtable` | Airtable API client SDK                  | ✅ Added (2026-08-07) |
| `msal`       | Microsoft OAuth2 client credentials flow | ✅ Added (2026-08-07) |

### New Environment Variables

| Variable            | Purpose                         | Required For  |
| ------------------- | ------------------------------- | ------------- |
| `AIRTABLE_API_KEY`  | Airtable API key                | Airtable sync |
| `AIRTABLE_BASE_ID`  | Primary Airtable base ID        | Airtable sync |
| `MS_TENANT_ID`      | Azure AD tenant ID              | Excel sync    |
| `MS_CLIENT_ID`      | Azure AD app registration ID    | Excel sync    |
| `MS_CLIENT_SECRET`  | Azure AD client secret          | Excel sync    |
| `MS_DRIVE_ID`       | SharePoint/OneDrive drive ID    | Excel sync    |
| `MS_SPREADDHEET_ID` | Excel file ID in OneDrive/Share | Excel sync    |

### Airtable API Key Management — How the App Gets & Holds the Key

> The Airtable API key (`AIRTABLE_API_KEY`) follows a **multi-layer, environment-driven
> security model** — it is **never hardcoded** in source code. It is read from environment
> variables at runtime and threaded through Flask's `app.config`.

**1. Source — Environment Variables (never committed):**

| Variable                 | Purpose                                             | Source                                                                                                              |
| ------------------------ | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `AIRTABLE_API_KEY`       | Airtable personal access token (starts with `pat…`) | **Production**: Render Dashboard (manually entered). **Local dev**: `.env` (gitignored, loaded via `load_dotenv()`) |
| `AIRTABLE_BASE_ID`       | Primary Airtable base ID (starts with `app…`)       | Same as above                                                                                                       |
| `ENABLE_AIRTABLE_SYNC`   | Feature flag — defaults to `false`                  | Set to `"true"` in `render.yaml` for production; `"false"` if unset                                                 |
| `ENABLE_BACKUP_SCHEDULE` | Gates the QStash daily backup schedule              | Must be `"true"` to activate; dormant otherwise                                                                     |

**2. Config Loading (`app/__init__.py::create_app`, ~line 187–191):**

```python
app.config["AIRTABLE_API_KEY"]    = os.environ.get("AIRTABLE_API_KEY")
app.config["AIRTABLE_BASE_ID"]    = os.environ.get("AIRTABLE_BASE_ID")
app.config["ENABLE_AIRTABLE_SYNC"] = os.environ.get("ENABLE_AIRTABLE_SYNC", "false").lower() == "true"
```

`render.yaml` declares `AIRTABLE_API_KEY` / `AIRTABLE_BASE_ID` as `sync: false` → manually
entered in the Render Dashboard, never stored in code or committed to the repo.

**3. Client creation (`app/services/airtable_sync.py::_get_client`):** lazily creates a
thread-local-cached `pyairtable.Api` instance. Reads from `current_app.config` first, then falls
back to `os.environ['AIRTABLE_API_KEY']`. Returns `None` if the key is missing or `pyairtable`
is not installed — the app **always boots** regardless of Airtable availability.

**4. Usage in the sync flow (`sync_to_airtable`):** gates on `ENABLE_AIRTABLE_SYNC` (both
Airtable and Excel are dormant by default; only Airtable is enabled in production via
`render.yaml`). Then: get client → `_get_base_id()` (honors 1,200-record base rotation) →
`client.table(base_id, table_name).insert(fields)` → track mapping in the `AirtableBaseMap`
model. Every failure is logged but never blocks the core operation.

**5. Backup path (daily QStash schedule):** when `ENABLE_BACKUP_SCHEDULE=true`, startup
registers a QStash recurring task (`0 2 * * *` daily at 02:00 UTC) →
`backup_coordinator.run_backup()` → `export_airtable_all_bases_to_r2()`, which writes a
combined CSV of all Airtable bases to R2 (`nsa_backups/airtable_csv/`, local fallback at
`instance/backups/airtable_csv/`).

**6. Restore path (SQLite fallback):** `restore_if_empty()` (`app/utils/sync.py`, line 415) on an
empty SQLite DB tries, in order: `restore_from_airtable_csv()` → `restore_from_excel_csv()` →
`restore_from_sheets_csv()`. Each downloads the R2 CSV, strips Airtable metadata (`base_id`,
`id`), maps rows to models with type coercion, and commits.

**Key points:** never in code · optional (flag defaults off) · graceful degradation (missing
key / missing package / API failure) · Render Dashboard provisioning (`sync: false`) ·
`.env` for local dev · sample placeholder values in `.env.example` (no real tokens).

### Integration Points with Existing Codebase

| Existing File                                | Extension Point                                                                                                                         |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `app/services/sheets_sync.py`                | Provides `WORKSHEET_MAP`, `SHEET_COLUMNS`, `get_gspread_client()` — reused by both Airtable and Excel services for column/field mapping |
| `app/utils/storage.py`                       | Provides `_get_client()` / `_get_bucket()` — reused by all backup/export scripts                                                        |
| `app/case_file_generator/routes.py`          | Add `sync_to_airtable()` + `sync_to_excel()` calls alongside existing `sync_to_sheets()`                                                |
| `app/adjudication/routes.py`                 | Same pattern — parallel sync calls                                                                                                      |
| `app/inspection/routes/inspection_routes.py` | Same pattern                                                                                                                            |
| `app/sample/routes.py`                       | Same pattern                                                                                                                            |
| `app/bill_generator/routes.py`               | Same pattern                                                                                                                            |
| `app/__init__.py`                            | Extend startup recovery hook to try new restore sources                                                                                 |
| `app/utils/sync.py`                          | Extend with `restore_from_airtable_csv()` + `restore_from_excel_csv()`                                                                  |
| `celery_app.py`                              | No changes needed (QStash handles scheduling)                                                                                           |

---

## 9. Food Cell DO Intimation (Phase 21) ✅

> **Status:** ✅ Implemented (2026-08-06). `tests/test_food_cell_do_intimation.py` — **15/15 pass**.

> **Goal:** Generate a templated DO (Designated Officer) Intimation after an FSO saves sample data, store HTML + PDF, and forward to the Food Cell via the multi-target sync chain (Sheets + Airtable + Excel).

### Architecture

```
FSO saves sample (app/sample/routes.py create_sample)
        │
        ├── commit sample + sync_to_sheets
        │
        ├── send_do_intimation.delay(sample_id)   [Celery task]
        │       │
        │       └── generate_and_forward_do_intimation(sample_id)
        │               ├── _next_do_reference_no()  →  "DO/YYYY/000123"
        │               ├── _render_html(sample)      →  Jinja2 template
        │               ├── _render_pdf(html)          →  WeasyPrint (stub fallback)
        │               ├── _store_intimation(...)     →  instance/food_cell/
        │               ├── sample.food_cell_forwarded = now
        │               ├── _sync_intimation(...)      →  best-effort:
        │               │       ├── sync_to_sheets("food_cell_do_intimations")
        │               │       ├── sync_to_airtable("FoodCellDOIntimations")
        │               │       └── sync_to_excel("FoodCellDOIntimations")
        │               └── commit + refresh(DoIntimation)
        │
        └── return 201 JSON { sample_id, sample_code }


Routes (registered at url_prefix="/food-cell"):
   GET  /food-cell/do-intimation/<sample_id>/pdf       → download PDF
   GET  /food-cell/do-intimation/<sample_id>/html      → view HTML inline
   GET  /food-cell/do-intimation/<sample_id>/status    → JSON status
   POST /food-cell/do-intimation/<sample_id>/regenerate → force re-render
```

### Files Created / Modified

**New files:**

- `app/food_cell/__init__.py` — package init (Blueprint placeholder, delegates to routes.py)
- `app/food_cell/routes.py` — `food_cell_bp` Blueprint with 4 routes (PDF download, HTML view, status, regenerate)
- `app/food_cell/services.py` — `generate_and_forward_do_intimation()` service: lazy sync-fn loading, `_next_do_reference_no()`, `_render_html()`, `_render_pdf()` (WeasyPrint + stub fallback), `_store_intimation()`, `_sync_intimation()`, `_build_sync_row()`
- `app/food_cell/tasks.py` — `send_do_intimation()` Celery task (lazy `celery` import, registered via `TASK_MODULES`)
- `app/food_cell/templates/food_cell/do_intimation.html` — Jinja2 DO intimation letter template
- `app/food_cell/templates/food_cell/do_intimation_inline.html` — HTML viewer wrapper with action buttons
- `app/models/food_cell.py` — `DoIntimation` SQLAlchemy model (version_id concurrency guard, indexes, FK → Sample)
- `migrations/versions/add_food_cell_do_intimation.py` — migration (creates `do_intimation` table + adds `food_cell_forwarded` column to `sample`)
- `tests/test_food_cell_do_intimation.py` — 15 tests across 8 test classes

**Modified files:**

- `app/__init__.py` — registered `food_cell_bp` at `url_prefix="/food-cell"` alongside existing 14 blueprints
- `app/models/__init__.py` — added `DoIntimation` to model re-exports
- `app/models/billing.py` — added `food_cell_forwarded` column (indexed) to `Sample` model
- `app/sample/routes.py` — post-save trigger in `create_sample()`: after Sheets sync, calls `send_do_intimation.delay(sample.id)` (guarded by `food_cell_forwarded is None`, best-effort)
- `celery_app.py` — added `"app.food_cell.tasks"` to `TASK_MODULES` list

### Data Model

```python
class DoIntimation(db.Model):
    __tablename__ = "do_intimation"
    id                   # auto-increment PK
    version_id           # optimistic concurrency (StaleDataError → 409)
    sample_id            # FK → sample.id (CASCADE delete)
    do_reference_no      # "DO/2026/000123" (unique, indexed)
    html_path            # local filepath (instance/food_cell/html/)
    pdf_url              # local filepath (instance/food_cell/pdfs/)
    food_cell_forwarded  # datetime — set when intimation is dispatched
    status               # pending | generated | forwarded | acknowledged | failed
    sync_status          # JSON blob: {"sheets": bool, "airtable": bool, "excel": bool}
    created_at           # UTC timestamp

class Sample  # extended:
    food_cell_forwarded  # DateTime, nullable, indexed — set by do intimation service
```

### Sync Chain Integration

The Food Cell module integrates with the Priority 7 multi-target sync architecture. The `_sync_intimation()` function calls all three sync services best-effort:

1. **`sync_to_sheets("food_cell_do_intimations", row)`** — Google Sheets via `app/utils/sync.py`
2. **`sync_to_airtable("FoodCellDOIntimations", row, intimation.id)`** — Airtable via `app/services/airtable_sync.py` (Priority 7, new)
3. **`sync_to_excel("FoodCellDOIntimations", row)`** — Excel Online via `app/services/excel_sync.py` (Priority 7, new)

Sync functions are loaded lazily via `_load_sync_fns()` so the module bootstraps even when optional sync dependencies (pyairtable, msal) are absent. Each call is wrapped in `try/except` — failures are logged but never block the intimation generation.

### Test Inventory

| File                                    | Tests | Covers                                                                                                                                                                                                                                                                                                               |
| --------------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/test_food_cell_do_intimation.py` | 15    | HTML rendering, PDF generation, intimation creation, FSO-save trigger (task callable), sync forwarding results, PDF download endpoint, HTML view endpoint, status endpoint (found + not-found), regenerate endpoint, idempotency, force regeneration, sample-not-found, DO reference uniqueness, forwarded timestamp |

---

## 9. Test Environment Issues (Discovered During 2026-08-06 Verification)

> During the verification run of all 14 open dependabot PRs (commit `a746104`), the full test suite (832 tests, 22-min runtime) was executed. The dependency updates themselves are **not** the cause of any failures — the updated package versions (pytest 9.1.1, pytest-cov 7.1.0, black 26.5.1, etc.) were already installed in the environment before the PR changes were applied. The failures are all **environment-specific** gaps that must be addressed for a clean test run.

### Test Suite Results

| Metric | Count                                                        |
| ------ | ------------------------------------------------------------ |
| Passed | 783                                                          |
| Failed | 18                                                           |
| Errors | 21                                                           |
| Total  | 822 attempted (832 collected, 10 not reached due to cascade) |

### Failure Breakdown — All Environment-Related

| Test File                         | Failures | Errors | Root Cause                                                                                                                                                                                                            | Fix Priority |
| --------------------------------- | -------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| `test_concurrency_inspection.py`  | 4        | 0      | PostgreSQL-specific: advisory locks + `StaleDataError` not raised on SQLite → HTTP 500 instead of 409                                                                                                                 | Medium       |
| `test_case_backup.py`             | 0        | 14     | PostgreSQL-specific: JSON export/import, zip archives, Celery beat require PG features; SQLite setup fails in fixtures                                                                                                | Medium       |
| `test_food_cell_do_intimation.py` | 7        | 7      | (a) Missing template `food_cell/do_intimation.html`; (b) Redis/Celery not available for async sync dispatch                                                                                                           | High         |
| `test_ocr_pipeline.py`            | 7        | 0      | Missing `cv2` (OpenCV) — optional OCR preprocessing dependency                                                                                                                                                        | Medium       |
| `test_timeline.py`                | 11       | 0      | Pre-existing uncommitted `app/__init__.py` change (health endpoint `health_bp` registration + `public_endpoints` addition) interferes with blueprint route initialization → 404 on all `TestTimelineRoutes` endpoints | High         |

### Environment Details

- **Python:** 3.11.15 (project requires 3.12+ — version mismatch may cause subtle issues)
- **Database:** SQLite (via conftest temp DB) — no PostgreSQL available
- **Redis:** Not running — Celery tasks fail silently
- **GitHub CLI:** Not installed in environment
- **Shell:** PowerShell 7 (no native `&&` / `||` support; `curl` aliased to `Invoke-WebRequest`)
- **GitHub API rate limit:** 60 requests/hour unauthenticated

### Notes

- The **dependabot PR changes themselves are verified safe** — all 783 passing tests run with the updated dependency versions already installed.
- The timeline **engine** tests pass in isolation (`TestTimelineEngine::test_valid_case_has_no_warnings` ✅); only the **route** tests fail due to the pre-existing `app/__init__.py` changes.
- The concurrency guard (S9a) code is correct (`StaleDataError → 409` tuple); the test failures are because SQLite doesn't trigger `StaleDataError` the way PostgreSQL does.

---

## 11. Corpus Coverage & CE-v2 Baseline — Status (2026-08-18)

> Companion docs: `docs/COVERAGE_COMPLETENESS.md` (full plan) and `evaluation/CV2_IMPROVEMENT_PLAN.md` (checklist).

### Done

- **Coverage audit tooling**: `evaluation/coverage_audit.py` + `tests/test_coverage_audit.py` (14) — repeatable, cache-based; `--live` refreshes the frozen payload cache.
- **P1 — `document_title` backfill** (`scripts/backfill_document_title.py`): 12,819 fills (29 docs, 27,350/27,351 covered) + DB mirror. Applied live.
- **P2 — L7 propagation** (`derive_l7` in `scripts/backfill_payload_identity.py`): header-trust corrections + amendment anchors, 2,075 updates (L7 correction 42 + L7 propagation 1,834 + L5 180 + L4 19). Applied live; re-run fully idempotent (0 changes).
- **Result:** substantive identity coverage **71.6% → 82.4%** (all chunks 58.0%); act docs 93.9%, commercial 99.9%, fssai 86.9%.
- **CE-v2 baseline re-frozen (2026-08-18)**: `evaluation/ce_v2_baseline.json` regenerated from the post-strip/post-P2 payload cache (`ce_v2_error_analysis` → `ce_v2_eval` → `--freeze-baseline`). Failure taxonomy shifted as predicted: **same_section_hard_neg 3 → 2** (one false same-section match removed by the reg/rule/notification noise strip); hierarchy_version 8 → 9; failures 12; pairwise/ranking metrics unchanged (score cache is payload-independent). Gates now: hierarchy 9 / same-section 2 (targets ≤4 / ≤1).

### Next: CE improvement plan (elaborated in `evaluation/CV2_IMPROVEMENT_PLAN.md` §2)

Post-re-freeze failure decomposition: **6 of 12 failures are V2 regressions** (Q049 1→10, Q080 2→9, Q097 2→7, Q102 3→8, Q118/Q120 2→4 — V1 solved them, V2 broke them). Sequence: Step 1 diagnose the 6 regressions (0.5 d) → Step 2 P1 section-prefix (1.5–2 d) ∥ Step 3 P2 same-section re-mine on the cleaned corpus (1–2 d) → Step 4 P4 domain balance (1 d) → Step 5 P3 calibration (anytime) → Step 7 re-freeze + deploy. Gates per step in `CV2_IMPROVEMENT_PLAN.md` §2.4.

**Steps 1–4 implemented (2026-08-18):** Step 1 diagnosis done — the 6 regressions are adjacent-section confusions (Q049 s5 vs s6, Q080 s45 Repeal vs s46, Q102 KMC s394 vs s392) + **L7-created same-section false friends (Q118/Q120 amendment chunks stamped sec=33)** + hl1 title-page noise (Q097). Step 2 P1 shipped: `app/rag/retrieval/section_prefix.py` (`prefix_passage`, `RAG_CE_SECTION_PREFIX` default off), `RetrievedChunk.clause_number` + retriever mapping, Reranker/EnsembleReranker/ce_rerank_eval pair-prefixing, `pairwise_dataset.py` authoritative payload-index identity join + `--section-prefix` (88.6% of examples prefixed on real data). Step 3 P2 shipped: `hard_negative_miner.py --subsection-filter` (same_section AND same_subsection, same-section fallback) in live + offline paths. Step 4 P4 shipped: `--domain-balanced --domain-balance-cap 3.0` (27,207 examples, fssai 57% → 30.7%). Tests: `test_section_prefix.py` (23) + `TestSubsectionFilter` (3) + `TestDomainBalance` — 145 affected pass, ruff clean. **Next:** re-mine + rebuild dataset + retrain + re-freeze (commands in the CV2 checklist).

### P3 — deferred (owner: user, later)

- **P3 re-ingestion of broken-OCR docs** (BNS + rule docs ≈ 2,129 substantive chunks; 9 PDFs, all present in `other domain/`) — **held by user, to be done later** (estimate: 2–4 days; see `docs/COVERAGE_COMPLETENESS.md` §P3). Once done: re-run `coverage_audit --live`, then re-freeze the CE-v2 baseline again (section stamps will change for those docs).

---

_End of plan.md_

---

## 10. Knowledge Graph for RAG — Discussion & Future Directions

### 10.1 Current State (2026-08-09 Knowledge Graph Extraction)

A **preliminary knowledge graph** was extracted from the 24-document FSSAI corpus evaluation result (`corpus_eval_result.json`):

- **88 nodes**: 24 documents + 57 sections + 3 canonical authorities + 4 jurisdictions
- **199 edges**: 133 document→section, 24 document→authority, 27 document→jurisdiction, 15 section_cooccurrence pairs
- **3 canonical authorities** (down from 6 raw variants):
    - `FOOD SAFETY AND STANDARDS AUTHORITY OF INDIA` — 10 docs (normalized from `FSSAI`, `fssai`, full-name variants)
    - `MINISTRY OF HEALTH AND FAMILY WELFARE` — 9 docs
    - `MINISTRY OF LAW AND JUSTICE` — 3 docs
- **57 sections** with semantic descriptions (50/57 mapped to FSS Act section meanings)
- **Section co-occurrence hotspots**: Section 4 (duties) + Section 92 (regulations) appear together in 28 documents — the "duties + regulatory power" foundation
- **Chunk quality**: All 13,104 chunks grade C; 48 quality issues (18 too short, 22 missing content hash, 8 too long)
- **Empty documents** (OCR needed): `FSS_Amendment_Act_1-2008.pdf`, `LicReg.pdf` — image-only PDFs with 0 extractable text

### 10.2 Neo4j Integration for RAG

**Current state**: No `neo4j` driver is installed; no Neo4j env vars exist in `.env`/`.env.example`. The RAG stack uses Qdrant (vector store + payload filtering) as its sole graph-capable store.

**Decision**: Neo4j integration is **not yet wired into the RAG retrieval pipeline** — Qdrant payloads already contain `document_type`, `authority`, `section_number`, `jurisdiction`, `citations`, `references`, and `entities` enabling filtered retrieval without a graph DB. The knowledge graph JSON (`knowledge_graph.json`) was generated as a **preliminary corpus-level analysis artifact**, not yet wired into the RAG retrieval pipeline. However, the **Phase 14 Knowledge Graph Engine** (`app/knowledge_graph/`) does include a Neo4j sync adapter (`app/knowledge_graph/neo4j_sync.py`) gated by `ENABLE_NEO4J_SYNC=false` (dormant) that mirrors the case-file entity/relationship graph to a Neo4j Aura database — this is separate from the corpus KG and the RAG retrieval path.

**Phase 14 Neo4j sync** (case-file knowledge graph):

1. Install `pip install neo4j` + add `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` / `ENABLE_NEO4J_SYNC` to `.env.example`
2. `app/knowledge_graph/neo4j_sync.py` — `Neo4jSync` class with `sync_graph(case_id, case_type, nodes, edges)` that runs a single Cypher `UNWIND … MERGE` transaction
3. Called after `build_graph_for_case()` when `ENABLE_NEO4J_SYNC=true`

**RAG Neo4j future direction** (if graph traversal RAG is needed):

1. Install `pip install neo4j` + add `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` to `.env.example`
2. Write `scripts/load_kg_to_neo4j.py` (~30 lines) converting `knowledge_graph.json` → Cypher `MERGE` statements
3. Add graph traversal to `retrieval/hybrid_retriever.py` (e.g., "follow citation chains" for hallucination detection)

**Hybrid approach** (recommended): Keep Qdrant for dense + sparse vector search; use Neo4j as a secondary store for structured graph traversal queries. Sync from Qdrant chunk payloads to Neo4j nodes/edges on ingestion. This avoids migrating the existing 13K chunks out of Qdrant.

### 10.3 Phase 14 Update — Knowledge Graph Engine ✅

> See also `task.md` §Phase 14 (below).

The Phase 14 knowledge graph engine (`app/knowledge_graph/`) is **✅ Complete (2026-08-08)** — the runtime engine extracts entities from individual case-file documents (cases, FBOs, inspectors, samples, lab reports, legal provisions, evidence), not the FSSAI corpus.

**Implementation**: `KnowledgeGraphEngine` extracts 8 node types (Case, FBO, Inspector, Sample, Lab, LegalSection, Evidence, Ancillary) and 6 directed edge types (INSPECTED_BY, SAMPLED_FROM, TESTED_AT, VIOLATED_SECTION, SUPPORTED_BY, REFERENCES) from `CaseFile`/`Adjudication` records. Returns Cytoscape.js-compatible JSON. Persists to `Entity`/`Relationship` tables for case_file only (idempotent). Blueprint registered at `/knowledge-graph` with view + JSON API routes. Neo4j sync adapter in `app/knowledge_graph/neo4j_sync.py` available via `ENABLE_NEO4J_SYNC` flag (dormant by default). `tests/test_knowledge_graph.py` — **21/21 pass**.

**Key distinction**: The corpus KG (§10.1) covers the FSSAI rulebook. The Phase 14 engine (§10.3) covers individual case files and their evidence chain.

### 10.4 Phase 19 Update — AI Case Intelligence

Phase 19 would benefit from both the corpus knowledge graph (for legal provision context) and the runtime knowledge graph (for case-specific entity traversal). The `knowledge_graph.json` artifact provides the authority normalization map and section semantic descriptions that `MetadataAdapter` and `CitationAdapter` could consume.

---

---

## 12. UI/UX Usability & Loading Plan (2026-08-24)

### ✅ Completed (commit `a871966`)

| # | Upgrade | Detail |
| - | ------- | ------ |
| 1 | Self-hosted FontAwesome 6.4.0 | `app/static/vendor/fontawesome/` (all.min.css + fa-solid-900/fa-regular-400 woff2). Removes cdnjs.cloudflare.com third-party request; zero template changes for the ~35 icons in use. |
| 2 | Self-hosted Google Fonts | Inter (300–700) + Merriweather (400/600) as 12 unicode-range-subset woff2 + `app/static/vendor/fonts/gf.css`. Georgia dropped from the request (system font). Removes fonts.googleapis.com/gstatic requests. |
| 3 | Static cache busting | `create_app` context processor wraps `url_for('static', ...)` to append `?v=<file mtime>` — deploys automatically invalidate browser caches across all 51 templates. |

Verified: rendered pages contain zero third-party asset references; vendored assets serve 200; auth suite 82 pass.

### ⬜ Pending (priority order)

| # | Upgrade | Detail |
| - | ------- | ------ |
| 4 | Move inline timeline-picker CSS out of `base.html` (~150 lines) into a cached `.css` file | base.html shrinks; login page stops carrying picker styles. |
| 5 | Flash messages: auto-dismiss success/info after N seconds + `aria-live="polite"` region | Users currently train to ignore persistent flashes. |
| 6 | Case-picker feed fetch on first open (not page load) if not already deferred | Free per-page-load win; verify current behavior first. |
| 7 | Table wrappers for mobile: `.table-wrap { overflow-x: auto }` applied to wide admin/list tables | Cheapest mobile fix; no per-page redesign. |
| 8 | Login form: add `autocomplete="username"` / `autocomplete="current-password"` | Password-manager support. |
| 9 | Reuse `task_status.js` spinner/disabled pattern for slow form buttons lacking pending state (PDF generation, RAG query) | Consistent loading feedback. |

### Explicitly rejected

- Inline-SVG replacement of all FontAwesome icons (~35 icons × 51 templates diff for bytes now served locally anyway)
- Dark mode, Tailwind, React/Vite migration, any JS build pipeline — violates the keep-Flask/no-build-pipeline decision (AGENTS.md §1); server-rendered Jinja2 + vanilla JS stays.
