# Agent Reference — NSA Webservice
> **Status:** ✅ Phases 0–10, Deepening D1–D5, Infrastructure, Phase 10 fuzzy search (56 tests), Phase 16 (backup/export/import, 14 tests), Phase A (OCR pipeline foundation, 14 tests), and Phase 13 (timeline engine + Gantt UI + global case-picker + entry points across the UI, 21 tests) all implemented & verified. S9a concurrency guard fully fixed (`tests/test_concurrency_inspection.py` 4/4 pass). Performance Quick Wins **7/7 complete** (FSO `@lru_cache`, Jinja2 bytecode cache, Flask-Compress, connection pooling, health endpoint, DB indexes, eager loading). **Phase 21 ✅ Complete (2026-08-06)** — Food Cell DO Intimation: `app/food_cell/` blueprint + `DoIntimation` model/migration + Celery task + post-save hook in `app/sample/routes.py` + sync (Sheets/Airtable/Excel); `tests/test_food_cell_do_intimation.py` **15/15 pass**. Priority 7 (Multi-Target Sheets Redundancy — Airtable + MS Excel) designed & documented in `plan.md` §8 and `task.md` §Priority 7. Phases 11–12, 14–15, 17–20 pending.


> **Purpose:** Single reference for any AI agent or developer working in this codebase. Covers project context, architecture, key patterns, directory map, and deletion history. **Read this first, then `plan.md` and `task.md`.**

---

## 1. Project Overview

**NSA Webservice** is a government-grade legal workflow platform for Food Safety Officers (FSOs). It digitizes the complete lifecycle of food-safety legal proceedings under the **Food Safety and Standards Act, 2006 (FSS Act)**: inspection → sample tracking → case file generation → adjudication → billing → audit trail.

- **Stack:** Flask 2.x + SQLAlchemy 2.x + PostgreSQL (primary) / SQLite (dev) + WeasyPrint + Celery/Redis + QStash
- **Frontend:** Jinja2 + Vanilla JS + Quill 2.x (server-rendered, no React build pipeline)
- **Python:** 3.12+
- **Version:** v0.8.0

### Key Domain Split

- **CaseFile** — sample-based violations (petition + permission letter)
- **Adjudication** — non-sample adjudications (different template, same engine family)

### Architectural Decisions (documented, do not re-litigate)

- **Keep Flask** (not React) — roadmap's React prescriptions are skipped by decision
- **Canonical Key Contract** — `app/shared/case_keys.py` defines uniform field names across all modules
- **Hash-chained audit** — `AuditLog` uses SHA-256 chaining for tamper evidence
- **Optimistic concurrency** — `version_id` columns + `StaleDataError` → 409 on conflict
- **Storage abstraction** — `app/utils/storage.py` branches to Cloudinary / R2 / local per env vars
- **Race-safe sequences** — `CodeSequence` table + PostgreSQL advisory locks for unique code generation

---

## 2. Directory Map (as of v0.8.0)

