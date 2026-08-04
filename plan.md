# Implementation Plan — NSA Webservice Roadmap (Phases 0–20)

> **Generated:** 2026-08-04  
> **Source:** Consolidated from `ROADMAP_ALIGNMENT_REPORT.md`, `IMPLEMENTATION_PLAN.md`, `ENGINEERING_ASSESSMENT.md`, and `technical_debt_implementation_plan.md`  
> **Status:** Phases 0–9 ✅ Complete. Phases 10–20 pending.

---

## 1. Phase Status Overview

### ✅ Completed (Phases 0–9)

| Phase | Feature                                                                                         | Status | Key Files                                                        |
| ----- | ----------------------------------------------------------------------------------------------- | ------ | ---------------------------------------------------------------- |
| **0** | Architecture (keep Flask) + JS linting (ESLint+Prettier)                                        | ✅     | `package.json`, `eslint.config.js`, `.github/workflows/lint.yml` |
| **1** | Core petition engine — auto-save, Delta storage, validation error display, Facts/Grounds/Prayer | ✅     | `editor.js`, `document_viewer/routes.py`, `petition.html`        |
| **2** | Rich editor — Quill 2.x, image upload, Markdown export                                          | ✅     | `editor.js`, `markdown_export.py`                                |
| **3** | Local DB — Settings/Annexure/Evidence/Version models, FTS5 search, backup/restore               | ✅     | `app/models/`, `app/search/`, `app/utils/backup.py`              |
| **4** | Annexure management — upload, **replace**, A/B/C letters, metadata, duplicate detection           | ✅     | `app/annexure/`                                                  |
| **5** | Evidence — unified model, drag-drop, compression, thumbnails, search                            | ✅     | `app/evidence/`, `unify_photo_evidence` migration                |
| **6** | Cross-reference engine — extraction/linking/renumbering/enclosures                              | ✅     | `app/cross_reference/`                                           |
| **7** | Dynamic TOC — extraction, numbering, bookmarks, live editor panel                               | ✅     | `app/toc_generator/`                                             |
| **8** | PDF assembly engine — headers/footers, QR, signatures, bookmarks, hyperlinks                    | ✅     | `app/pdf_assembly/`                                              |
| **9** | Version control — compare, restore, branching, history UI                                       | ✅     | `app/version_control/`, `app/services/version_control.py`        |

### ⏳ Pending (Phases 10–20)

| Phase  | Feature                                                                     | Status                              | Gap                                                                       |
| ------ | --------------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------- |
| **10** | Search engine — fuzzy search                                                | ⚠️ FTS5 keyword + filters done      | Fuzzy search (rapidfuzz) not implemented nor declared in `pyproject.toml` |
| **11** | AI assistant — grammar, legal language, summarize, contradictions, drafting | ❌                                  | Only rule-based `suggester.py` exists                                     |
| **12** | Legal rule engine — ValidationEngine (score, warnings, errors, suggestions) | ❌                                  | Only `suggest_sections()` exists                                          |
| **13** | Timeline engine — auto-generated events, Gantt UI                           | ❌                                  | Not started                                                               |
| **14** | Knowledge graph — entity/relationship extraction, traversal API             | ❌                                  | Not started                                                               |
| **15** | Analytics dashboard — aggregate queries, charts, geo map                    | ❌                                  | Only billing + inspection lists exist                                     |
| **16** | Backup & export — JSON export, case import, scheduled backups               | ⚠️ PDF/ZIP + manual backup done     | JSON export, case import, scheduled backups                               |
| **17** | Cloud sync — Supabase bridge, annexure sync, conflict resolution            | ⚠️ R2/B2 + Cloudinary + Sheets done | Supabase bridge, conflict resolution, sync-status UI                      |
| **18** | Multi-user RBAC — Role model, `@role_required`, comments, approval workflow | ❌                                  | Auth only, all users have full access                                     |
| **19** | AI case intelligence — evidence strength, traceability, readiness score     | ❌                                  | Not started                                                               |
| **20** | Plugin architecture — OCR/AI/rule/PDF provider interfaces                   | ❌                                  | OCR and rules hardcoded                                                   |

### New (Extraction → Storage → Autopopulation Pipeline)

| Phase   | Feature                                                                                                                  | Status         |
| ------- | ------------------------------------------------------------------------------------------------------------------------ | -------------- |
| **A–E** | OCR extraction (Vision-LLM + zonal OCR), review/commit workflow, conflict resolution, autopopulation, feedback dashboard | ❌ Not started |

---

## 2. Recommended Implementation Order

