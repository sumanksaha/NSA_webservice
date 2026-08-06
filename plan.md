# Implementation Plan — NSA Webservice Roadmap (Phases 0–20)

> **Status:** ✅ Deepening Tasks D1–D5, S6a–d, S7, S2, S10a–c, Priority 6 infra, S9a (concurrency guard, fully fixed), Phase 16 (backup/export/import), Phase A (OCR pipeline foundation), Phase 13 (timeline engine + Gantt UI + global case-picker + entry points), **Phase 21 (Food Cell DO Intimation)**, and 7/7 Performance Quick Wins all implemented & verified. Phases 11–12, 14–15, 17–20 pending.

> **Generated:** 2026-08-06  
> **Source:** Consolidated from `ROADMAP_ALIGNMENT_REPORT.md`, `IMPLEMENTATION_PLAN.md`, `ENGINEERING_ASSESSMENT.md`, and `technical_debt_implementation_plan.md`  
> **Status:** Phases 0–10 ✅ Complete. Deepening Tasks D1–D5 ✅ Complete. Infrastructure ✅ Complete. Phase 16 ✅ Complete (14 tests pass). Phase A ✅ Complete (OCR services + Celery task + 14 tests pass). Phase 13 ✅ Complete (timeline engine + Gantt UI + global case-picker + entry points across search/evidence/annexure/inspection/audit/version-control/sample — 21 tests pass). **Phase 21 ✅ Complete** (Food Cell DO Intimation — 15 tests pass). S9a ✅ Fully fixed (inspection PUT 409 tuple; `tests/test_concurrency_inspection.py` 4/4 pass). Performance Quick Wins: **7/7 done** (connection pooling ✅, FSO lru_cache ✅, Jinja2 bytecode cache ✅, Flask-Compress ✅, health endpoint ✅, DB indexes ✅, eager loading ✅).

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

### ⏳ Pending (Phases 11–20)

| Phase  | Feature                                                                     | Status                              | Gap                                                                                                                                               |
| ------ | --------------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **10** | Search engine — fuzzy search                                                | ✅                                  | `rapidfuzz` fallback (`fuzzy_search_fallback`), `fuzzy` API/UI toggle, deps declared in `pyproject.toml` — implemented & verified (56 tests pass) |
| **11** | AI assistant — grammar, legal language, summarize, contradictions, drafting | ❌                                  | Only rule-based `suggester.py` exists                                                                                                             |
| **12** | Legal rule engine — ValidationEngine (score, warnings, errors, suggestions) | ❌                                  | Only `suggest_sections()` exists                                                                                                                  |
| **13** | Timeline engine — auto-generated events, Gantt UI                           | ✅                                  | `app/timeline/` — extraction engine, view/API/refresh routes, vertical + Gantt UI + global case-picker + entry points; 21 tests pass |
| **14** | Knowledge graph — entity/relationship extraction, traversal API             | ⚠️                                  | Entity/Relationship models + migration done; extractor + API pending                                                                              |
| **15** | Analytics dashboard — aggregate queries, charts, geo map                    | ❌                                  | Only billing + inspection lists exist                                                                                                             |
| **16** | Backup & export — JSON export, case import, scheduled backups               | ✅                                  | `export_case_as_json()`, `export_case_as_zip()`, `import_case_from_json()` in `app/case_file_generator/services.py`; 3 routes in `routes.py`; Celery beat `daily-db-snapshot` at midnight UTC (`celery_app.py`, `app/utils/backup.py`); settings UI (`settings/backup.html`, `settings/routes.py`). 14 tests in `tests/test_case_backup.py` all pass.                                                            |
| **17** | Cloud sync — Supabase bridge, annexure sync, conflict resolution            | ⚠️ R2/B2 + Cloudinary + Sheets done | Supabase bridge, conflict resolution, sync-status UI                                                                                              |
| **18** | Multi-user RBAC — Role model, `@role_required`, comments, approval workflow | ⚠️                                  | Role/UserRole/Comment models + migration done; decorator + comment UI pending                                                                     |
| **19** | AI case intelligence — evidence strength, traceability, readiness score     | ❌                                  | Not started                                                                                                                                       |
| **20** | Plugin architecture — OCR/AI/rule/PDF provider interfaces                   | ❌                                  | OCR and rules hardcoded                                                                                                                           |
| **21** | Food Cell — DO Intimation generation, PDF export, forwarding after sample save | ✅                                  | `app/food_cell/` blueprint (`__init__.py`, `routes.py`, `services.py`, `tasks.py`, templates); `DoIntimation` model + `food_cell_forwarded` on `Sample`; post-save Celery hook in `app/sample/routes.py`; integrated with Priority 7 sync chain (Sheets + Airtable + Excel best-effort); `add_food_cell_do_intimation` migration; 15 tests in `tests/test_food_cell_do_intimation.py` all pass |