```
NSA_webservice/
├── app.py                        # WSGI entry point
├── celery_app.py                 # Celery factory
├── app/
│   ├── __init__.py               # App factory, blueprint registration, security headers
│   ├── extensions.py             # db, csrf, login_manager, talisman singletons
│   ├── audit_hooks.py            # SQLAlchemy after_flush audit event hooks
│   ├── models/                   # Split from monolithic models.py
│   │   ├── __init__.py           # Re-exports all models (backward-compatible)
│   │   ├── auth.py               # User, RecordAudit
│   │   ├── billing.py            # Bill, BillSample, CodeSequence, Sample
│   │   ├── config.py             # AppSecret, Settings
│   │   ├── document.py           # Adjudication, Annexure, CaseFile, Evidence, Version
│   │   ├── inspection.py         # FSO, AuditLog, Inspection
│   │   ├── issue.py              # FboIssue, FboIssueAudit
│   │   └── food_cell.py          # DoIntimation (DO intimation record)
│   ├── adjudication/             # Non-sample adjudication
│   ├── annexure/                 # Annexure upload + metadata
│   ├── audit/                    # Audit log viewer
│   ├── auth/                     # Login/logout/change-password
│   ├── bill_generator/           # Bill PDF (QStash async)
│   ├── billing/                  # Billing summary + Excel
│   ├── case_file_generator/      # Sample-based petition/PDF generation
│   ├── document_cleaner/         # Legal text cleaning pipeline
│   ├── document_loader/          # PDF/DOCX/TXT ingestion
│   ├── document_viewer/          # Quill editor + save/restore
│   ├── evidence/                 # Unified Evidence model + UI
│   ├── fbo_issue/                # State machine + audit trail
│   ├── food_cell/                # Phase 21 DO Intimation workflow (2026-08-06)
│   ├── health/                   # GET /health probe (public)
│   ├── inspection/               # CRUD + photos + OCR (split into routes/ package)
│   │   └── routes/               # Modular: inspection_routes, lookup_routes,
│   │                             #   photo_routes, derived_views
│   ├── legal_analysis/           # Legal paragraph detection workbench
│   ├── metadata_extractor/       # Regex + NER field extraction
│   ├── ocr_pipeline/             # PaddleOCR + Tesseract
│   ├── pdf_assembly/             # PDF assembly engine
│   ├── sample/                   # Sample tracking CRUD
│   ├── search/                   # SQLite FTS5 + API
│   ├── services/                 # Business logic services
│   │   ├── legal_engine.py       # Legal engine wrapper
│   │   ├── sheets_sync.py        # Google Sheets sync
│   │   ├── version_control.py    # Version compare/restore, branching
│   │   └── audit.py
│   ├── settings/                 # Settings + backup/restore
│   ├── shared/                   # Canonical keys + context deriviners
│   ├── tasks_webhook/            # QStash webhook + task status
│   ├── timeline/                 # Phase 13 milestone timeline + Gantt UI
│   ├── toc_generator/            # Dynamic TOC extraction/numbering
│   ├── utils/                    # Filters, storage, pdf_utils, lookup, etc.
│   ├── static/                   # CSS, JS (Quill vendor, editor.js, task_status.js)
│   ├── templates/base.html       # Master layout (global Timeline case-picker)
│   └── version_control/          # Version history UI + routes
├── migrations/                   # Alembic — 27 migration files (newest: fix_rbac_tables)
├── tests/                        # 39 pytest modules, ~700+ test cases
├── legal_paragraph_detection_engine/  # Standalone rule-based legal parser
├── scripts/                      # Utility scripts (create_user.py kept; others deleted)
├── .github/workflows/            # CI: lint, pip-audit, validation, deploy, docker-build, release
├── pyproject.toml                # Dependencies + tool config (setuptools build)
├── requirements.txt              # -e . + dev deps
├── render.yaml                   # Deploy config
└── .env.example                  # 15 environment variables
```

### Registered Flask Blueprints (20)

| Blueprint           | Prefix               | Purpose                                     |
| ------------------- | -------------------- | ------------------------------------------- |
| auth                | /auth                | Login, logout, change password              |
| case_file_generator | /case_file_generator | Petition + permission letter (sample-based) |
| adjudication        | /adjudication        | Non-sample adjudication                     |
| document_viewer     | /document_viewer     | Quill editor, save/restore, PDF             |
| evidence            | /evidence            | Evidence library (photos, reports, etc.)    |
| bill_generator      | /bill_generator      | Bill PDF (async via QStash)                 |
| billing             | /billing             | Billing summary + Excel export              |
| fbo_issue           | /fbo-issue           | FBO issue state machine                     |
| sample              | /sample              | Sample tracking CRUD                        |
| inspection          | /inspection          | Inspection CRUD + photos + OCR              |
| legal_analysis      | /legal               | Legal paragraph detection workbench         |
| audit               | /admin               | Read-only audit log viewer                  |
| settings            | /settings            | Settings dashboard, backup/restore          |
| version_control     | /api/version-control | Version history UI + API                    |
| search              | /search              | FTS5 + fuzzy search API                     |
| tasks_webhook       | _(none)_             | QStash webhook, task status                 |
| annexure            | /annexure            | Annexure upload + metadata                  |
| timeline            | /timeline            | Phase 13 milestone timeline + Gantt UI      |
| health              | /health              | Health probe (public)                      |
| food_cell           | /food-cell           | Phase 21 DO Intimation workflow             |

---

## 3. Key Patterns for Agents

### 3.1 Blueprint Registration

New blueprints are registered in `app/__init__.py::create_app()`. Add the import + `app.register_blueprint(bp, url_prefix=...)` in the alphabetical section.

### 3.2 Model Imports

All models are importable from `app.models` (re-exported). New models go in `app/models/<subcategory>.py` and are added to `app/models/__init__.py`.

### 3.3 PDF Generation (centralized)

All HTML→PDF goes through `app/utils/pdf_utils.py::generate_pdf_from_html()`. WeasyPrint is imported defensively (`import_weasyprint()`) so the app still boots on hosts without GTK.

