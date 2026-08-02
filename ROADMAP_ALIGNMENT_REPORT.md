# Roadmap Alignment Report — NSA Webservice

> **Generated:** 2026-08-02
> **Last status update:** 2026-08-02 (steps 1–4 of the recommended order complete)
> **Purpose:** Evaluate the current NSA Webservice codebase against the 20-phase roadmap and produce a detailed implementation plan with TODO list and file-level edit guidance.

> **📌 Current Project Status Update (2026-08-02):**
> - **Cloudinary Integration:** ✅ **COMPLETED** for adjudication photos (`InspectionPhoto`). Backend implemented in `app/utils/storage.py` with R2/B2 fallback. Environment config added to `.env.example`, `render.yaml`, and `pyproject.toml`.
> - **Evaluation Score:** 4.3/5 (see `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md` for details).
> - **Next Steps:** Add unit tests, retry logic, and credential validation (see [Recommendations](#8-recommendations) in Cloudinary plan).
> - **Out of Scope:** `PhotoEvidence` migration (requires OCR refactor).

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
- **Cloudinary Integration:** ✅ **Fully implemented** for adjudication photos (`InspectionPhoto`). The backend in `app/utils/storage.py` now supports Cloudinary as an optional storage backend (active when `CLOUDINARY_*` env vars are set), with automatic fallback to R2/B2. No changes required to routes, models, or PDF embedding logic.
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
| InspectionPhoto | inspection_photos | adjudication_id, file_url, caption |
| PhotoEvidence | photo_evidence | image_id, case_id, filepath, geo, verification_status |
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

| Roadmap Item | Current State | Gap | Action Needed |
|---|---|---|---|
| React + TypeScript frontend | Flask + Jinja2 + Vanilla JS | **Major mismatch** | Build frontend/ package or keep Flask templates |
| Tailwind CSS | Custom theme.css (1852 lines) | Not using Tailwind | Skip if keeping Flask; add if new React frontend |
| Zustand | Server-side session | N/A for Flask | Only if React path chosen |
| Dexie.js (IndexedDB) | SQLAlchemy | N/A for Flask | Only if React path chosen |
| React Router | Flask routes | N/A for Flask | Only if React path chosen |
| Vite | setuptools | N/A for Flask | Only if React path chosen |
| ESLint + Prettier | ruff + black + mypy + bandit | JS linting absent | Only if React path chosen |
| GitHub Actions (CI) | Present (lint, pip-audit, validation, deploy) | Already exists | No action needed |

### Phase 1 — Core Petition Engine (Largely Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Case Model (Petition, Parties, Facts, Grounds, Prayer) | CaseFile model exists | app/models.py:12-90 |
| Petition form (UI) | Jinja2 template with all fields | app/case_file_generator/templates/case_file_generator/index.html |
| Petition template (PDF) | WeasyPrint-rendered HTML | app/case_file_generator/templates/case_file_generator/petition.html |
| Validation | process_form_data() + get_applicable_sections() | app/case_file_generator/routes.py:27-48 |
| Live preview | Quill editor with live iframe preview | app/static/js/document_viewer/editor.js |
| PDF export | WeasyPrint via QStash async or sync fallback | app/case_file_generator/tasks.py, app/utils/pdf_utils.py |
| Print layout | CSS print styles in theme.css | app/static/css/theme.css |
| Auto-save | ✅ Continuous (debounced text-change) | app/document_viewer/routes.py:32-115, editor.js |
| Non-sample adjudication Petition | Adjudication model + templates | app/adjudication/routes.py + templates |
| Permission Letter (both types) | Templates for both types | permission_letter.html, Legal_NonsampleAdjudication_Template.html |

**TODO:**
1. [x] Continuous auto-save (debounce on text-change) in editor.js — **done (commit ee3db9a)**
2. [x] Store Quill Delta alongside HTML for round-trip fidelity — **done (commit ee3db9a)**
3. [ ] Add validation error display in UI
4. [ ] Add Facts/Grounds/Prayer structured sections to petition template

**Files to edit:** app/static/js/document_viewer/editor.js, app/case_file_generator/templates/case_file_generator/petition.html, app/document_viewer/routes.py

### Phase 2 — Rich Editor (Implemented - Quill 2.x)

| Feature | Status | File(s) |
|---|---|---|
| Rich text | Quill 2.x with Snow theme | app/static/vendor/quill/ |
| Tables | Quill table module | editor.js |
| Lists | Bulleted/numbered list toolbar | editor.js |
| Images | Toolbar has image option, no upload handler | editor.js |
| Hyperlinks | Toolbar includes link module | editor.js |
| Custom legal blocks | Via Jinja2 template variables | Templates |
| Track formatting | Quill Delta stored alongside HTML (.delta files) | editor.js |
| Store as HTML | HTML stored in instance/saved/ | document_viewer/routes.py |
| Store as Quill Delta | ✅ Stored alongside HTML | document_viewer/routes.py |
| Store as Markdown | Not stored | Would need quill-delta-to-markdown |

**TODO:**
1. [ ] Wire up image upload handler in Quill editor
2. [x] Store Quill Delta — **done (commit ee3db9a)**
3. [ ] Add Markdown export option

**Files to edit:** app/static/js/document_viewer/editor.js, app/models.py, migrations/versions/new_saved_documents_table.py

### Phase 3 — Local Database (Partially Implemented - Server-side)

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
| Backup | Partial - instance folder ZIP | case_file_generator/routes.py |
| Restore | Not implemented | New feature |
| Auto-save | Partial (see Phase 1) | editor.js, document_viewer/routes.py |
| Offline support | Not server-side offline | Would need PWA |

**TODO:**
1. [x] Create Settings model + migration (app-level config) — **done (commit e9e3a0e)**
2. [x] Create Annexure model (UUID, caption, date, hash, page_count, ocr_text, tags) — **done (commit e9e3a0e)**
3. [x] Add Version model for full audit trail + snapshot-on-save — **done (commit e9e3a0e)**
4. [x] Implement SQLite FTS5 search index + search API — **done (commit 00db98e)**
5. [ ] Implement backup and restore endpoints
6. [x] Extend PhotoEvidence to general Evidence model — **done (commit e9e3a0e)**

**Files to edit/create:** app/models.py (add Settings, Annexure, Version, Evidence), migrations/versions/, app/search/ (new blueprint)

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

### Phase 5 — Evidence Management (Partially Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Evidence Types (Photos, Videos, Reports, Licences, Bills, Lab reports) | Photos only via PhotoEvidence/InspectionPhoto | app/models.py:330-347, 163-176 |
| Drag & drop upload | Not implemented | New frontend feature |
| Compression | Not implemented | New feature |
| OCR | PaddleOCR + Tesseract pipeline | app/ocr_pipeline/, app/inspection/tasks.py |
| Metadata extraction | Partial (geo coords, verification) | app/models.py:330-347 |
| Categorization | Not implemented | New feature |
| Search | Not implemented for evidence | New feature |
| Thumbnail generation | Not implemented | New feature |
| **Cloud Storage** | ✅ **Cloudinary for adjudication photos** | app/utils/storage.py (lines 162–269) |

**TODO:**
1. [ ] Extend Evidence model to support all types (video, report, licence, bill, lab report)
2. [ ] Add drag-and-drop upload UI
3. [ ] Implement image compression (Pillow)
4. [ ] Add thumbnail generation for images
5. [ ] Implement evidence categorization + search
6. [ ] Unify InspectionPhoto and PhotoEvidence into single Evidence model

**Files to edit/create:** app/models.py (extend/replace Evidence), app/evidence/ (blueprint), app/static/js/evidence_uploader.js, migrations/

### Phase 6 — Automatic Cross-Reference Engine (Not Implemented)

| Feature | Status | File(s) |
|---|---|---|
| Auto-generate paragraph to annexure/section references | Not implemented | New engine needed |
| Automatic paragraph/annexure/page numbering updates | Not implemented | Document post-processing |

**TODO:**
1. [ ] Build CrossReferenceEngine that parses text for patterns (paragraph numbers, Annexure refs, Section refs)
2. [ ] Link references to annexure metadata (page refs, section refs)
3. [ ] Implement renumbering on insert/delete
4. [ ] Integrate into PDF assembly pipeline

**Files to edit/create:** app/cross_reference/__init__.py, app/cross_reference/engine.py, app/utils/pdf_utils.py (renumbering pass)

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
8. [ ] **Cross-reference engine**
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
22. [ ] **Backup & restore**
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
| app/backup/ | Backup & restore | __init__.py, routes.py, tasks.py |
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
| 1 | Phase 0 | ✅ Architecture decision: keep Flask templates (no new React frontend) | — |
| 2 | Phase 1 | ✅ Continuous auto-save + delta storage | editor.js, document_viewer/routes.py |
| 3 | Phase 3 | ✅ Settings + Annexure + Evidence + Version models | models.py, migrations/ |
| 4 | Phase 3 | ✅ SQLite FTS5 search index + API | app/search/ |
| 5 | Phase 4 | ✅ Annexure upload + metadata extraction + letters + duplicate detection | app/annexure/ |
| 6 | Phase 5 | ⏳ **Extend evidence model → blueprint/UI (NEXT)** | app/evidence/ |
| 6 | Phase 5 | Extend evidence model | app/evidence/ |
| 7 | Phase 6 | Cross-reference engine | app/cross_reference/ |
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

1. **Cohesive production-ready Flask app** - ~245 tests, CI/CD, security hardening, 13 blueprints. Phase 0 (foundation) already complete.

2. **Biggest decision: frontend architecture.** Roadmap calls for React, but current app uses Flask + Jinja2 + Quill. New React frontend means existing templates become redundant.

3. **Quill editor already integrated** - supports rich text, tables, lists, links. HTML + Delta stored in `instance/saved/`. Supports Phase 2; only image upload remains.

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

---

*End of Report*