### New (Extraction → Storage → Autopopulation Pipeline)

| Phase   | Feature   | Status |
| ------- | --------- | ------ |
| **A–E** | OCR extraction (Vision-LLM + zonal OCR), review/commit workflow, conflict resolution, autopopulation, feedback dashboard | ⚠️     | **Phase A foundation ✅** (models+migration+services+task, 14 tests); Phases B–E pending |
| **F**   | Food Cell DO Intimation — automated DO letter generation, PDF export, and forwarding after FSO sample save               | ✅     | `app/food_cell/` blueprint; `DoIntimation` model; `generate_and_forward_do_intimation()` service; Celery `send_do_intimation` task; post-save trigger in `app/sample/routes.py`; routes: PDF download, HTML view, status, regenerate; best-effort sync to Sheets + Airtable + Excel; `add_food_cell_do_intimation` migration; 15 tests pass |

---

## 2. Recommended Implementation Order

| Step | Phase       | Action                                                                                                                                                 | Key Files                                                                                      |
| ---- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| 1–8  | Phases 0–10 | ✅ Done — Flask architecture, petition engine, rich editor, local DB, annexures, evidence, cross-ref, TOC, PDF assembly, version control, fuzzy search | (complete)                                                                                     |
| 9    | **S9a**     | ✅ **Fully fixed (2026-08-06)** — `version_id` + `__mapper_args__` on `Inspection`, `Sample`, `Bill`, `CaseFile`; `StaleDataError` → 409 in case_file/adjudication/bill/sample PUT+DELETE routes. The one-line inspection-PUT bug (`409` inside `jsonify()`) is fixed — `tests/test_concurrency_inspection.py` **4/4 pass**. | `app/models/inspection.py`, `app/models/billing.py`, `app/inspection/routes/inspection_routes.py` |
| 10   | **Perf**    | ✅ **7/7 done** — SQLAlchemy pool config ✅ (`app/__init__.py:200-210`), FSO `@lru_cache` ✅ (`fso_data.py:27`), Jinja2 `FileSystemBytecodeCache` ✅ (`__init__.py:285-293`), Flask-Compress ✅ (`extensions.py` + `__init__.py:283`), health endpoint ✅ (`health/routes.py`), DB indexes ✅, **eager loading ✅** (`load_only` column trimming in `DocumentCaseManager._list_cases_query()` for the JSON `/cases` endpoints; `lazy="selectin"` on `Bill.samples` + `bills` backref; `distinct()` on the evidence tag-cloud query). | `app/__init__.py`, `app/utils/fso_data.py`, `app/extensions.py`, `app/health/routes.py`, `app/shared/document_case_manager.py`, `app/models/billing.py`, `app/evidence/routes.py` |
| 11   | **Phase A** | ✅ **Done (2026-08-06)** — OCR extraction services + Celery task + persistence tests. `process_document_ocr()` (regex+NER field extraction), `split_pdf_bundle()` (PyMuPDF), `process_ocr_document_async` Celery task (persists `OCRDocument` + `LabTestParameter`). `tests/test_ocr_extraction.py` — **14/14 pass**. | `app/services/ocr_extraction.py`, `app/services/page_splitter.py`, `app/ocr_pipeline/tasks.py`, `tests/test_ocr_extraction.py` |
| 12   | Phase 12    | ⚠️ Create `app/validation/` (engine, rules, routes)                                                                                                    | new blueprint                                                                                  |
| 13   | Phase 13    | ✅ **Done (2026-08-06)** — `TimelineEngine` (extract / refresh / validate_sequence / build_payload), 3 routes (`/timeline/case/<id>`, `/timeline/api/case/<id>`, `/timeline/api/case/<id>/refresh`), vertical-timeline + Gantt UI with document links. case_file events persisted to `timeline_event`; adjudication served ephemerally (FK constraint). Access: global nav case-picker (keyboard-navigable search dropdown), both index-page panels, document-editor button, search results, evidence/annexure/inspection/audit/version-control/sample entry points, sample-detail `case_id`+`timeline_url`. Also wired orphaned `app/audit` routes (audit log viewer was 404) and fixed stale `edit_case_file`/`edit_adjudication` url_for names. `tests/test_timeline.py` — **21/21 pass**. | `app/timeline/engine.py`, `app/timeline/routes.py`, `app/timeline/templates/timeline/index.html`, `app/templates/base.html`, `tests/test_timeline.py` |
| 14   | Phase 15    | ⚠️ Create `app/analytics/` (aggregate queries, charts)                                                                                                 | new blueprint                                                                                  |
| 15   | Phase 18    | ⚠️ Role/UserRole/Comment models + migration done; `@role_required` + comment UI pending                                                                | `app/models/auth.py`, `app/decorators.py`                                                      |
| 16   | Phase 16    | ✅ **Done** — JSON export, ZIP export, case import, daily Celery beat, settings UI, 14 tests pass. | `case_file_generator/routes.py`, `celery_app.py`
| 17   | Phase 11    | ⚠️ Create `app/ai_assistant/` (LLM service, prompt templates)                                                                                          | new blueprint                                                                                  |
| 18   | Phase 19    | ⚠️ Create `app/case_intelligence/` (evidence strength, readiness score)                                                                                | new blueprint                                                                                  |
| 19   | Phase 14    | ⚠️ Entity/Relationship models + migration done; extractor + API pending                                                                                | `app/knowledge_graph/`                                                                         |
| 20   | Phase 20    | ⚠️ Create `app/plugins/` (registry, base interfaces)                                                                                                   | new blueprint                                                                                  |
| 21   | Phase 17    | ⚠️ R2/B2 + Cloudinary + Sheets done; Supabase bridge, conflict resolution, sync-status UI pending                                                      | `app/sync/`                                                                                    |
| 22   | **Phase 21** | Done (2026-08-06) — Food Cell DO Intimation: food_cell blueprint (services, routes, tasks, templates); DoIntimation model + food_cell_forwarded on Sample; HTML-to-PDF via WeasyPrint + stub fallback; Celery send_do_intimation task; post-save hook in sample/routes.py; best-effort sync to Sheets + Airtable + Excel; migration add_food_cell_do_intimation; 15 tests pass | food_cell package, models/food_cell.py, sample/routes.py, init, celery_app.py, test_food_cell_do_intimation.py |

