# Roadmap Alignment Report — NSA Webservice

> **Generated:** 2026-08-02
> **Last status update:** 2026-08-03 (steps 1–7 of the recommended order complete — Phase 6 done)
> **Purpose:** Evaluate the current NSA Webservice codebase against the 20-phase roadmap and produce a detailed implementation plan with TODO list and file-level edit guidance.

> **📌 Current Project Status Update (2026-08-03):**
> - **Phase 5 (Evidence Management):** ✅ **COMPLETED** — unified `Evidence` model (photo/video/report/licence/bill/lab_report), drag-and-drop multi-file upload, Pillow compression + thumbnails, type/tag categorization, library + FTS5 search, and the legacy `PhotoEvidence`/`InspectionPhoto` tables unified into `evidence` (migration `unify_photo_evidence`). Blueprint registered at `/evidence`, nav link added, 16 tests in tests/test_phase5_evidence.py.
> - **Cloudinary Integration:** ✅ **COMPLETED** for adjudication photos (`InspectionPhoto` → now `Evidence`). Backend implemented in `app/utils/storage.py` with R2/B2 fallback. Environment config added to `.env.example`, `render.yaml`, and `pyproject.toml`.
> - **Evaluation Score:** 4.3/5 (see `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md` for details).
> - **Next Steps:** Phase 7 — Dynamic TOC generator.
> - **Phase 6 (Cross-Reference Engine):** ✅ **COMPLETED** — `app/cross_reference/` engine: paragraph/annexure/section reference extraction (incl. sub-clause refs like `Section 26(2)(ii)` and runs like `Sections 55, 56 and 58`), annexure metadata linking by letter/index, renumbering passes (plain-text list markers, `<ol start>` continuations scoped to `class="justify"` lists, annexure letter reassignment), auto "List of Enclosures", and a defensive `post_process_pdf_html` pass wired into every PDF-assembly path (document_viewer, case_file_generator, adjudication). 27 tests in tests/test_cross_reference.py.

---

## 0. Executive Summary

| Aspect | Current State | Roadmap Target | Alignment |
|--------|--------------|----------------|-----------|
| **Frontend** | Flask + Jinja2 + Vanilla JS (Quill 2.x) + custom CSS | React + TypeScript + Tailwind + Vite | **MISMATCH** |
| **State Management** | Server-side session (Flask-Login) | Zustand (client-side) | **MISMATCH** |
| **Local Storage** | SQLAlchemy + PostgreSQL/SQLite + **Cloudinary (photos)** | Dexie.js (IndexedDB, offline-first) | **PARTIAL** |
| **Routing** | Flask routes | React Router | **MISMATCH** |
| **Build Tooling** | setuptools + GitHub Actions | Vite + ESLint + Prettier | **MISMATCH** |
| **Backend** | Flask (Python 3.12), SQLAlchemy, Celery + QStash, **Cloudinary (R2/B2 fallback)** | Backend as API provider | **PARTIAL** |

The current project is a mature **Flask web application** (server-side rendered) for Food Safety Officer adjudication.

**📌 Recent Progress:**
- **Phase 5 (Evidence Management):** ✅ **Completed 2026-08-03** — unified Evidence model + `/evidence` blueprint (drag-and-drop upload, Pillow compression, thumbnails, type/tag categorization, search) and the legacy PhotoEvidence/InspectionPhoto tables merged into `evidence`.
- **Phase 6 (Cross-Reference Engine):** ✅ **Completed 2026-08-03** — `app/cross_reference/` (reference extraction/linking, paragraph + HTML-list + annexure renumbering, auto enclosures list) wired into every PDF-assembly path.
- **Cloudinary Integration:** ✅ **Fully implemented** for adjudication photos (now the unified `Evidence` model). The backend in `app/utils/storage.py` now supports Cloudinary as an optional storage backend (active when `CLOUDINARY_*` env vars are set), with automatic fallback to R2/B2. No changes required to routes, models, or PDF embedding logic.
- **Evaluation:** See `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md` for a detailed assessment (score: 4.3/5).

The roadmap describes a **new React + TypeScript** frontend. The roadmap's *domain features* are the substantive goals. Two approaches:
- **(A)** Build a new React frontend (`frontend/`) that consumes Flask REST APIs
- **(B)** Adapt roadmap features to the existing Flask/Jinja2 architecture

---

## 1. Current Project Structure

`
c:\github\NSA_webservice\
├── app.py                              # WSGI entry point
├── app/
│   ├── __init__.py                     # App factory, blueprint registration
│   ├── extensions.py                   # db, talisman, csrf, login_manager
│   ├── models.py                       # 15 SQLAlchemy models
│   ├── audit_hooks.py                  # SQLAlchemy after_flush audit logging
│   ├── adjudication/                   # Non-sample adjudication
│   ├── audit/                          # Audit log viewer
│   ├── auth/                           # Login/logout/change-password
│   ├── bill_generator/                 # Bill PDF generation
│   ├── billing/                        # Billing summary + Excel
│   ├── case_file_generator/            # Sample-based petition/PDF generation
│   ├── document_cleaner/               # Legal text cleaning pipeline
│   ├── document_loader/                # Document ingestion (PDF/DOCX/TXT)
│   ├── document_viewer/                # Quill editor viewer/saver
│   ├── fbo_issue/                      # FBO issue state machine
│   ├── inspection/                     # Inspection CRUD + photo + OCR
│   ├── legal_analysis/                 # Legal paragraph detection workbench
│   ├── ocr_pipeline/                   # OCR (PaddleOCR + Tesseract)
│   ├── sample/                         # Sample tracking CRUD
│   ├── services/                       # Services layer
│   ├── settings/                       # Settings dashboard
│   ├── shared/                         # Canonical field keys + context deriviners
│   ├── static/                         # CSS, JS, Quill vendor
│   ├── templates/base.html             # Master layout
│   ├── utils/                          # Lookup, filters, storage, pdf_utils
│   ├── tasks_webhook/                  # QStash webhook + task status
├── legal_paragraph_detection_engine/   # Standalone rule-based legal parser
├── migrations/                         # Alembic migrations (15 files)
├── tests/                              # 14 pytest modules, ~245 test cases
├── scripts/                            # Utility scripts
├── .opencode/plans/                    # AI-assisted planning docs
├── render.yaml                         # Render deployment config
├── celery_app.py                       # Celery integration
├── pyproject.toml                      # Dependencies + tool config
├── requirements.txt / requirements-dev.txt
├── .env.example                        # 15 environment variables
├── ALL_TODO_MERGED.md                  # Security TODO items
├── ENGINEERING_ASSESSMENT.md           # Principal architect assessment
├── PROJECT_EVOLUTION.md                # Comprehensive architecture audit
└── LEGAL_PARAGRAPH_DETECTION_ENGINE.md # Legal text parser spec
`

### 1.1 Registered Flask Blueprints (13)

| Blueprint | Prefix | Purpose |
|-----------|--------|---------|
| uth | /auth | Login, logout, change password |
| case_file_generator | /case_file_generator | Sample-based petition + permission letter |
| djudication | /adjudication | Non-sample adjudication, section suggestions |
| document_viewer | /document_viewer | Quill editor + save-to-PDF |
| ill_generator | /bill_generator | Bill PDF (QStash async) |
| bo_issue | /fbo-issue | FBO issue state machine |
| sample | /sample | Sample tracking CRUD |
| illing | /billing | Billing summary + Excel export |
| settings | /settings | Settings, FSO sync |
| inspection | /inspection | Inspection CRUD, photos, OCR |
| legal_analysis | /legal | Legal paragraph detection workbench |
| udit | /admin | Read-only audit log viewer |
| 	asks_webhook | *(none)* | QStash webhook, task status |

### 1.2 SQLAlchemy Models (15)

| Model | Table | Key Fields |
|-------|-------|-----------|
| CaseFile | case_files | case_number, parties, sample_code, applicable_sections, version_id |
| Adjudication | djudications | case_number, FBO details, checklist, sections, version_id, photos |
| Inspection | inspection | inspection_code, fso_name, compliance_deadline, adjudication_id |
| Sample | sample | sample_code, sample_name, fso_name, collection_date, price |
| Bill | ills | Name, EMP_ID, prices, version_id |
| FboIssue | bo_issue | fbo_id, state, source_type, detail_json, geo coords |
| FboIssueAudit | bo_issue_audit | issue_id, from_state, to_state |
| FSO | so | fso_name (PK) |
| Evidence | evidence | id, evidence_type, filepath, caption, tags, ocr_text, geo, verification_status — unified model (replaces InspectionPhoto + PhotoEvidence in Phase 5) |
| AuditLog | udit_log | entity_type, action, actor, hash chain |
| RecordAudit | 
ecord_audit | action, record_type, changes_json, user_id |
| User | user | username, password_hash |
| CodeSequence | code_sequence | key, last_value |
| AppSecret | pp_secrets | name, value |

