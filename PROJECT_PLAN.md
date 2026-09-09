# NSA Webservice — Project Plan & Task List

> **Status:** ✅ Phases 0–16, 19, 20, 21, Phase A + OCR Phases B–E, Deepening D1–D5, S6a–d, S7, S2, S10a–c, S9a, Priority 6 infra, Priority 7, Phase 15, Phase 18, Work Diary, Security close-out, Redis/Celery fix, Case File Preview, Adjudication Preview, Phase 12, CI/CD gates, Pre-commit hook, Phase 6 deferred — all implemented & verified. Total ~1,970 tests. Phases 17 ⚠️ pending (Supabase bridge, conflict resolution, sync-status UI). Phase 19 ✅ complete (AI Case Intelligence). ENV-2/3/5/10 open.

> **Purpose:** Consolidated, actionable, highly detailed implementation plan and TODO list for AI agents and developers. Organized by priority with checkboxes, explicit file targets, data schemas, function signatures, routes, acceptance criteria, and testing strategies.

---

## 1. Phase Status Overview

### ✅ Completed (Phases 0–9)

| Phase | Feature                                                                           | Status | Key Files                                                        |
| ----- | --------------------------------------------------------------------------------- | ------ | ---------------------------------------------------------------- |
| 0     | Architecture (keep Flask) + JS linting                                            | ✅     | `package.json`, `eslint.config.js`, `.github/workflows/lint.yml` |
| 1     | Core petition engine                                                              | ✅     | `editor.js`, `document_viewer/routes.py`, `petition.html`        |
| 2     | Rich editor — Quill 2.x, image upload, Markdown export                            | ✅     | `editor.js`, `markdown_export.py`                                |
| 3     | Local DB — Settings/Annexure/Evidence/Version models, FTS5 search, backup/restore | ✅     | `app/models/`, `app/search/`, `app/utils/backup.py`              |
| 4     | Annexure management — upload, replace, A/B/C letters                              | ✅     | `app/annexure/`                                                  |
| 5     | Evidence — unified model, drag-drop, compression, thumbnails, search              | ✅     | `app/evidence/`, `unify_photo_evidence` migration                |
| 6     | Cross-reference engine                                                            | ✅     | `app/cross_reference/`                                           |
| 7     | Dynamic TOC                                                                       | ✅     | `app/toc_generator/`                                             |
| 8     | PDF assembly engine                                                               | ✅     | `app/pdf_assembly/`                                              |
| 9     | Version control — compare, restore, branching, history UI                         | ✅     | `app/version_control/`, `app/services/version_control.py`        |

### ✅ / ⚠️ / ❌ Status (Phases 11–21 + RAG & Infra Stack)

| Phase              | Feature                                                          | Status                              | Key Files                                                                                                |
| ------------------ | ---------------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 10                 | Fuzzy search                                                     | ✅                                  | `rapidfuzz` fallback (`fuzzy_search_fallback`), `fuzzy` API/UI toggle, 56 tests                          |
| 11                 | AI assistant                                                     | ✅                                  | `app/ai_assistant/` (service + routes + tasks + JS sidebar); 23 tests pass                               |
| 12                 | Legal rule engine                                                | ✅                                  | `app/validation/` — 7 rules + engine + 2 routes + UI; 46 tests pass                                      |
| 13                 | Timeline engine + Gantt UI                                       | ✅                                  | `app/timeline/` — extraction engine, view/API/refresh routes, vertical + Gantt UI; 21 tests pass         |
| 14                 | Knowledge graph                                                  | ✅                                  | Full engine + Cytoscape.js UI + API + SQL persistence + 21 tests + Neo4j Aura                            |
| 15                 | Analytics dashboard                                              | ✅                                  | `app/analytics/` — `GET /analytics/` dashboard + `GET /analytics/api/metrics`; 15 tests pass             |
| 16                 | Backup & export — JSON export, ZIP export, case import           | ✅                                  | 14 tests in `tests/test_case_backup.py` all pass                                                         |
| 17                 | Cloud sync — Supabase bridge, annexure sync, conflict resolution | ⚠️ R2/B2 + Cloudinary + Sheets done | Supabase bridge, conflict resolution, sync-status UI                                                     |
| 18                 | Multi-user RBAC                                                  | ✅                                  | Role/UserRole/Comment models + `enforce_rbac` gate + `fso` role + record-level scoping; 44/44 tests pass |
| Work Diary         | Per-FSO inspection diary + official report PDF                   | ✅                                  | `app/workdiary/` blueprint; 28/28 tests pass                                                             |
| Security close-out | S10c backup monitoring + S2 CSP-report collector                 | ✅                                  | 12/12 tests pass                                                                                         |
| 19                 | AI case intelligence — evidence strength, readiness score        | ✅                                  | `app/case_intelligence/`; 12/12 tests pass                                                               |
| 20                 | Plugin architecture                                              | ✅                                  | `app/plugins/` (base ABCs + `PluginRegistry` + 4 providers); 23/23 tests pass                            |
| 21                 | Food Cell — DO Intimation                                        | ✅                                  | `app/food_cell/` blueprint; 15/15 tests pass                                                             |