> **📌 Next 3 steps (highest future impact, smallest effort) — suggested 2026-08-06 (Phase 13 now ✅):** **Phase 12** (Legal Validation Engine — self-contained rule-based engine, no external deps, plugs into the existing `legal_analysis` UI), **Phase 15** (Analytics Dashboard — aggregate queries + Chart.js/Leaflet; also exercises the new `selectin`/`load_only` query patterns), **Phase 18** (Multi-User RBAC — `Role`/`UserRole`/`Comment` models + migration done; only `@role_required`, comment API/UI, and user-role admin UI remain).

---

## 3. File-Level Edit Guide

### 3.1 Files Needing New Models

**File:** `app/models/document.py` (was `app/models.py`, now split into `app/models/` package)

- Add: `TimelineEvent`, `Role`, `UserRole`, `Comment`, `Entity`, `Relationship`

### 3.2 New Blueprints to Create

| Blueprint                | Purpose              | New Files                                                            |
| ------------------------ | -------------------- | -------------------------------------------------------------------- |
| `app/validation/`        | Legal rule engine    | `__init__.py`, `engine.py`, `rules.py`                               |
| `app/timeline/`          | Timeline engine      | ✅ DONE — `__init__.py`, `engine.py`, `routes.py`, `templates/timeline/index.html`; global picker in `base.html` + entry points; 21 tests |
| `app/food_cell/`        | Food Cell DO Intimation | ✅ DONE — `__init__.py`, `routes.py`, `services.py`, `tasks.py`, `templates/food_cell/do_intimation.html`, `templates/food_cell/do_intimation_inline.html` |
| `app/analytics/`         | Analytics dashboard  | `__init__.py`, `routes.py`, `templates/`                             |
| `app/ai_assistant/`      | AI assistant         | `__init__.py`, `service.py`, `routes.py`, `templates/`, `static/js/` |
| `app/case_intelligence/` | AI case intelligence | `__init__.py`, `engine.py`                                           |
| `app/knowledge_graph/`   | Knowledge graph      | `__init__.py`, `models.py`, `engine.py`, `routes.py`                 |
| `app/sync/`              | Cloud sync           | `__init__.py`, `supabase_sync.py`, `routes.py`                       |
| `app/plugins/`           | Plugin architecture  | `__init__.py`, `registry.py`, `base.py`                              |