### 1.3 Technology Stack

Python 3.12, Flask 2.x, SQLAlchemy, Alembic, WeasyPrint, openpyxl, Celery + Redis, QStash,
PaddleOCR + Tesseract, pdf2image, PIL, numpy, **cloudinary>=1.40.0**, boto3 (R2/B2), gspread, pdfplumber,
PyMuPDF, python-docx, Pydantic v2, ruff, black, mypy, bandit, pytest.
GitHub Actions CI/CD (lint, pip-audit, validation, deploy).

### 1.4 Test Coverage

| Test File | Tests | Covers |
|---|---|---|
| 	est_auth_change_password.py | 9 | Auth: password change |
| 	est_bill_generator.py | 11 | Bill: creation, PDF template vars |
| 	est_document_cleaner.py | 45 | Document: text cleaning pipeline |
| 	est_document_loader.py | 35 | Document: PDF/DOCX/TXT loading |
| 	est_document_viewer.py | 24 | Editor: save, retrieve, PDF, session |
| 	est_legal_suggest.py | 4 | Adjudication: section suggestions |
| 	est_metadata_extractor.py | 31 | Metadata: regex + NER extraction |
| 	est_ocr_pipeline.py | 24 | OCR: decision, preprocessing, engine |
| 	est_pdf_photo_embedding.py | 11 | PDF: photo embedding in WeasyPrint |
| 	est_route_collisions.py | 1 | URL collision regression |
| 	est_step1.py-	est_step5_integration.py | 74 | Step-based integration tests |
| **Total** | **~245** | |

---

## 2. Phase-by-Phase Roadmap Alignment

### Phase 0 — Architecture & Foundation

> **Status (2026-08-02):** Foundation decision **made** — keep Flask + Jinja2 (no new React frontend, recorded in Section 5 step 1). All React-only items are therefore **skipped by decision**; CI already exists. The one non-prescribed improvement is JS linting/formatting for the growing vanilla-JS files.

| Roadmap Item | Current State | Status | Action |
|---|---|---|---|
| React + TypeScript frontend | Flask + Jinja2 + Vanilla JS | ✅ **Decided: keep Flask** | No `frontend/` package; roadmap domain features implemented natively (see Phases 1–10) |
| Tailwind CSS | Custom theme.css (~1852 lines) | ✅ Skipped (by decision) | Custom CSS remains the styling system |
| Zustand | Server-side session (Flask-Login) | ✅ Skipped (N/A for Flask) | No client-side state store needed |
| Dexie.js (IndexedDB) | SQLAlchemy + PostgreSQL/SQLite | ✅ Skipped (N/A for Flask) | Server-side DB remains |
| React Router | Flask routes | ✅ Skipped (N/A for Flask) | Flask blueprints handle routing |
| Vite | setuptools | ✅ Skipped (N/A for Flask) | Python packaging remains |
| ESLint + Prettier | ruff + black + mypy + bandit; **no JS linting** | ⚠️ **Gap (not prescribed)** | Recommend adding ESLint (+ optional Prettier) for `app/static/js/**` (editor.js 447 lines, task_status.js 106 lines) |
| GitHub Actions (CI) | lint, pip-audit, validation, deploy, release, docker-build | ✅ Present | No action needed |