### 3.4 Async Tasks

QStash (webhook-based, no worker required on free tier) + Celery (for heavier jobs). `celery_app.py` creates the Celery instance.

### 3.5 Security

- `app/__init__.py::create_app()` sets up Talisman CSP, ProxyFix, CSRF, Flask-Login
- Global `before_request` gate (`require_login`) — all routes require auth except listed `public_endpoints`
- Audit hooks fire on SQLAlchemy `after_flush` for Adjudication, Bill, CaseFile
- Security TODOs tracked in `task.md` (S7, S2, S9a, S6a, etc.)

### 3.6 Deprecation Notes

- `datetime.utcnow()` → use `datetime.now(timezone.utc)` throughout
- `Model.query.get()` → use `db.session.get(Model, id)`
- `db.get_engine()` → use `db.engines['default']` in migrations

---

## 4. How to Work Here

### Running Tests

```bash
python -m pytest tests/ -v          # full suite (~500+ tests)
python -m pytest tests/test_<x>.py  # targeted
```

### Running the App (dev)

```bash
python app.py                      # default port 8000
FLASK_APP=app.py flask run         # alternative
```

### Linting / Formatting

```bash
ruff check .                       # lint
ruff format .                      # format
mypy .                             # type check
npm run lint                       # JS lint (ESLint + Prettier)
```

### Migrations

```bash
flask db migrate -m "description"  # generate
flask db upgrade                   # apply
```

### Environment

Copy `.env.example` → `.env`, set `DATABASE_URL`, `SECRET_KEY`, `SPREADSHEET_ID`, etc.

---

## 5. Deletion History (what was cleaned up)

The following were deleted in the 2026-08-03/04 cleanup commit (`deletion_plan.md`, `cleanup_report.md`):

| Category              | What was removed                                                                    | Reason                                                                                          |
| --------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Root duplicates (S6a) | `suggester.py` (120 LOC, root)                                                      | Orphaned duplicate; app uses `app/utils/suggester.py` (docstring + annotations backported)       |
| Root duplicates (S6b) | `sections_data.py` (root)                                                           | Older duplicate; app uses `app/utils/sections_data.py` (pathlib, typed)                          |
| Tracked bytecode      | `__pycache__/app/suggester/sections_data.cpython-313.pyc`                           | Compiled artifacts committed despite `__pycache__/` in `.gitignore`                              |
| Broken duplicate      | `legal-paragraph-detection-engine/` (5,095 LOC)                                     | Dashed duplicate with broken imports; app uses `legal_paragraph_detection_engine/` (underscore) |
| Scratch scripts       | `_fix_*.py`, `_write_*.py`, `_test_*.py`, `_debug_*.py`                             | Never committed, local debugging                                                                |
| One-off check scripts | `check_*.py`, `search_*.py`, `filter_house_number.py`, `fuzzy_dedup_stage0.py`      | 0 importers, 0 CI references                                                                    |
| AI tool artifacts     | `.pi-subagents/artifacts/`, `piolium/`, `.opencode/plans/`, `.ai/PROJECT_MEMORY.md` | Agent harness working state                                                                     |
| Debug snapshots       | `_init_full.txt`, `_plan_output.txt`, `_roadmap_out.txt`, etc.                      | AI tooling output                                                                               |
| Runtime artifacts     | `instance/saved/*.html`, `instance/editor_images/*.png`                             | Now gitignored                                                                                  |
| CSV data files        | `*.csv` (root)                                                                      | ~70MB, untracked via `.gitignore`                                                               |

### AI Planning Docs Removed (consolidated into this file set)

- `AST_SKELETONIZATION.md` (1,964 lines) → codebase structure → this file (§2)
- `ENGINEERING_ASSESSMENT.md` (1,390 lines) → performance bottlenecks → `plan.md` §4
- `ROADMAP_ALIGNMENT_REPORT.md` (1,159 lines) → roadmap phases → `plan.md` + `task.md`
- `LEGAL_ENGINE_ANALYSIS_TODO.md` → merged into `task.md`
- `IMPLEMENTATION_PLAN.md` (141 lines) → was a status snapshot; superseded by `plan.md` + `task.md`
- `cleanup_report.md` (248 lines) → this file §5
- `deletion_plan.md` (212 lines) → this file §5
- `technical_debt_implementation_plan.md` (435 lines) → already completed, results captured here
- `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md` (328 lines) → `task.md` Cloudinary section