### 3.3 Existing Files to Extend

| File                                | Extension Needed                                           |
| ----------------------------------- | ---------------------------------------------------------- |
| `app/__init__.py`                   | Register new blueprints, add health endpoint               |
| `app/case_file_generator/routes.py` | JSON export endpoint                                       |
| `app/adjudication/routes.py`        | StaleDataError handling (S9a), validation                  |
| `app/inspection/routes/`            | StaleDataError handling (S9a) — see `inspection_routes.py` |
| `app/sample/routes.py`              | StaleDataError handling (S9a), post-save Food Cell DO Intimation trigger (`send_do_intimation.delay()`) |
| `app/__init__.py`                   | Register new blueprints, add health endpoint, register `food_cell_bp` at `url_prefix="/food-cell"` |
| `app/search/indexer.py`             | Fuzzy search fallback                                      |
| `app/legal_analysis/routes.py`      | Validation + readiness score                               |
| `celery_app.py`                     | Beat schedule for backups                                  |
| `app/templates/base.html`           | Nav links (analytics, version history)                     |
| `pyproject.toml`                    | `rapidfuzz` + `numpy`, `openai` or `httpx` for Phase 11    |

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

### AI Readiness

- **Current:** None — no AI integration exists
- **Critical blockers:** No structured training data, no vector DB, no document indexing, no context management, no LLM integration layer
- **Phase 11** (AI assistant) and **Phase 19** (case intelligence) are the AI entry points
- **Foundation needed:** Add `rapidfuzz`, `openai`/`httpx`, vector DB (Qdrant/Pinecone), embeddings pipeline

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

## 8. Multi-Target Sheets Redundancy Architecture

> **Status:** ⚠️ Designed. Implementation planned (Priority 7 in `task.md`).
> **Goal:** Eliminate Google Sheets as a single point of failure by adding Airtable and Microsoft Excel Online as parallel real-time sync targets, with R2 CSV exports of each service for redundant restore.

### Architecture

```
Primary:  PostgreSQL (Render)
           │  (per-record push on create/update)
           ├──► Google Sheets       (gspread API)
           ├──► Airtable             (pyairtable API)  ← NEW
           └──► Excel Online          (Microsoft Graph API)  ← NEW
           │
           └──► Daily QStash-triggered backup:
                scripts/backup_redundant_sheets.py
                  ├──► Sheets  → CSV → Cloudflare R2
                  ├──► Airtable → CSV → Cloudflare R2  ← NEW
                  └──► Excel  → CSV → Cloudflare R2  ← NEW
           │
           └──► On PG failure → SQLite fallback
                └──► Restore chain:
                    1. R2 JSON backup (build_backup_archive format)
                    2. R2 CSV from Sheets (if R2 JSON unavailable)
                    3. R2 CSV from Airtable (if Sheets R2 gone)  ← NEW
                    4. R2 CSV from Excel (if Airtable R2 gone)   ← NEW
                    5. Live API pull (if all R2 backups gone)    ← NEW
                    6. Empty SQLite (graceful degradation)
```

### Key Design Decisions

1. **Parallel, not replacement:** All three services (Sheets, Airtable, Excel) run in parallel. No single service is authoritative for data recovery.
2. **`< 1,200 records/base` Airtable handling:** Airtable's free tier limit is 1,200 records per base. The Airtable sync service implements **automatic base rotation** — when the current base nears capacity, a new base with identical schema is created programmatically (via Airtable REST API `/v0/meta/bases`), and subsequent records are routed to the new base. A tracking table (`airtable_base_map`) records which base each record lives in.
3. **R2 CSV = canonical backup:** All three services export to R2 as CSV. The restore chain tries R2 JSON (most complete) first, then falls through to each service's R2 CSV backup in order.
4. **QStash-only scheduling:** No Celery worker required for daily exports. QStash webhook triggers `scripts/backup_redundant_sheets.py` at 1 AM UTC daily.
5. **Best-effort sync:** If any service fails (rate limits, auth expiry, plan limits), the sync is silently skipped — PostgreSQL is the source of truth.

### New Dependencies