### RAG & Infrastructure Stack (Sub-phases)

| Sub-Phase                                  | Status | Tests | Date       |
| ------------------------------------------ | ------ | ----- | ---------- |
| RAG Phase 1 — Corpus pipeline              | ✅     | 117   | 2026-08-08 |
| RAG Phase 2 — Generation                   | ✅     | 40    | 2026-08-09 |
| RAG Phase 3 — Verification                 | ✅     | 48    | 2026-08-09 |
| RAG Phase 4 — Evaluation                   | ✅     | 49    | 2026-08-09 |
| RAG Phase 5 — Integration                  | ✅     | 31    | 2026-08-09 |
| RAG Phase 3 Agent A (ingest+e2e)           | ✅     | 25    | 2026-08-09 |
| Multi-Domain Phase 1                       | ✅     | 37    | 2026-08-20 |
| P1-4 FSSAI re-ingest                       | ✅     | 15    | 2026-08-11 |
| Evaluation Framework                       | ✅     | 77    | 2026-08-12 |
| Benchmark v1.0                             | ✅     | 150q  | 2026-08-12 |
| Rust PyO3 Normalizers                      | ✅     | 45+   | 2026-08-12 |
| Remote Inference (Modal)                   | ✅     | 55    | 2026-08-16 |
| LangGraph Agent Pipeline                   | ✅     | 41    | 2026-08-16 |
| M5 Checkpointing + HITL                    | ✅     | 15    | 2026-08-16 |
| FastAPI ASGI Gateway                       | ✅     | 50    | 2026-08-19 |
| Config seam (`app/shared/config.py` `cfg`) | ✅     | —     | 2026-08-22 |
| Phase 6 (full rewrite)                     | ❌     | —     | deferred   |

---

## 2. Recommended Implementation Order

| Step | Phase       | Action                                                                                                                                                                            | Key Files                                                                                      |
| ---- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 1–8  | Phases 0–10 | ✅ Done                                                                                                                                                                           | (complete)                                                                                     |
| 9    | S9a         | ✅ Fully fixed (2026-08-06) — inspection PUT 409 tuple; 4/4 pass                                                                                                                  | `app/models/inspection.py`, `app/inspection/routes/inspection_routes.py`                       |
| 10   | Perf        | ✅ 7/7 done — connection pooling, FSO `@lru_cache`, Jinja2 bytecode cache, Flask-Compress, health endpoint, DB indexes, eager loading                                             | `app/__init__.py`, `app/utils/fso_data.py`, `app/extensions.py`, `app/health/routes.py`        |
| 11   | Phase A     | ✅ Done (2026-08-06) — OCR extraction services + Celery task; 14/14 pass                                                                                                          | `app/services/ocr_extraction.py`, `app/services/page_splitter.py`, `app/ocr_pipeline/tasks.py` |
| 12   | Phase 12    | ✅ Done (2026-08-07) — `app/validation/` (7 rules + engine + routes); blueprint registered; shared `validation_drawer.js` UI; 46 tests pass                                       | new blueprint                                                                                  |
| 13   | Phase 13    | ✅ Done (2026-08-06) — `TimelineEngine` + 3 routes + vertical-timeline + Gantt UI + global case-picker + entry points; 21 tests pass                                              | `app/timeline/engine.py`, `app/timeline/routes.py`, `app/templates/base.html`                  |
| 14   | Phase 15    | ✅ Done (2026-08-22) — `app/analytics/` blueprint at `/analytics`: Chart.js + Leaflet map + `GET /analytics/api/metrics`; 15 tests pass                                           | `app/analytics/`, `tests/test_analytics.py`                                                    |
| 15   | Phase 18    | ✅ Complete (2026-08-26) — `app/shared/rbac.py` (`enforce_rbac` gate, `ROLE_BLUEPRINTS`), `scoped_officer_name()`/`case_visible_to_user()` record-level scoping; 44/44 tests pass | `app/__init__.py`, `app/shared/rbac.py`, `app/comments/`, `app/auth/provisioning.py`           |
| 16   | Phase 16    | ✅ Done — JSON export, ZIP export, case import, daily Celery beat, settings UI, 14 tests pass                                                                                     | `case_file_generator/routes.py`, `celery_app.py`                                               |
| 17   | Phase 11    | ✅ Complete (2026-08-08) — `app/ai_assistant/` (service + routes + tasks + JS sidebar); 23/23 tests pass                                                                          | `app/ai_assistant/`, `app/static/js/ai_assistant.js`                                           |
| 18   | Phase 19    | ⚠️ Create `app/case_intelligence/` (evidence strength, readiness score)                                                                                                           | new blueprint                                                                                  |
| 19   | Phase 14    | ✅ Done (2026-08-08) — KnowledgeGraphEngine + Cytoscape.js UI + API + SQL persistence + Neo4j Aura; 21 tests pass                                                                 | `app/knowledge_graph/`                                                                         |
| 20   | Phase 20    | ✅ Complete (2026-08-18) — plugin architecture with registry + ABCs, all 6 callers refactored, 23 tests pass, config-driven provider selection                                    | new blueprint                                                                                  |
| 21   | Phase 17    | ⚠️ R2/B2 + Cloudinary + Sheets done; Supabase bridge, conflict resolution, sync-status UI pending                                                                                 | `app/sync/`                                                                                    |
| 22   | Phase 21    | ✅ Done (2026-08-06) — Food Cell DO Intimation: `app/food_cell/` blueprint; `DoIntimation` model; 15 tests pass                                                                   | food_cell package, models/food_cell.py, sample/routes.py                                       |