---

## 8. Known Shallow Modules & Architectural Friction

The following modules have low Module Depth (interface nearly as complex as implementation). They are the targets of the ongoing deepening effort in `task.md` §Deepening Tasks. Current status and refined targets:

### ✅ CONFIRMED SHALLOW MODULES (Priority Order)

1. **Cross-module case resolution** — **HIGHEST PRIORITY (D1)**
   - **Problem**: `_resolve_case()` in `document_viewer/routes.py` (lines 66-78), `_resolve_target()` + `_kind_param()` in `version_control/routes.py` (lines 39-68), and inline lookups in evidence/search/annexure all solve the same "CaseFile vs Adjudication ID" problem
   - **Target**: `CaseResolver` class in `app/shared/case_resolver.py` with `ResolvedCase` dataclass
   - **Interface**: `resolve(case_id, kind=None) -> ResolvedCase | None`
   - **Dependencies**: None (prerequisite for D2 and D5)
   - **Effort**: 1 day | **Risk**: Low | **Module Depth**: 1 → 4

2. **Document viewer inlined concerns** — **PRIORITY 2 (D2)**
   - **Problem**: Route file directly calls `VersionService`, `log_audit`, `save_saved_document` via 5 private helpers (`_resolve_case`, `_save_document_content`, `_log_audit`, `_snapshot_version`, `_actor`)
   - **Target**: `DocumentSaveCoordinator` class in `app/services/document_lifecycle.py`
   - **Interface**: `save(case_id, case_type, doc_type, html, delta, force_snapshot=False) -> SaveResult`
   - **Dependencies**: D1 (CaseResolver)
   - **Effort**: 1 day | **Risk**: Low | **Module Depth**: 2 → 4

3. **PDF utils grab-bag** — **PRIORITY 3 (D3)**
   - **Problem**: `app/utils/pdf_utils.py` mixes WeasyPrint import guard, bookmark CSS, post-processing orchestration, and image embedding
   - **Current State**: `PDFAssemblyEngine` class already exists in `app/pdf_assembly/__init__.py` (42KB) but needs consolidation
   - **Target**: Complete `PDFAssemblyEngine` in `app/pdf_assembly/engine.py` with clean interface
   - **Interface**: `assemble()`, `post_process()`, `embed_photos()`, `generate_from_html()`
   - **Dependencies**: None
   - **Effort**: 2 days | **Risk**: Medium | **Module Depth**: 3 → 4

4. **Inspection routes mechanical split** — **PRIORITY 4 (D4)**
   - **Problem**: `photo_routes.py` (15,111 bytes, ~400+ lines) mixes EXIF extraction, validation, storage, OCR dispatch, and routes
   - **Target**: `InspectionPhotoService` class in `app/inspection/photo_service.py`
   - **Interface**: `upload_evidence()`, `upload_adjudication_photo()`, `delete()`, `list_for_inspection()`, `list_adjudication()`
   - **Dependencies**: None
   - **Effort**: 2 days | **Risk**: Medium | **Module Depth**: 1 → 4

5. **Case/Adjudication route duplication** — **PRIORITY 5 (D5)**
   - **Problem**: `case_file_generator/routes.py` (697 lines) and `adjudication/routes.py` (820 lines) are near-mirrors with duplicated logic
   - **Target**: `DocumentCaseManager` class in `app/shared/document_case_manager.py`
   - **Interface**: Parameterized by `(model, template_dir, bp, case_type, sections_fn)` with methods for CRUD, rendering, and document generation
   - **Dependencies**: D1 (CaseResolver), D2 (DocumentSaveCoordinator)
   - **Effort**: 3 days | **Risk**: Medium | **Module Depth**: 2 → 4

### 📊 REFACTORING METRICS

| Module | Current Lines | Current Depth | Target Depth | Complexity Reduction |
|--------|---------------|----------------|--------------|---------------------|
| CaseResolver | N/A | N/A | 4 | New abstraction |
| DocumentSaveCoordinator | N/A | N/A | 4 | Encapsulates 5 helpers |
| PDFAssemblyEngine | ~1000+ | 3 | 4 | Consolidates PDF concerns |
| InspectionPhotoService | ~400+ | 1 | 4 | Separates business logic |
| DocumentCaseManager | ~1500+ | 2 | 4 | Eliminates duplication |

### 🎯 RECOMMENDED IMPLEMENTATION ORDER