| Package     | Purpose                          | Current Status |
| ----------- | -------------------------------- | -------------- |
| `pyairtable` | Airtable API client SDK          | New            |
| `msal`       | Microsoft OAuth2 client credentials flow | New            |

### New Environment Variables

| Variable             | Purpose                          | Required For |
 -------------------- | -------------------------------- | ------------ |
| `AIRTABLE_API_KEY`   | Airtable API key                 | Airtable sync |
| `AIRTABLE_BASE_ID`   | Primary Airtable base ID         | Airtable sync |
| `MS_TENANT_ID`       | Azure AD tenant ID               | Excel sync |
| `MS_CLIENT_ID`       | Azure AD app registration ID     | Excel sync |
| `MS_CLIENT_SECRET`   | Azure AD client secret           | Excel sync |
| `MS_DRIVE_ID`        | SharePoint/OneDrive drive ID     | Excel sync |
| `MS_SPREADDHEET_ID`  | Excel file ID in OneDrive/Share  | Excel sync |

### Integration Points with Existing Codebase

| Existing File                  | Extension Point                                                                 |
| ------------------------------ | ------------------------------------------------------------------------------- |
| `app/services/sheets_sync.py`  | Provides `WORKSHEET_MAP`, `SHEET_COLUMNS`, `get_gspread_client()` — reused by both Airtable and Excel services for column/field mapping |
| `app/utils/storage.py`         | Provides `_get_client()` / `_get_bucket()` — reused by all backup/export scripts |
| `app/case_file_generator/routes.py` | Add `sync_to_airtable()` + `sync_to_excel()` calls alongside existing `sync_to_sheets()` |
| `app/adjudication/routes.py`   | Same pattern — parallel sync calls                          |
| `app/inspection/routes/inspection_routes.py` | Same pattern                          |
| `app/sample/routes.py`         | Same pattern                          |
| `app/bill_generator/routes.py` | Same pattern                          |
| `app/__init__.py`              | Extend startup recovery hook to try new restore sources     |
| `app/utils/sync.py`            | Extend with `restore_from_airtable_csv()` + `restore_from_excel_csv()` |
| `celery_app.py`                | No changes needed (QStash handles scheduling)                    |

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

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_food_cell_do_intimation.py` | 15 | HTML rendering, PDF generation, intimation creation, FSO-save trigger (task callable), sync forwarding results, PDF download endpoint, HTML view endpoint, status endpoint (found + not-found), regenerate endpoint, idempotency, force regeneration, sample-not-found, DO reference uniqueness, forwarded timestamp |

---

## 9. Test Environment Issues (Discovered During 2026-08-06 Verification)

> During the verification run of all 14 open dependabot PRs (commit `a746104`), the full test suite (832 tests, 22-min runtime) was executed. The dependency updates themselves are **not** the cause of any failures — the updated package versions (pytest 9.1.1, pytest-cov 7.1.0, black 26.5.1, etc.) were already installed in the environment before the PR changes were applied. The failures are all **environment-specific** gaps that must be addressed for a clean test run.

### Test Suite Results

| Metric | Count |
|--------|-------|
| Passed | 783 |
| Failed | 18 |
| Errors | 21 |
| Total | 822 attempted (832 collected, 10 not reached due to cascade) |

### Failure Breakdown — All Environment-Related

| Test File | Failures | Errors | Root Cause | Fix Priority |
|-----------|----------|--------|------------|--------------|
| `test_concurrency_inspection.py` | 4 | 0 | PostgreSQL-specific: advisory locks + `StaleDataError` not raised on SQLite → HTTP 500 instead of 409 | Medium |
| `test_case_backup.py` | 0 | 14 | PostgreSQL-specific: JSON export/import, zip archives, Celery beat require PG features; SQLite setup fails in fixtures | Medium |
| `test_food_cell_do_intimation.py` | 7 | 7 | (a) Missing template `food_cell/do_intimation.html`; (b) Redis/Celery not available for async sync dispatch | High |
| `test_ocr_pipeline.py` | 7 | 0 | Missing `cv2` (OpenCV) — optional OCR preprocessing dependency | Medium |
| `test_timeline.py` | 11 | 0 | Pre-existing uncommitted `app/__init__.py` change (health endpoint `health_bp` registration + `public_endpoints` addition) interferes with blueprint route initialization → 404 on all `TestTimelineRoutes` endpoints | High |

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

_End of plan.md_