| Step | Phase    | Action                                                                                  | Key Files                                        |
| ---- | -------- | --------------------------------------------------------------------------------------- | ------------------------------------------------ |
| 1    | Phase 0  | ✅ Done — keep Flask + JS linting                                                       | (complete)                                       |
| 2    | Phase 1  | ✅ Done — petition engine                                                               | (complete)                                       |
| 3    | Phase 3  | ✅ Done — models + search + backup                                                      | (complete)                                       |
| 4    | Phase 6  | ✅ Done — cross-reference engine                                                        | (complete)                                       |
| 5    | Phase 7  | ✅ Done — TOC generator                                                                 | (complete)                                       |
| 6    | Phase 8  | ✅ Done — PDF assembly                                                                  | (complete)                                       |
| 7    | Phase 9  | ✅ Done — version control                                                               | (complete)                                       |
| 8    | Phase 10 | Add `rapidfuzz` to `pyproject.toml`; fuzzy fallback in `search/indexer.py`              | `pyproject.toml`, `app/search/indexer.py`        |
| 9    | Phase 12 | Create `app/validation/` (engine, rules, routes)                                        | new blueprint                                    |
| 10   | Phase 13 | Create `app/timeline/` (TimelineEvent model, engine, Gantt UI)                          | new blueprint                                    |
| 11   | Phase 15 | Create `app/analytics/` (aggregate queries, charts)                                     | new blueprint                                    |
| 12   | Phase 18 | Add `Role`/`UserRole` models + `@role_required` decorator                               | `app/models/`, `app/decorators.py`               |
| 13   | Phase 16 | JSON export + extend ZIP to include annexures/evidence/versions; Celendar beat schedule | `case_file_generator/routes.py`, `celery_app.py` |
| 14   | Phase 11 | Create `app/ai_assistant/` (LLM service, prompt templates)                              | new blueprint                                    |
| 15   | Phase 19 | Create `app/case_intelligence/` (evidence strength, readiness score)                    | new blueprint                                    |
| 16   | Phase 14 | Create `app/knowledge_graph/` (Entity/Relationship models, traversal API)               | new blueprint                                    |
| 17   | Phase 20 | Create `app/plugins/` (registry, base interfaces)                                       | new blueprint                                    |
| 18   | Phase 17 | Create `app/sync/` (Supabase bridge, conflict resolution)                               | new blueprint                                    |

---

## 3. File-Level Edit Guide

### 3.1 Files Needing New Models

**File:** `app/models/document.py` (was `app/models.py`, now split into `app/models/` package)

- Add: `TimelineEvent`, `Role`, `UserRole`, `Comment`, `Entity`, `Relationship`

### 3.2 New Blueprints to Create

| Blueprint                | Purpose              | New Files                                                            |
| ------------------------ | -------------------- | -------------------------------------------------------------------- |
| `app/validation/`        | Legal rule engine    | `__init__.py`, `engine.py`, `rules.py`                               |
| `app/timeline/`          | Timeline engine      | `__init__.py`, `engine.py`, `routes.py`, `templates/`                |
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
| `app/sample/routes.py`              | StaleDataError handling (S9a)                              |
| `app/billing/routes.py`             | Analytics data endpoints                                   |
| `app/search/indexer.py`             | Fuzzy search fallback                                      |
| `app/legal_analysis/routes.py`      | Validation + readiness score                               |
| `celery_app.py`                     | Beat schedule for backups                                  |
| `app/templates/base.html`           | Nav links (analytics, version history)                     |
| `pyproject.toml`                    | `rapidfuzz`, `openai` or `httpx` for Phase 11              |

### 3.4 New Migrations

```
add_timeline_event_table.py
add_role_user_role_comment_tables.py
add_entity_relationship_tables.py
add_ocr_pipeline_models.py  (OCRDocument, LabTestParameter, OCRCorrection, ConflictLog, FieldAuthority)
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

1. SQLAlchemy connection pooling
2. Cache FSO names (Redis)
3. Enable Jinja2 template caching
4. Add DB indexes (check missing)
5. Fix N+1 with eager loading
6. Flask compression middleware
7. Health check endpoint
8. Move FSO sync to startup script (already done via `sync_fso_from_markdown` in `create_app`)

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

| Package             | Phase           | Reason                                                                           |
| ------------------- | --------------- | -------------------------------------------------------------------------------- |
| `rapidfuzz`         | Phase 10        | Fuzzy search (also fixes undeclared import in `document_cleaner/normalizers.py`) |
| `openai` or `httpx` | Phase 11        | LLM integration                                                                  |
| `redis`             | Phase 1 (infra) | Caching + Celery broker (already in `pyproject.toml`)                            |
| `tenacity`          | Phase A         | Retry logic for Cloudinary and extraction pipeline                               |
| `psycopg2-binary`   | Phase 1 (infra) | PostgreSQL driver (already in `pyproject.toml`)                                  |

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

_End of plan.md_