1. **D1: CaseResolver** (Foundation - no dependencies)
2. **D2: DocumentSaveCoordinator** (Depends on D1)
3. **D3: PDFAssemblyEngine** (Independent, can run parallel)
4. **D4: InspectionPhotoService** (Independent, can run parallel)
5. **D5: DocumentCaseManager** (Depends on D1+D2)

**Parallelization**: D3 and D4 can be implemented concurrently with D1+D2

---

## 6. Environment Variables (`.env.example`)

| Variable                                                                 | Purpose                                        |
| ------------------------------------------------------------------------ | ---------------------------------------------- |
| `SECRET_KEY`                                                             | Flask session signing (required in production) |
| `DATABASE_URL`                                                           | PostgreSQL or SQLite URL                       |
| `SPREADSHEET_ID`                                                         | Google Sheets sync target                      |
| `GOOGLE_CREDENTIALS_JSON`                                                | Service account for Sheets API                 |
| `REDIS_URL`                                                              | Celery broker + cache                          |
| `CLOUDINARY_CLOUD_NAME` / `CLOUDINARY_API_KEY` / `CLOUDINARY_API_SECRET` | Cloudinary photo storage (optional)            |
| `R2_*` / `B2_*`                                                          | S3-compatible storage fallback                 |
| `PDF_ENABLE_HYPERLINKS`                                                  | Toggle PDF link annotation (default on)        |
| `PDF_ENABLE_QR_CODES`                                                    | Toggle QR in PDFs (default off)                |
| `PDF_ENABLE_SIGNATURES`                                                  | Toggle signature placeholders                  |
| `PDF_USE_DIRECT_URLS`                                                    | Embed photo URLs directly vs base64            |

---

## 7. Test Inventory

| Test File                    | Tests | Covers                                                                 |
| ---------------------------- | ----- | ---------------------------------------------------------------------- |
| test_annexure.py             | 22    | Annexure upload, replace, rename, reorder, delete, duplicate detection |
| test_case_backup.py          | 14    | Phase 16: JSON/ZIP export, case import                                 |
| test_case_resolver.py        | —     | CaseResolver CaseFile/Adjudication disambiguation                      |
| test_concurrency_inspection.py | 4   | S9a: StaleDataError → 409 on inspection PUT/DELETE                     |
| test_document_lifecycle.py   | —     | DocumentSaveCoordinator save/version/audit                             |
| test_food_cell_do_intimation.py | 15  | Phase 21: DO Intimation generate/forward/sync |
| test_inspection_photo_service.py | —  | InspectionPhotoService upload/verify/stamp                             |
| test_ocr_extraction.py       | 14    | Phase A: OCR field extraction + task persistence                       |
| test_auth_*.py               | 9+9   | Auth: login, password change                                           |
| test_bill_generator.py       | 11    | Bill PDF template vars                                                 |
| test_cross_reference.py      | 27    | Reference extraction/linking/renumbering                               |
| test_document_cleaner.py     | 45    | Text cleaning pipeline                                                 |
| test_document_loader.py      | 35    | PDF/DOCX/TXT loading                                                   |
| test_document_viewer.py      | 24+27 | Editor save/retrieve, Markdown export, TOC                             |
| test_legal_suggest.py        | 4     | Section suggestions                                                    |
| test_metadata_extractor.py   | 31    | Regex + NER extraction                                                 |
| test_ocr_pipeline.py         | 24    | OCR pipeline                                                           |
| test_pdf_photo_embedding.py  | 11    | Photo embedding in PDFs                                                |
| test_phase1.py               | —     | Validation errors, Facts/Grounds/Prayer                                |
| test_phase3_models.py        | —     | Settings, Annexure, Evidence, Version models                           |
| test_phase5_evidence.py      | 16    | Unified Evidence model                                                 |
| test_phase7_toc_generator.py | 37    | TOC extraction/numbering/bookmarks                                     |
| test_phase8_pdf_assembly.py  | 40    | PDF assembly, hyperlinks, QR, signatures                               |
| test_route_collisions.py     | 1     | URL collision regression                                               |
| test_search.py               | 56    | FTS5 search, fuzzy fallback, API, auto-index hooks
| test_step1-5_integration.py  | 74    | End-to-end integration                                                 |
| test_storage.py              | —     | Storage backend selection                                              |
| test_timeline.py             | 21    | Phase 13: engine, routes, picker, entry points                         |
| test_version_control.py      | 23    | Version compare, restore, branching                                    |
| test_toc_generator.py        | 37    | TOC generator engine                                                   |

---

_End of agents.md_