---

## 3. Rust Refactoring — 5-Part Implementation Plan (created 2026-08-12)

> **Source:** `docs/RUST_REFACTORING_EVALUATION.md`. Strategy: **PyO3 + maturin** extension modules compiled to a Python-importable `nsa_rust` module, with a **pure-Python fallback** always intact (graceful degradation, matching the project's existing pattern). The 1,757-test suite must pass unchanged.
> **Ordering rationale:** Part 1 is the _easiest_ (pure string functions, clean boundary, 45-test parity net) — done first to prove the build+pipeline end to end with low risk. Part 5 (Legal Engine) is the _highest ROI_ but the _hardest_ port, so it is last. No Rust toolchain was present on 2026-08-12; it was installed (`rustup` → rustc/cargo 1.97.1) and `maturin 1.14.1` added to the venv as the first action of Part 1.

### Part 1 — Rust Toolchain + Document Cleaner Port (`nsa_rust::cleaner`) — SOURCE DONE, BUILD BLOCKED

> **Build Status (2026-08-25):** The Rust source code is **complete and written** (all 4 `.rs` files + `Cargo.toml` + `Cargo.lock`). The Python fallback wiring in `app/document_cleaner/pipeline.py` is **done** (3 wrapper functions try `from nsa_rust import …` → `None` on ImportError → pure-Python path). Tests exist in `tests/test_rust_normalizers.py` (uses `pytest.importorskip`).
>
> **HOWEVER: the extension has NEVER been successfully compiled.** `maturin build --release` fails on this Windows dev machine: `linker link.exe not found` — the MSVC linker / Windows 10 SDK requires admin elevation for installation (exit code 5007, UAC prompt), which a headless shell cannot provide. On a Linux host (e.g. Render), the linker is available and the build would succeed.
>
> The `nsa_rust` module is **NOT installed**: `python -c "import nsa_rust"` → `ModuleNotFoundError`. `maturin` is in the venv (installed separately) but is NOT in `requirements.txt` / `requirements-dev.txt`. There is **no `[tool.maturin]` section** in any `pyproject.toml`, and `render.yaml`'s `buildCommand` does NOT include any Rust/maturin build step.
>
> **Bottom line:** Part 1 source is done ✅. The app works fine without it (pure-Python fallback is active). To ship the extension, you must (a) add `maturin` to `build-system.requires` in `pyproject.toml`, (b) add `[tool.maturin]` config, and (c) add `maturin build + pip install .whl` to `render.yaml`'s `buildCommand`. See "Rust Build & Deploy Plan" below (§0).

### Part 2 — Search Fuzzy Helpers Port (`nsa_rust::search_fuzzy`)

- **Goal:** Accelerate the pure helper functions behind fuzzy search.
- **Targets:** `app/search/indexer.py` — `_field_score`, `_find_match_spans`, `_snippet_around_matches`, `_apply_marks`, `_expand_to_word` (pure, ~150 LOC). The main `fuzzy_search_fallback()` stays in Python (it is DB/ORM-coupled); only the pure helpers move to Rust.
- **Steps:** port helpers → build → wire into `fuzzy_search_fallback()` → parity vs `tests/test_search.py` (56).
- **Acceptance:** ≥2× fuzzy search; 56/56 tests identical.

### Part 3 — TOC Generator + Cross-Reference Port (`nsa_rust::toc`, `nsa_rust::cross_reference`)

- **Goal:** Accelerate HTML pre-processing for PDF generation.
- **Targets:** `app/toc_generator/engine.py` (293 LOC), `app/cross_reference/engine.py` (495 LOC).
- **Steps:** port both → build → wire into `PDFAssemblyEngine.post_process()` → parity vs `tests/test_phase7_toc_generator.py` (37) + `tests/test_cross_reference.py` (27).
- **Acceptance:** ≥2–3× pre-processing; 64/64 tests identical.

### Part 4 — RAG Enrichment + Verification Port (`nsa_rust::enrichment`, `nsa_rust::verification`)

- **Goal:** Accelerate ingestion enrichment and hallucination detection.
- **Targets:** `app/rag/enrichment/deterministic.py`, `entity_extractor.py`, `citation_adapter.py`, `crossref_adapter.py`, `metadata_adapter.py`; `app/rag/verification/claim_extractor.py`, `evidence_verifier.py`, `hallucination_detector.py`, `citation_validator.py`, `token_counter.py`.
- **Steps:** port enrichment (+ ray-pll `rayon` for the evidence-verifier per-pair loop) → build → wire into `IngestionPipeline