**Phase 0 TODO:**
1. [x] **JS linting (ESLint + Prettier)** for vanilla JS in `app/static/js/**` — not prescribed by the roadmap (it assumed a React path) but warranted now that `editor.js` has grown past 400 lines with upload/export logic. **Implemented 2026-08-02:** `package.json` (ESLint 9 + Prettier 3 dev deps), `eslint.config.js` (flat config, ES2020, browser globals + `Quill`, Prettier-integrated), `.prettierrc` (4-space, double quotes, semicolons), `js-lint` job added to `.github/workflows/lint.yml`. Both `app/static/js/**` files now pass `eslint --max-warnings=0` and `prettier --check`. **Bonus:** ESLint caught a pre-existing syntax error in `editor.js` line 214 (`[{ table: [[], [], false]] }]` — unbalanced brackets broke the whole file's parse in the browser since Phase 1); fixed to `[{ table: [[], [], false] }]` (parse fix only; the vendored Quill 2.0.1 table module exposes no toolbar handler, so table insertion via the toolbar is a separate future item).

**Files created/changed:** `package.json`, `eslint.config.js`, `.prettierrc`, `.github/workflows/lint.yml` (+js-lint job), `.pre-commit-config.yaml` (+js-lint + prettier-format local hooks so JS is checked locally before commits), `app/static/js/document_viewer/editor.js` (syntax fix + auto-format), `app/static/js/task_status.js` (auto-format only).

### Phase 1 — Core Petition Engine (Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Case Model (Petition, Parties, Facts, Grounds, Prayer) | CaseFile model exists | app/models.py:13-90 |
| Petition form (UI) | Jinja2 template with all fields | app/case_file_generator/templates/case_file_generator/index.html |
| Petition template (PDF) | WeasyPrint-rendered HTML | app/case_file_generator/templates/case_file_generator/petition.html |
| Validation | process_form_data() + get_applicable_sections() + **validate_case_file_form()** | app/case_file_generator/routes.py |
| Validation error display | ✅ Structured field errors rendered inline in UI | routes.py (400 + `errors` map), index.html (`renderFieldErrors`), task_status.js (`errors` passthrough), theme.css (`.form-input--error`/`.field-error`) |
| Live preview | Quill editor with live iframe preview | app/static/js/document_viewer/editor.js |
| PDF export | WeasyPrint via QStash async or sync fallback | app/case_file_generator/tasks.py, app/utils/pdf_utils.py |
| Print layout | CSS print styles in theme.css | app/static/css/theme.css |
| Auto-save | ✅ Continuous (debounced text-change) | app/document_viewer/routes.py, editor.js |
| Non-sample adjudication Petition | Adjudication model + templates | app/adjudication/routes.py + templates |
| Permission Letter (both types) | Templates for both types | permission_letter.html, Legal_NonsampleAdjudication_Template.html |

**TODO:**
1. [x] Continuous auto-save (debounce on text-change) in editor.js — **done (commit ee3db9a)**
2. [x] Store Quill Delta alongside HTML for round-trip fidelity — **done (commit ee3db9a)**
3. [x] Add validation error display in UI — **done**: `validate_case_file_form()` returns structured `{field: message}` errors (400) wired into `generate_case_file_route`; `task_status.js` passes `errors` through; `index.html` renders them inline (`renderFieldErrors`) with `.form-input--error` / `.field-error` styling; tests in tests/test_phase1.py
4. [x] Add Facts/Grounds/Prayer structured sections to petition template — **done**: petition.html now has explicit **STATEMENT OF FACTS**, **GROUNDS** (analysis result, s.26(2)(ii) contravention, no appeal u/s 46(4), DO authorization), and **PRAYER** (numbered prayer clauses + closing line) sections

**Files changed (Phase 1 completion):** app/case_file_generator/routes.py (validation), app/case_file_generator/templates/case_file_generator/petition.html (GROUNDS/PRAYER sections), app/case_file_generator/templates/case_file_generator/index.html (inline errors), app/static/js/task_status.js (errors passthrough), app/static/css/theme.css (error styles), tests/test_phase1.py

### Phase 2 — Rich Editor (Implemented - Quill 2.x)

| Feature | Status | File(s) |
|---|---|---|
| Rich text | Quill 2.x with Snow theme | app/static/vendor/quill/ |
| Tables | Quill table module | editor.js |
| Lists | Bulleted/numbered list toolbar | editor.js |
| Images | ✅ Image toolbar button + server upload handler | editor.js, document_viewer/routes.py |
| Hyperlinks | Toolbar includes link module | editor.js |
| Custom legal blocks | Via Jinja2 template variables | Templates |
| Track formatting | Quill Delta stored alongside HTML (.delta files) | editor.js |
| Store as HTML | HTML stored in instance/saved/ | document_viewer/routes.py |
| Store as Quill Delta | ✅ Stored alongside HTML | document_viewer/routes.py |
| Store as Markdown | ✅ Export endpoint + Delta→Markdown converter | markdown_export.py, routes.py |

**TODO:**
1. [x] Wire up image upload handler in Quill editor — **done (Phase 2)**
2. [x] Store Quill Delta — **done (commit ee3db9a)**
3. [x] Add Markdown export option — **done (Phase 2)**

**Files to edit:** app/static/js/document_viewer/editor.js, app/document_viewer/routes.py, app/document_viewer/markdown_export.py — ✅ complete; 27 tests in tests/test_document_viewer_phase2.py

### Phase 3 — Local Database (Implemented)

| Roadmap Table | Current Equivalent | Gap | Action Needed |
|---|---|---|---|
| Cases | case_files table | Exists | No action |
| Petitions | case_files (template render) | HTML not stored as field | Consider persisting |
| Templates | Jinja2 .html files | Exist | OK |
| Settings | ✅ Settings model exists | app/models.py:444 | No action |
| Annexures | ✅ Annexure model exists | app/models.py:491 | Phase 4: upload + metadata |
| Evidence | ✅ General Evidence model exists | app/models.py:542 | Phase 5: blueprint/UI |
| Versions | ✅ Version model exists | app/models.py:620 | Phase 9: compare/restore |
| SearchIndex | ✅ FTS5 virtual table | app/search/ | Phase 10: fuzzy/filters |

**Functions:**

| Function | Status | File(s) |
|---|---|---|
| CRUD | Full CRUD for all models | All route files |
| Search | ✅ SQLite FTS5 search + API | app/search/ | Phase 10: fuzzy/filters |
| Backup | ✅ Full ZIP (JSON DB dump + instance files) | app/utils/backup.py, app/settings/routes.py |
| Restore | ✅ ZIP restore (FK-safe, schema-drift guarded) | app/utils/backup.py, app/settings/routes.py |
| Auto-save | Partial (see Phase 1) | editor.js, document_viewer/routes.py |
| Offline support | Not server-side offline | Would need PWA |

**TODO:**
1. [x] Create Settings model + migration (app-level config) — **done (commit e9e3a0e)**
2. [x] Create Annexure model (UUID, caption, date, hash, page_count, ocr_text, tags) — **done (commit e9e3a0e)**
3. [x] Add Version model for full audit trail + snapshot-on-save — **done (commit e9e3a0e)**
4. [x] Implement SQLite FTS5 search index + search API — **done (commit 00db98e)**
5. [x] Implement backup and restore endpoints — **done (admin-only `/settings/backup` page, `/backup/download`, `/backup/restore`)**
6. [x] Extend PhotoEvidence to general Evidence model — **done (commit e9e3a0e)**

**Files to edit/create:** app/models.py (add Settings, Annexure, Version, Evidence), migrations/versions/, app/search/ (new blueprint) — ✅ all in place; backup/restore added in app/utils/backup.py + app/settings/routes.py; 10 tests in tests/test_phase3_backup.py

### Phase 4 — Annexure Management (Blueprint Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Upload (PDF, JPG, PNG, DOCX) | ✅ /annexure/upload (multipart, 20 MB) | app/annexure/routes.py |
| Generate Annexure A/B/C | ✅ Auto letter assignment (A, B, C, ... per case) | app/annexure/routes.py::_next_annexure_letter |
| Metadata (UUID, Caption, Date, Hash, Page count, OCR text, Tags) | ✅ SHA-256 hash + page count + OCR/text + size + MIME | app/annexure/metadata.py |
| Preview | Download endpoint serves original file | app/annexure/routes.py::download |
| Rename | ✅ POST /annexure/<id>/rename | app/annexure/routes.py |
| Delete | ✅ POST /annexure/<id>/delete | app/annexure/routes.py |
| Reorder | ✅ POST /annexure/<id>/reorder (letter A-Z) | app/annexure/routes.py |
| Replace | Not implemented | Phase 4 remaining |
| Duplicate detection | ✅ By SHA-256 content hash (409 on duplicate) | app/annexure/routes.py::upload |

**TODO:**
1. [x] Create Annexure model (uuid, caption, date, hash, page_count, ocr_text, tags, case_id FK) — **done (commit e9e3a0e)**
2. [x] Add upload endpoint supporting PDF/JPG/PNG/DOCX — **done (annexure blueprint)**
3. [x] Implement annexure generation (A/B/C naming) — **done (auto letter assignment)**
4. [x] Add metadata extraction (hash, page count, OCR text) — **done (app/annexure/metadata.py)**
5. [x] Add frontend UI for annexure list/preview/rename/delete/reorder — **done (annexure/index.html)**
6. [x] Implement duplicate detection by hash — **done (409 on duplicate)**

**Files to edit/create:** app/models.py (add Annexure), app/annexure/ (new blueprint), app/document_loader/ (extend for metadata), migrations/ — ✅ all in place; 14 tests in tests/test_annexure.py

### Phase 5 — Evidence Management (Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Evidence Types (Photos, Videos, Reports, Licences, Bills, Lab reports) | ✅ All 6 types on unified `Evidence` model | app/models.py:503-577 (EVIDENCE_TYPES) |
| Drag & drop upload | ✅ Multi-file drag-and-drop with queue + per-file status | app/static/js/evidence_uploader.js, app/evidence/templates/evidence/index.html |
| Compression | ✅ Pillow lossy compression + downscale (>2560px → optimized JPEG) | app/evidence/media.py::compress_image |
| OCR | ✅ PaddleOCR + Tesseract pipeline + text extraction at upload (PDF/DOCX/image) | app/ocr_pipeline/, app/inspection/tasks.py, app/annexure/metadata.py |
| Metadata extraction | ✅ SHA-256 hash, size, MIME, OCR text, geo coords, verification, captured_at | app/models.py:503-577, app/evidence/routes.py |
| Categorization | ✅ Evidence type + free-form tags + tag cloud filters | app/evidence/routes.py::index, templates |
| Search | ✅ In-library keyword filter + global FTS5/LIKE search index | app/evidence/routes.py::index, app/search/indexer.py (ENTITY_EVIDENCE) |
| Thumbnail generation | ✅ JPEG thumbnails (320px) generated on upload / lazily served | app/evidence/media.py::generate_thumbnail |
| **Cloud Storage** | ✅ **Cloudinary for adjudication photos** (R2/B2 fallback); evidence blueprint stores locally | app/utils/storage.py (lines 162–269) |

**TODO:**
1. [x] Extend Evidence model to support all types (video, report, licence, bill, lab report) — **done** (EVIDENCE_TYPES tuple)
2. [x] Add drag-and-drop upload UI — **done** (evidence_uploader.js + index.html)
3. [x] Implement image compression (Pillow) — **done** (app/evidence/media.py::compress_image)
4. [x] Add thumbnail generation for images — **done** (app/evidence/media.py::generate_thumbnail)
5. [x] Implement evidence categorization + search — **done** (type/tag filters + tag cloud + FTS5/LIKE search)
6. [x] Unify InspectionPhoto and PhotoEvidence into single Evidence model — **done** (migration `unify_photo_evidence`, legacy endpoints read/write Evidence)

**Files created/changed:** app/evidence/ (blueprint: `__init__.py`, `routes.py`, `media.py`, `templates/evidence/index.html`), app/static/js/evidence_uploader.js, migrations/versions/unify_photo_evidence.py, app/models.py (Evidence unified model), app/inspection/routes.py + app/adjudication/routes.py + app/document_viewer/renderer.py (Evidence queries), app/templates/base.html (nav link), app/static/css/theme.css (evidence styles), tests/test_phase5_evidence.py (16 tests).

### Phase 6 — Automatic Cross-Reference Engine (Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Auto-generate paragraph to annexure/section references | ✅ `CrossReferenceEngine.extract_references()` — paragraph word refs (`para 3`), numbered list markers, Annexure refs (letters + numbers, incl. `Annexure-C`, `Annexure No. B`), Section refs (runs `Sections 55, 56 and 58`, sub-clauses `Section 26(2)(ii)`, `u/s 63`) | app/cross_reference/engine.py |
| Link references to annexure metadata | ✅ `link_references()` — resolves Annexure refs by letter or 1-based index against stored metadata (caption, page_count, filename); flags unresolved refs; marks known FSS sections | app/cross_reference/engine.py |
| Automatic paragraph/annexure/page numbering updates | ✅ Renumbering passes: `renumber_paragraphs()` (text list markers), `renumber_html_lists()` (`<ol start>` continuations, scoped to `class="justify"` lists), `renumber_annexures()` (A/B/C letter reassignment) | app/cross_reference/engine.py |
| Enclosures list | ✅ `build_enclosures_html()` auto-generates "List of Enclosures" from stored annexures; injected via an `<ol data-cross-reference="enclosures">` placeholder | app/cross_reference/engine.py |
| PDF-assembly integration | ✅ Defensive `post_process_pdf_html()` / `renumber_html_lists()` in pdf_utils, wired into document_viewer, case_file_generator, and adjudication PDF paths | app/utils/pdf_utils.py, renderer.py, tasks.py, adjudication/routes.py |

**TODO:**
1. [x] Build CrossReferenceEngine that parses text for patterns (paragraph numbers, Annexure refs, Section refs) — **done**
2. [x] Link references to annexure metadata (page refs, section refs) — **done** (`link_references`)
3. [x] Implement renumbering on insert/delete — **done** (text + HTML list + annexure letter passes)
4. [x] Integrate into PDF assembly pipeline — **done** (defensive `post_process_pdf_html` in all PDF paths)

**Files created/changed:** app/cross_reference/__init__.py, app/cross_reference/engine.py, app/utils/pdf_utils.py (`post_process_pdf_html`, `renumber_html_lists`), app/document_viewer/renderer.py, app/case_file_generator/tasks.py, app/adjudication/routes.py, tests/test_cross_reference.py (27 tests).

### Phase 7 — Dynamic TOC (Not Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Detect headings/subheadings | Not implemented | Could use legal_paragraph_detection_engine |
| Detect annexures/appendices | Not implemented | New feature |
| Generate TOC, bookmarks, hyperlinks | Not implemented | PDF post-processing |
| Live update after edit | Not implemented | New feature |

**TODO:**
1. [ ] Build TOC generator that parses HTML for heading tags and annexure markers
2. [ ] Generate hyperlinked TOC
3. [ ] Inject PDF bookmarks during WeasyPrint rendering
4. [ ] Hook into editor for live updates

**Files to edit/create:** app/toc_generator/__init__.py, app/toc_generator/generator.py, app/document_viewer/templates/document_viewer/editor.html (add TOC panel)

### Phase 8 — PDF Assembly Engine (Partially Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Petition + Permission Letter only | Petition + Permission Letter only | app/case_file_generator/tasks.py |
| Bookmarks | Not implemented | WeasyPrint CSS @page |
| Hyperlinks | Not implemented | |
| Headers | Partial - CSS @page possible | theme.css / templates |
| Footers | Not implemented | |
| Page numbers | Not implemented | |
| QR code | Not implemented | Would need qrcode library |
| Signature placeholders | Not implemented | |

**TODO:**
1. [ ] Extend PDF assembly to include annexures, evidence, and index pages
2. [ ] Add WeasyPrint CSS for headers/footers/page numbers (@page rules)
3. [ ] Add QR code generation (qrcode package)
4. [ ] Add signature placeholders in templates
5. [ ] Add PDF bookmarks via PDFAssemblyEngine

**Files to edit/create:** app/pdf_assembly/__init__.py, app/pdf_assembly/assembler.py, app/case_file_generator/templates/..., pyproject.toml (add qrcode)

### Phase 9 — Version Control (Partially Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Version chain (v1 -> v2 -> v3) | version_id optimistic locking only | app/models.py |
| Compare | Not implemented | Need diff view |
| Restore | Not implemented | |
| Branch drafts | Not implemented | |
| Audit history | Partial - AuditLog hash chain + RecordAudit | app/models.py, app/audit_hooks.py |

**TODO:**
1. [ ] Create Version model (version_number, content_hash, created_at, user_id, change_summary)
2. [ ] Add snapshot-on-save triggers (store HTML/deltas)
3. [ ] Implement compare view (diff of two versions)
4. [ ] Implement restore endpoint
5. [ ] Add branch draft support
6. [ ] Expose audit history in UI

**Files to edit/create:** app/models.py (add Version), app/audit/routes.py (extend), migrations/, app/static/js/version_history.js

### Phase 10 — Search Engine (Not Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Index Petition, OCR text, Evidence, Annexures, Case metadata | ✅ FTS5 index (case_file, adjudication, annexure, evidence) | app/search/indexer.py |
| Keyword search | ✅ FTS5 MATCH + bm25 | app/search/indexer.py |
| Filters | ✅ Entity-type filter | app/search/routes.py |
| Fuzzy search | Not implemented | | Phase 10 remaining |
| Full-text search | ✅ FTS5 with LIKE fallback (PostgreSQL) | app/search/indexer.py |

**TODO:**
1. [x] Create search index table (SQLite FTS5) — **done (commit 00db98e)**
2. [x] Index: case metadata, adjudication, annexure OCR text, evidence OCR text — **done (commit 00db98e)**
3. [x] Build search API endpoint — **done (commit 00db98e)**
4. [ ] Implement fuzzy matching (rapidfuzz)
5. [x] Add search UI — **done (commit 00db98e)**

**Files to edit/create:** app/search/__init__.py, app/search/routes.py, app/search/indexer.py, migrations/, pyproject.toml (add rapidfuzz)

### Phase 11 — AI Assistant (Partially Implemented - Rule-Based)

| Capability | Status | File(s) |
|---|---|---|
| Grammar correction | Not implemented | |
| Legal language improvement | Not implemented | |
| Summarize evidence | Not implemented | |
| Detect contradictions | Not implemented | |
| Identify missing annexures | Not implemented | |
| Suggest legal provisions | Rule-based via suggest_sections() | app/utils/suggester.py |
| Draft prayers | Not implemented | |
| Draft facts | Not implemented | |
| Draft grounds | Not implemented | |

**Note:** The `legal_paragraph_detection_engine` provides paragraph-level legal citation extraction — a foundation for AI features. Service layer: `app/services/legal_engine.py`.

**TODO:**
1. [ ] Create AIAssistant service layer
2. [ ] Integrate LLM API (OpenRouter/OpenAI) for grammar, legal language, summarization, contradiction detection
3. [ ] Add "identify missing annexures" logic
4. [ ] Add "draft prayers/facts/grounds" via LLM prompt templates
5. [ ] Wire AI features into document editor UI

**Files to edit/create:** app/ai_assistant/__init__.py, app/ai_assistant/service.py, app/ai_assistant/routes.py, app/static/js/ai_assistant.js, pyproject.toml (add openai or httpx)

### Phase 12 — Legal Rule Engine (Partially Implemented)

| Validation | Status | File(s) |
|---|---|---|
| Mandatory sections | Rule-based via suggest_sections() | app/utils/suggester.py |
| Missing signatures | Not implemented | |
| Numbering | Not implemented | |
| Statutory references | Partial - section detection in legal_paragraph_detection_engine | legal_paragraph_detection_engine/ |
| Duplicate evidence | Not implemented | |
| Timeline consistency | Not implemented | |
| Document completeness | Not implemented | |

**Output formats:**
| Output | Status |
|---|---|
| Validation Score | Not implemented |
| Warnings | Not implemented |
| Errors | Partial via ValidationError JSON |
| Suggestions | Section suggestions via suggester |

**TODO:**
1. [ ] Create ValidationEngine that runs all checks
2. [ ] Implement missing-signature detection (scan templates for signature placeholders)
3. [ ] Implement numbering validation (case_number, sample_code, lab_reg_no formats)
4. [ ] Implement statutory reference validation (Sections 55/56/58/63/64 rules)
5. [ ] Implement duplicate evidence detection (hash-based)
6. [ ] Implement timeline consistency validation
7. [ ] Implement document completeness check
8. [ ] Return structured score + warnings + errors + suggestions

**Files to edit/create:** app/validation/__init__.py, app/validation/engine.py, app/validation/rules.py, app/legal_analysis/routes.py (extend)

### Phase 13 — Timeline Engine (Not Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Auto-create complaint to inspection to sampling to dispatch to lab result to notice to reply to petition to order | Not implemented | |
| Link events to supporting documents | Not implemented | Partial via case_id/inspection_id FKs |

**TODO:**
1. [ ] Create TimelineEvent model (event_type, case_id, timestamp, document_ref, description)
2. [ ] Implement timeline auto-generation from case data (derive events from dates)
3. [ ] Add timeline UI (Gantt-style)
4. [ ] Link events to supporting documents

**Files to edit/create:** app/models.py (add TimelineEvent), app/timeline/__init__.py, app/timeline/engine.py, app/timeline/routes.py, migrations/

### Phase 14 — Knowledge Graph (Not Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Store relationships (Complaint to Inspection to Observation to Evidence to Sample to Lab Report to Violation to Ground to Prayer) | Not implemented | |
| Trace assertions to evidence | Not implemented | |

**Note:** README mentions Neo4j (Level 8, planned). The legal_paragraph_detection_engine provides entity relationship extraction foundation.

**TODO:**
1. [ ] Create Entity and Relationship models (or use Neo4j)
2. [ ] Extract entities from case data
3. [ ] Build relationship mappings
4. [ ] Provide graph traversal API

**Files to edit/create:** app/knowledge_graph/__init__.py, app/knowledge_graph/models.py, app/knowledge_graph/engine.py, app/models.py (add Entity/Relationship)

### Phase 15 — Analytics Dashboard (Partially Implemented)

| Metric | Status | File(s) |
|---|---|---|
| Pending cases | Partial - derivable from DB | app/billing/routes.py |
| Disposed cases | Not implemented | |
| Inspections | List view with filters | app/inspection/routes.py |
| Sample status | Sample list | app/sample/routes.py |
| Evidence count | Partial - per-case photo count | app/adjudication/routes.py |
| Legal provisions used | Section tracking via applicable_sections | app/models.py, app/shared/case_keys.py |
| Violation trends | Partial - checklist data exists | app/adjudication/routes.py |
| Geographic distribution | Partial - geo coords in PhotoEvidence | app/models.py |

**Current analytics-like features:**
- Billing summary with filters: app/billing/routes.py, app/billing/billing_utils.py
- Inspection list with sorting/filtering: app/inspection/routes.py
- FBO issue state tracking: app/fbo_issue/routes.py

**TODO:**
1. [ ] Create analytics dashboard route + template
2. [ ] Implement aggregate queries (pending/disposed cases, inspection counts)
3. [ ] Add chart.js or similar for visualization
4. [ ] Add geographic map (Leaflet + geo coords)
5. [ ] Add violation trend analysis

**Files to edit/create:** app/analytics/__init__.py, app/analytics/routes.py, migrations/

### Phase 16 — Backup & Export (Partially Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Export PDF | Case file PDF (petition + permission letter) | app/case_file_generator/tasks.py |
| Export JSON | Not implemented | |
| Export ZIP | Case file ZIP (petition + permission letter) | app/case_file_generator/tasks.py |
| Import complete case | Not implemented | |
| Scheduled backups | Partial - Render automated DB backups | render.yaml |

**TODO:**
1. [ ] Add JSON export endpoint for full case data
2. [ ] Extend ZIP export to include annexures, evidence, versions
3. [ ] Implement case import (JSON to DB restore)
4. [ ] Add scheduled backup configuration (Celery beat)
5. [ ] Add backup download UI

**Files to edit/create:** Extend app/case_file_generator/routes.py, app/backup/ (new), celery_app.py (beat schedule)

### Phase 17 — Cloud Synchronization (Partially Implemented via Google Sheets)

| Feature | Status | File(s) |
|---|---|---|
| Backend (Supabase/R2/Cloudinary) | R2/B2 + **Cloudinary** for photo storage; Google Sheets for data | app/utils/storage.py, app/services/sheets_sync.py |
| Sync cases | Partial - Google Sheets sync for case data | app/services/sheets_sync.py |
| Sync annexures | Not implemented | |
| Sync images | Photos uploaded to R2/**Cloudinary** | app/utils/storage.py |
| Sync evidence | Partial - photos only | app/inspection/routes.py |
| Sync versions | Not implemented | |

**TODO:**
1. [ ] Build Supabase sync bridge (export DB to Supabase PostgreSQL)
2. [ ] Sync annexures to cloud storage
3. [ ] Add conflict resolution logic (version_id check)
4. [ ] Add sync status UI

**Files to edit/create:** app/sync/__init__.py, app/sync/supabase_sync.py, app/sync/routes.py

### Phase 18 — Multi-user Workflow (Auth Only)

All authenticated users have equal access. No role differentiation.

**TODO:**
1. [ ] Add Role model + user_roles association table
2. [ ] Implement RBAC decorator (@role_required)
3. [ ] Add review/comments system (Comment model)
4. [ ] Add approval workflow (status field on CaseFile/Adjudication)
5. [ ] Add user management UI (admin only)

**Files to edit/create:** app/models.py (add Role, UserRole, Comment), app/decorators.py, migrations/

### Phase 19 — AI Case Intelligence (Not Implemented)

**TODO:**
1. [ ] Create CaseIntelligence engine running all checks
2. [ ] Implement evidence strength analysis (OCR quality, photo verification)
3. [ ] Implement allegation-to-evidence traceability
4. [ ] Implement date conflict detection
5. [ ] Compute composite readiness score

**Files to edit/create:** app/case_intelligence/__init__.py, app/case_intelligence/engine.py, app/legal_analysis/routes.py

### Phase 20 — Plugin Architecture (Not Implemented)

OCR providers (PaddleOCR + Tesseract) are hardcoded. Rule packs are hardcoded in suggester.py.

**TODO:**
1. [ ] Create plugin registry system (app/plugins/)
2. [ ] Abstract OCR engine as plugin interface
3. [ ] Abstract AI model as plugin interface
4. [ ] Abstract rule packs as plugin interface
5. [ ] Abstract PDF engine as plugin interface

**Files to edit/create:** app/plugins/__init__.py, app/plugins/registry.py, app/plugins/base.py, refactor app/ocr_pipeline/ocr_engine.py

---

## 3. Consolidated TODO List

### Priority 1 — Foundational (Phase 1-3 completion)
1. [x] **Continuous auto-save** in Quill editor (editor.js) — **done (ee3db9a)**
2. [x] **Settings table** model + migration — **done (e9e3a0e)**
3. [x] **Annexure model** with metadata — **done (e9e3a0e)**
4. [x] **Evidence model** extended to all types — **done (e9e3a0e)**
5. [x] **SQLite FTS5 search** index + API — **done (00db98e)**
6. [ ] **Version history table** + compare/restore (Version model exists; compare/restore pending)
7. [ ] **JSON export** endpoint

### Priority 2 — Core Features (Phase 6-12)
8. [x] **Cross-reference engine** — **done** (`app/cross_reference/` + PDF pipeline integration)
9. [ ] **Dynamic TOC generator**
10. [ ] **PDF assembly** (headers/footers/page numbers)
11. [ ] **QR code** generation
12. [ ] **Signature placeholders**
13. [ ] **Validation engine**
14. [ ] **Timeline engine**

### Priority 3 — Intelligence & Analytics (Phase 13-19)
16. [ ] **Knowledge graph**
17. [ ] **Analytics dashboard**
18. [ ] **AI assistant service** (LLM integration)
19. [ ] **Case intelligence engine** (readiness score)
20. [ ] **Multi-user RBAC** (Role model, @role_required)
21. [ ] **Cloud sync** (Supabase)
22. [x] **Backup & restore** (manual ZIP backup/restore done in Phase 3; automated scheduled export still open)
23. [ ] **Plugin architecture**


## 4. File-Level Edit Guide

### 4.1 Files Needing New Models
**File:** `app/models.py`
- Add: `Settings`, `Annexure`, `Evidence`, `Version`, `TimelineEvent`, `Role`, `UserRole`, `Comment`, `Entity`, `Relationship`, `SavedDocument`

### 4.2 Files Needing New Blueprints
| Blueprint | Purpose | New Files |
|---|---|---|
| app/annexure/ | Annexure management | __init__.py, routes.py, templates/ |
| app/evidence/ | Evidence management | __init__.py, routes.py, templates/ |
| app/search/ | Full-text search | __init__.py, routes.py, indexer.py |
| app/cross_reference/ | Auto cross-reference | __init__.py, engine.py |
| app/toc_generator/ | Dynamic TOC | __init__.py, generator.py |
| app/pdf_assembly/ | PDF assembly | __init__.py, assembler.py |
| app/validation/ | Legal rule engine | __init__.py, engine.py, rules.py |
| app/timeline/ | Timeline engine | __init__.py, engine.py, routes.py |
| app/knowledge_graph/ | Knowledge graph | __init__.py, models.py, engine.py |
| app/analytics/ | Analytics dashboard | __init__.py, routes.py, templates/ |
| app/settings/ | Backup & restore (Phase 3, admin-only) | app/utils/backup.py, app/settings/routes.py |
| app/sync/ | Cloud sync | __init__.py, supabase_sync.py |
| app/ai_assistant/ | AI assistant | __init__.py, service.py, routes.py |
| app/case_intelligence/ | AI intelligence | __init__.py, engine.py |
| app/plugins/ | Plugin architecture | __init__.py, registry.py, base.py |

### 4.3 Existing Routes to Extend
| File | Extension Needed |
|---|---|
| app/case_file_generator/routes.py | JSON export, validation |
| app/adjudication/routes.py | Validation, comments, approvals |
| app/inspection/routes.py | StaleDataError handling (S9a) |
| app/sample/routes.py | StaleDataError handling (S9a) |
| app/billing/routes.py | Analytics data endpoints |
| app/settings/routes.py | Settings CRUD UI |
| app/document_viewer/routes.py | Delta storage, auto-save |
| app/legal_analysis/routes.py | Validation + readiness score |
| app/__init__.py | Register blueprints, RBAC |

### 4.4 New Migrations
new_annexure_table.py, new_evidence_table.py, new_settings_table.py,
new_version_history_table.py, new_search_index_table.py,
new_timeline_event_table.py, new_role_userrole_comment_tables.py,
new_entity_relationship_tables.py, new_saved_document_table.py

### 4.5 Frontend Files to Modify
| File | Modification |
|---|---|
| app/static/js/document_viewer/editor.js | Auto-save, delta, image upload, TOC |
| app/document_viewer/templates/document_viewer/editor.html | TOC, version history, comments |
| app/templates/base.html | Analytics nav link |
| app/static/css/theme.css | Styles for new panels |

### 4.6 Config Files to Modify
pyproject.toml (add qrcode, rapidfuzz, openai), .env.example (add API keys),
render.yaml (beat worker, env vars), celery_app.py (beat schedule)

### 4.7 React Frontend (If Chosen)
Create frontend/ with Vite + React + TS + Tailwind + Zustand + Dexie.js.
Flask backend exposes REST APIs for all features.


## 5. Recommended Implementation Order

| Step | Phase | Action | Key Files |
|---|---|---|---|
| 1 | Phase 0 | ✅ Architecture decision: keep Flask templates (no new React frontend) — **done**; JS linting (ESLint+Prettier) also **done** | package.json, eslint.config.js, lint.yml |
| 2 | Phase 1 | ✅ Core petition engine — auto-save + delta storage + **validation error display** + **Facts/Grounds/Prayer sections** | editor.js, routes.py, petition.html, task_status.js |
| 2b | Phase 2 | ✅ Rich editor completion: image upload + Markdown export | editor.js, markdown_export.py, routes.py |
| 3 | Phase 3 | ✅ Settings + Annexure + Evidence + Version models | models.py, migrations/ |
| 4 | Phase 3 | ✅ SQLite FTS5 search index + API | app/search/ |
| 4b | Phase 3 | ✅ Backup & restore endpoints (admin-only) | app/utils/backup.py, app/settings/routes.py |
| 5 | Phase 4 | ✅ Annexure upload + metadata extraction + letters + duplicate detection | app/annexure/ |
| 6 | Phase 5 | ✅ **Evidence model extended → blueprint/UI done** — unified Evidence model, drag-and-drop upload, compression, thumbnails, categorization, search, PhotoEvidence/InspectionPhoto unification | app/evidence/ |
| 7 | Phase 6 | ✅ Cross-reference engine — **done**: reference extraction/linking, renumbering, enclosures, PDF pipeline integration | app/cross_reference/ |
| 8 | Phase 7 | Dynamic TOC generator | app/toc_generator/ |
| 9 | Phase 8 | PDF assembly (headers/footers/QR) | app/pdf_assembly/ |
| 10 | Phase 9 | Version history + compare/restore | models.py, audit/ |
| 11 | Phase 10 | Search engine (fuzzy, filters) | app/search/ |
| 12 | Phase 11 | AI assistant (LLM integration) | app/ai_assistant/ |
| 13 | Phase 12 | Legal validation engine | app/validation/ |
| 14 | Phase 13 | Timeline engine | app/timeline/ |
| 15 | Phase 14 | Knowledge graph | app/knowledge_graph/ |
| 16 | Phase 15 | Analytics dashboard | app/analytics/ |
| 17 | Phase 16 | Backup & export | app/backup/ |
| 18 | Phase 17 | Cloud sync (Supabase) | app/sync/ |
| 19 | Phase 18 | Multi-user RBAC | models.py, decorators.py |
| 20 | Phase 19 | AI case intelligence | app/case_intelligence/ |
| 21 | Phase 20 | Plugin architecture | app/plugins/ |

## 6. Key Architectural Observations

1. **Cohesive production-ready Flask app** - ~430 tests, CI/CD, security hardening, 15 blueprints. Phase 0 foundation complete (keep-Flask decision + JS linting); **Phase 1 complete** (auto-save, delta storage, validation error display, structured Facts/Grounds/Prayer petition); Phase 2 (rich editor) and **Phase 3 complete** (models + backup/restore endpoints); Phase 4 complete (annexures); **Phase 5 complete** (unified evidence library + PhotoEvidence/InspectionPhoto unification); **Phase 6 complete** (cross-reference engine).

2. **Biggest decision: frontend architecture.** Roadmap calls for React, but current app uses Flask + Jinja2 + Quill. New React frontend means existing templates become redundant.

3. **Quill editor already integrated** - supports rich text, tables, lists, links, images (server upload) + Delta. HTML + Delta stored in `instance/saved/`; Markdown export available. Phase 2 complete.

4. **Domain model well-normalized** - CaseFile (sample-based) vs Adjudication (non-sample) split. `shared/case_keys.py` establishes canonical key contract across 4 UIs.

5. **Async task handling sophisticated** - QStash (webhook delivery) with sync fallback. Works on free-tier without persistent Celery worker.

6. **legal_paragraph_detection_engine** - standalone package with test suite. Foundation for Phase 6, 12, 19.

7. **Security robust** - Talisman (CSP, HSTS), CSRF, Flask-Login, hash-chained AuditLog, optimistic locking. Gaps: S7 (scraper TLS), S2 (CSP enforcement), S6a.

8. **ENGINEERING_ASSESSMENT.md** (2026-07-18) rates architecture 6.5/10. Bottlenecks: SQLite, Sheets sync, in-memory PDF. Partially addressed (PostgreSQL, QStash).

## 7. Cloudinary Integration Status

### Overview
The Cloudinary photo storage backend has been **fully implemented** for adjudication photos (`InspectionPhoto`). This provides an alternative to R2/B2 storage with automatic fallback if Cloudinary is not configured.

### Implementation Details
- **Backend Location:** `app/utils/storage.py` (lines 162–269)
- **Environment Configuration:**
  - `.env.example` (lines 57–65)
  - `render.yaml` (lines 70–75 for web service, 134–139 for worker service)
  - `pyproject.toml` (line 59: `cloudinary>=1.40.0`)
- **Integration Points:**
  - `upload_adjudication_photo` (`app/inspection/routes.py:936`) – Uses `storage.upload_photo`
  - `delete_adjudication_photo` (`app/inspection/routes.py:1004`) – Uses `storage.delete_photo`
  - PDF embedding (`app/utils/pdf_utils.py:56`) – Fetches HTTPS URLs via `requests.get`

### Features
✅ **Automatic Backend Selection:** Cloudinary is used when `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, and `CLOUDINARY_API_SECRET` are all set.
✅ **Fallback to R2/B2:** If Cloudinary env vars are missing or SDK is not installed, falls back to R2/B2.
✅ **Lazy SDK Import:** Cloudinary SDK is imported only when needed, so the module remains importable without it.
✅ **Deterministic Public IDs:** Uploads use `public_id = inspections/<adjudication_id>/<uuid>` for organization.
✅ **Idempotent Deletes:** Cloudinary deletes are idempotent (mirrors R2's NoSuchKey behavior).
✅ **PDF Compatibility:** Cloudinary HTTPS URLs work seamlessly with existing PDF embedding logic.

### Evaluation
- **Score:** 4.3/5 (see `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md` for detailed breakdown)
- **Strengths:** Minimal invasive changes, robust fallback, proper security handling, seamless integration.
- **Gaps:** No unit tests for Cloudinary helpers, no retry logic, no early credential validation.

### Next Steps
1. Add unit tests for Cloudinary helpers (`_extract_cloudinary_public_id`, `_upload_to_cloudinary`, etc.).
2. Add retry logic for Cloudinary operations (using `tenacity`).
3. Add a health endpoint to validate Cloudinary credentials early.
4. Consider supporting `CLOUDINARY_URL` for convenience.

### Out of Scope
- **`PhotoEvidence` Migration:** Inspection photos still use local storage. Migrating to Cloudinary requires refactoring the OCR pipeline to work with HTTP URLs instead of local paths.

---

## 8. Documentation Reference

| File | Purpose |
|---|---|
| PROJECT_EVOLUTION.md | Architecture audit (must-read) |
| ENGINEERING_ASSESSMENT.md | Performance/scaling assessment |
| ALL_TODO_MERGED.md | Security TODO items |
| LEGAL_PARAGRAPH_DETECTION_ENGINE.md | Legal parser spec |
| DOCUMENT_VIEWER_IMPLEMENTATION_PLAN.md | Editor plan |
| POSTGRES_MIGRATION.md | PostgreSQL migration guide |
| CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md | Cloudinary integration plan + evaluation |
| .opencode/plans/PHASE2_MODERNIZATION_STRATEGY.md | Code modernization |
| .opencode/plans/PHASE3_PRODUCTION_REFACTORING.md | Production refactoring |
| **Section 9** | **Extraction → Storage → Autopopulation Pipeline** | This document |

---

## 9. Extraction → Storage → Autopopulation Pipeline Implementation Plan

### 9.1 Overview

This section details the implementation plan for the **extraction → storage → autopopulation** pipeline with **correction feedback** and **conflict resolution** as requested. The pipeline integrates with the existing Flask architecture and leverages current OCR capabilities while adding new document-specific extraction, review workflows, and autopopulation features.

### 9.2 Schema Design

#### New Database Models Required

| Model | Purpose | Key Fields | Relationships |
|-------|---------|------------|---------------|
| **OCRDocument** | Stores OCR-processed documents with extraction results | `id`, `sample_id` (FK), `doc_type` (enum), `image_path`, `raw_extracted_json`, `confidence`, `status`, `reviewed_by`, `reviewed_at` | Belongs to Sample |
| **LabTestParameter** | Child table for VIIA's variable test parameter rows | `id`, `sample_id` (FK), `sl_no`, `parameter`, `method`, `result`, `prescribed_standard` | Belongs to Sample |
| **OCRCorrection** | Tracks manual corrections to OCR-extracted fields | `id`, `ocr_document_id` (FK), `field_name`, `doc_type`, `ocr_value`, `corrected_value`, `corrected_by`, `corrected_at` | Belongs to OCRDocument |
| **FieldAuthority** | Static config table for field priority in conflict resolution | `id`, `field_name`, `doc_type`, `priority` | Standalone |
| **ConflictLog** | Tracks field conflicts between documents | `id`, `sample_id` (FK), `field_name`, `existing_value`, `existing_source_doc_id`, `new_value`, `new_source_doc_id`, `status`, `resolved_value`, `resolved_by`, `resolved_at` | Belongs to Sample |

#### Enhanced Sample Model

The existing `Sample` model will be extended with:
- **New OCR-discovered fields**: `nature_of_food`, `batch_no`, `mfd` (manufacturing date), `exp` (expiry date), `preservative`, `parts_quantity`, `place_of_collection`, `witness_name`, `test_parameter`
- **New status enum**: `collected` → `sent_to_lab` → `analysis_pending` → `result_received` → `conforms`/`non_conforming` → `adjudication_initiated`

### 9.3 Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    EXTRACTION → STORAGE → AUTOPOPULATION PIPELINE        │
├─────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │  Document    │    │  Page       │    │  Vision-LLM          │  │
│  │  Upload     │───▶│  Splitter   │───▶│  Extraction          │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
│                       │                    │                          │
│                       ▼                    ▼                          │
│                  ┌─────────────┐    ┌─────────────────────┐          │
│                  │  Zonal OCR  │◀───│  Fallback (printed   │          │
│                  │  (Tesseract) │    │  forms only)         │          │
│                  └─────────────┘    └─────────────────────┘          │
│                       │                                          │
│                       ▼                                          │
│                  ┌─────────────────────┐                            │
│                  │  Raw Extraction      │                            │
│                  │  Storage            │                            │
│                  └─────────────────────┘                            │
│                       │                                          │
│                       ▼                                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    REVIEW & COMMIT PHASE                      │   │
│  │                                                             │   │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐  │   │
│  │  │  Editable   │    │  Diff       │    │  Conflict       │  │   │
│  │  │  Review    │───▶│  Detection  │───▶│  Resolution     │  │   │
│  │  │  Form      │    │  & OCR     │    │  Queue          │  │   │
│  │  │            │    │  Correction │    │                 │  │   │
│  │  └─────────────┘    └─────────────┘    └─────────────────┘  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                       │                                          │
│                       ▼                                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    AUTOPOPULATION PHASE                       │   │
│  │                                                             │   │
│  │  ┌─────────────┐    ┌─────────────────────────────────┐  │   │
│  │  │  Confirmed  │    │  Field Mapping Dictionary        │  │   │
│  │  │  Sample    │───▶│  (DO Letter, Bill Generator,     │  │   │
│  │  │  Data      │    │   Case File, Adjudication       │  │   │
│  │  │            │    │   Notices)                       │  │   │
│  │  └─────────────┘    └─────────────────────────────────┘  │   │
│  │                       │                                      │   │
│  │                       ▼                                      │   │
│  │                  ┌─────────────┐                          │   │
│  │                  │  Document   │                          │   │
│  │                  │  Generation │                          │   │
│  │                  └─────────────┘                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────┘
```

### 9.4 Extraction Phase

#### Vision-LLM Extraction Strategy
- **Primary Method**: Vision-LLM with JSON schema prompts for each `doc_type`
- **Document Types Supported**:
  - `VA` (Voluntary Analysis)
  - `sample_tag` (Sample Tag)
  - `coupon` (Test Coupon)
  - `form_vi` (Form VI)
  - `invoice` (Invoice)
  - `form_viia` (Form VIIA - Lab Test Report)
  - `do_letter` (DO Letter)
- **Schema Prompts**: Each doc_type has a specific JSON schema that guides the LLM extraction
- **Confidence Handling**: Always treated as low by default - no auto-accept threshold

#### Zonal OCR Fallback
- **Usage**: Only for printed forms with reliably fixed layouts
- **Implementation**: Existing PaddleOCR + Tesseract pipeline
- **Trigger**: When Vision-LLM is unavailable or for handwritten/cursive content

#### Page Splitter for Multi-Sample PDFs
- **Function**: Groups multi-sample PDF bundles by extracted `sample_code` before per-page extraction
- **Implementation**: Pre-processing step that analyzes PDF structure and separates by sample
- **Output**: Individual document processing jobs for each sample

#### Async Processing with Celery
- **Job Type**: Async Celery job for OCR processing
- **Integration**: Uses existing Celery setup (`celery_app.py`)
- **Status Tracking**: Job status stored in `OCRDocument.status`

### 9.5 Review → Commit Phase

#### Editable Pre-filled Review Form
- **UI**: Web interface showing extracted data in editable form fields
- **Pre-filling**: Form fields populated from `raw_extracted_json`
- **User Interaction**: Manual correction of OCR errors

#### Diff Detection and OCRCorrection
- **Process**: On form submit, compare corrected values against `raw_extracted_json`
- **Auto-write**: Create `OCRCorrection` rows only for changed fields
- **Fields Tracked**: `field_name`, `doc_type`, `ocr_value`, `corrected_value`, `corrected_by`, `corrected_at`

#### Conflict Detection and Resolution
- **Before Upsert**: Normalize and compare new field values against existing confirmed `Sample` values
- **Match Handling**: Silent upsert for matching values
- **Mismatch Handling**:
  - Block upsert for conflicting fields
  - Write to `ConflictLog` table
  - Surface in resolution queue with side-by-side source thumbnails
- **Conflict Log Fields**: `sample_id`, `field_name`, `existing_value`, `existing_source_doc_id`, `new_value`, `new_source_doc_id`, `status`, `resolved_value`, `resolved_by`, `resolved_at`

#### Conflict Resolution Queue
- **UI**: Dashboard showing all unresolved conflicts
- **Visualization**: Side-by-side comparison with source document thumbnails
- **Resolution Workflow**: Manual selection of correct value or new correction
- **Status Flow**: `unresolved` → `resolved`

#### Sample Field Flagging
- **Conflict Indication**: Sample fields with open conflicts are flagged in UI
- **Autopopulation Block**: Conflict fields render as visible warnings instead of values

### 9.6 Feedback Loop

#### Correction Rate Dashboard
- **Metrics**: Per `(doc_type, field_name)` correction rate tracking
- **Data Source**: `OCRCorrection` table aggregated by doc_type and field_name
- **Visualization**: Identifies weak extraction fields requiring attention

#### Few-Shot Learning Integration
- **Pattern**: Same as existing FSS-section-suggester
- **Trigger**: Periodic (weekly) or on correction-count threshold
- **Process**: Trailing corrected examples folded into Vision-LLM prompt as few-shot pairs
- **Refresh**: Automated refresh of few-shot examples based on new corrections

### 9.7 Autopopulation Phase

#### Field Mapping Dictionary Approach
- **Central Configuration**: Single field mapping dictionary for all document generators
- **No Per-Document Hardcoding**: Unified mapping from confirmed Sample + LabTestParameter fields
- **Supported Generators**:
  - DO Letter Generator
  - Bill Generator  
  - Case File/Petition Generator
  - Adjudication Notices

#### Non-Conforming Result Handling
- **Auto-draft**: Non-conforming lab results automatically draft FBOIssue
- **State Machine**: Uses existing FBOIssue state machine
- **Approval**: DO remains sole approver - nothing auto-dispatches

#### Manual Override Capability
- **At Generation Time**: Every autopopulated field remains manually overridable
- **Conflict Handling**: Conflict fields render as visible warnings instead of values
- **User Control**: Full manual control over final document content

### 9.8 Operational Modes

#### Backfill Mode (Bulk Historical PDFs)
- **Entry Point**: Bulk upload endpoint for historical PDF processing
- **Processing**: Same pipeline as forward mode
- **Batch Handling**: Optimized for large volumes with progress tracking
- **Integration**: Uses existing batch processing capabilities

#### Forward Mode (Single Sample, Real-Time)
- **Entry Point**: Sample creation/upload in real-time
- **Processing**: Immediate pipeline execution as sample progresses
- **Status Tracking**: Real-time status updates through existing task webhook system

#### Shared Pipeline
- **Unified Architecture**: Both modes share identical extraction → storage → autopopulation pipeline
- **Configuration**: Mode-specific configuration at entry point only
- **Code Reuse**: Maximum code reuse between operational modes

### 9.9 Implementation Files and Components

#### New Blueprints Required
| Blueprint | Purpose | Key Files |
|---|---|---|
| `app/ocr_extraction/` | OCR extraction service | `__init__.py`, `routes.py`, `service.py`, `schemas/` |
| `app/conflict_resolution/` | Conflict resolution UI and API | `__init__.py`, `routes.py`, `templates/` |
| `app/autopopulation/` | Autopopulation service | `__init__.py`, `service.py`, `mappings.py` |
| `app/feedback_dashboard/` | Correction feedback analytics | `__init__.py`, `routes.py`, `templates/` |

#### Model Extensions
- **File**: `app/models.py`
- **Additions**: `OCRDocument`, `LabTestParameter`, `OCRCorrection`, `FieldAuthority`, `ConflictLog`
- **Modifications**: Extend `Sample` model with new fields and status enum

#### Service Layer
| Service | Purpose | File |
|---|---|---|
| `OCRExtractionService` | Vision-LLM and zonal OCR orchestration | `app/services/ocr_extraction.py` |
| `PageSplitterService` | Multi-sample PDF bundle processing | `app/services/page_splitter.py` |
| `ConflictResolutionService` | Conflict detection and resolution logic | `app/services/conflict_resolution.py` |
| `AutopopulationService` | Field mapping and document generation | `app/services/autopopulation.py` |
| `FeedbackService` | Correction analytics and few-shot learning | `app/services/feedback.py` |

#### Celery Tasks
- **File**: `app/ocr_extraction/tasks.py`
- **Tasks**:
  - `process_ocr_document_async` - Async OCR processing
  - `process_batch_ocr_job` - Batch processing for backfill mode
  - `refresh_few_shot_examples` - Periodic few-shot learning refresh

#### Templates
| Template | Purpose | File |
|---|---|---|
| `ocr_extraction/review.html` | Editable review form for OCR results |
| `conflict_resolution/queue.html` | Conflict resolution dashboard |
| `feedback_dashboard/index.html` | Correction rate analytics |
| `autopopulation/preview.html` | Autopopulated document preview |

#### Static Assets
| File | Purpose |
|---|---|
| `app/static/js/ocr_review.js` | Review form interaction logic |
| `app/static/js/conflict_resolution.js` | Conflict resolution UI logic |
| `app/static/css/ocr_styles.css` | OCR-specific styling |

### 9.10 Integration Points

#### Existing System Integration
- **OCR Pipeline**: Leverage existing `app/ocr_pipeline/` for zonal OCR fallback
- **Celery**: Use existing `celery_app.py` setup for async processing
- **Storage**: Use existing `app/utils/storage.py` for document storage
- **Models**: Extend existing `app/models.py` with new tables
- **Routes**: Add new blueprints to existing Flask app structure

#### Document Type Specifics
- **Form VIIA Integration**: Special handling for variable rows via `LabTestParameter` model
- **Sample Code Extraction**: Page splitter uses sample code detection for multi-sample PDFs
- **Field Authority**: Static configuration for conflict resolution priority

### 9.11 Implementation Priority and Timeline

#### Phase A: Foundation (High Priority)
1. **Database Schema** - Create new models and migrations
2. **OCR Extraction Service** - Vision-LLM + zonal OCR implementation
3. **Page Splitter** - Multi-sample PDF processing
4. **Celery Tasks** - Async processing infrastructure

#### Phase B: Review Workflow (High Priority)
5. **Review Interface** - Editable pre-filled forms
6. **Diff Detection** - OCRCorrection automatic creation
7. **Conflict Resolution** - ConflictLog and resolution queue

#### Phase C: Autopopulation (High Priority)
8. **Field Mapping** - Unified mapping dictionary
9. **Document Generators** - Integration with existing generators
10. **Conflict Flagging** - UI indication of conflicted fields

#### Phase D: Feedback Loop (Medium Priority)
11. **Correction Dashboard** - Analytics and visualization
12. **Few-Shot Learning** - Periodic prompt refresh

#### Phase E: Operational Modes (Medium Priority)
13. **Backfill Mode** - Bulk processing interface
14. **Forward Mode** - Real-time processing integration

### 9.12 Success Metrics

- **Extraction Accuracy**: >90% field-level accuracy for printed forms, >70% for handwritten
- **Conflict Resolution Time**: <5 minutes average per conflict
- **Autopopulation Coverage**: 100% of standard document fields supported
- **User Satisfaction**: Manual correction effort reduced by >60%
- **System Performance**: Batch processing of 100+ documents/hour

### 9.13 Dependencies and Requirements

#### New Dependencies
- **Vision-LLM API**: OpenRouter/OpenAI API access for document extraction
- **Additional Python Packages**: `pydantic` (already present), `tenacity` (for retry logic)

#### Infrastructure Requirements
- **API Keys**: Vision-LLM provider credentials
- **Storage**: Existing storage infrastructure sufficient
- **Compute**: Existing Celery worker capacity for async processing

#### Configuration
- **Environment Variables**: `VISION_LLM_API_KEY`, `VISION_LLM_PROVIDER`, etc.
- **Settings**: Configurable confidence thresholds, retry logic, batch sizes

### 9.14 Testing Strategy

#### Unit Tests
- OCR extraction accuracy tests with known document samples
- Conflict detection and resolution logic tests
- Field mapping and autopopulation tests
- Few-shot learning refresh tests

#### Integration Tests
- End-to-end pipeline tests for each document type
- Backfill vs forward mode comparison tests
- Performance and load testing

#### User Acceptance Tests
- Review interface usability testing
- Conflict resolution workflow validation
- Autopopulation accuracy verification

---

## 10. Updated Consolidated TODO List

### Priority 1 — Foundational (Phase 1-3 completion)
1. [x] **Continuous auto-save** in Quill editor (editor.js) — **done (ee3db9a)**
2. [x] **Settings table** model + migration — **done (e9e3a0e)**
3. [x] **Annexure model** with metadata — **done (e9e3a0e)**
4. [x] **Evidence model** extended to all types — **done (e9e3a0e)**
5. [x] **SQLite FTS5 search** index + API — **done (00db98e)**
6. [ ] **Version history table** + compare/restore (Version model exists; compare/restore pending)
7. [ ] **JSON export** endpoint

### Priority 2 — Core Features (Phase 6-12)
8. [x] **Cross-reference engine** — **done** (`app/cross_reference/` + PDF pipeline integration)
9. [ ] **Dynamic TOC generator**
10. [ ] **PDF assembly** (headers/footers/page numbers)
11. [ ] **QR code** generation
12. [ ] **Signature placeholders**
13. [ ] **Validation engine**
14. [ ] **Timeline engine**

### Priority 3 — Extraction → Storage → Autopopulation Pipeline (NEW)
15. [ ] **Database Schema** - OCRDocument, LabTestParameter, OCRCorrection, FieldAuthority, ConflictLog models
16. [ ] **Update Sample model** - Add OCR fields and status enum
17. [ ] **OCR Extraction Service** - Vision-LLM + zonal OCR fallback implementation
18. [ ] **Page Splitter** - Multi-sample PDF bundle processing
19. [ ] **Celery Tasks** - Async OCR processing with status tracking
20. [ ] **Review Interface** - Editable pre-filled forms with diff detection
21. [ ] **Conflict Resolution** - ConflictLog system with resolution queue UI
22. [ ] **Autopopulation Service** - Field mapping dictionary and document generation integration
23. [ ] **Feedback Dashboard** - Correction rate analytics and few-shot learning refresh
24. [ ] **Operational Modes** - Backfill (bulk) and forward (real-time) processing

### Priority 4 — Intelligence & Analytics (Phase 13-19)
25. [ ] **Knowledge graph**
26. [ ] **Analytics dashboard**
27. [ ] **AI assistant service** (LLM integration)
28. [ ] **Case intelligence engine** (readiness score)
29. [ ] **Multi-user RBAC** (Role model, @role_required)
30. [ ] **Cloud sync** (Supabase)
31. [x] **Backup & restore** (manual ZIP backup/restore done in Phase 3; automated scheduled export still open)
32. [ ] **Plugin architecture**

---

*End of Report*
