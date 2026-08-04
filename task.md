# Task List — NSA Webservice

> **Purpose:** Consolidated, actionable TODO list for agents. Organized by priority with checkboxes, file targets, and acceptance criteria. Sources: `SECURITY_TODO.md`, `ALL_TODO_MERGED.md`, `ROADMAP_ALIGNMENT_REPORT.md`, `ENGINEERING_ASSESSMENT.md`, `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md`.

---

## Completed Milestones

> Items finished after this file was created are tracked here so agents can trust `plan.md` status at a glance.

- [x] **Phase 4 — Annexure Replace** (`POST /annexure/<id>/replace` + UI button + 8 tests). Replaces the stored file on an existing annexure in place: re-extracts hash/page-count/OCR/size/MIME, keeps the annexure id + letter so document references stay valid, rejects content-hash duplicates of *other* annexures (self re-upload allowed), deletes the old file after commit, and audit-logs `ANNEXURE_REPLACED`. Files: `app/annexure/routes.py`, `app/annexure/templates/annexure/index.html`, `tests/test_annexure.py`.

---

## Priority 0 — Security Risk (MUST FIX)

### S7: Scraper TLS — Remove `check_hostname=False` and `CERT_NONE`

- **File:** `app/utils/lookup.py`, lines 108–111
- **What:** KMC trade license scraper disables certificate verification (`ctx.check_hostname = False`, `ctx.verify_mode = ssl.CERT_NONE`). KMC's cert is valid (Sectigo); the real fix is `SECLEVEL=1` (line 109, already present).
- **Fix:** Remove the two `check_hostname=False` / `verify_mode=CERT_NONE` lines. Keep `SECLEVEL=1`.
- **Risk:** MITM attacker could intercept KMC data.
- **Verify:** `python -c "from app.utils.lookup import lookup_ce; lookup_ce('test')"` — should not bypass TLS.

### S6a: Remove legacy root-level `suggester.py`

- **File:** `suggester.py` (project root)
- **What:** Duplicate rule-based suggester. The real one is `app/utils/suggester.py`.
- **Fix:** Verify 0 importers (`rg -l "import suggester" --type py .`), then delete.
- **Verify:** `rg -rn "suggester" --type py .` shows only `app/utils/suggester.py` references.

---

## Priority 1 — Foundational (Phases 10–12)

### Phase 10: Fuzzy Search

- **Files:** `pyproject.toml`, `app/search/indexer.py`, `app/search/routes.py`
- **Tasks:**
  - [ ] Add `rapidfuzz` to `pyproject.toml` `[project.dependencies]`
  - [ ] In `search/indexer.py::search()`, add fuzzy fallback (`fuzz.ratio`/`partial_ratio`) when FTS5/LIKE returns nothing
  - [ ] Expose `fuzzy=true` query param in `search/routes.py`
  - [ ] Add UI toggle for fuzzy search
  - [ ] Also fixes: `document_cleaner/normalizers.py` imports `rapidfuzz` but it's not declared
- **Tests:** `tests/test_search.py` — add fuzzy match test cases
- **Acceptance:** `pip install -e .` succeeds; search endpoint returns fuzzy results for typos

### Phase 12: Legal Validation Engine

- **Files:** New blueprint `app/validation/`
- **Tasks:**
  - [ ] Create `app/validation/__init__.py`, `engine.py`, `rules.py`
  - [ ] Implement `ValidationEngine` running all checks:
    - [ ] Mandatory sections (reuse `suggester.py`)
    - [ ] Missing signatures (scan templates for signature placeholders)
    - [ ] Numbering validation (case_number, sample_code, lab_reg_no formats)
    - [ ] Statutory reference validation (Sections 55/56/58/63/64 rules)
    - [ ] Duplicate evidence detection (hash-based)
    - [ ] Timeline consistency validation
    - [ ] Document completeness check
  - [ ] Return structured `{score, warnings, errors, suggestions}`
  - [ ] Wire into `app/legal_analysis/routes.py`
  - [ ] Register blueprint in `app/__init__.py`
- **Tests:** `tests/test_validation.py` (new)
- **Acceptance:** POST to `/validation/validate` returns JSON score + warnings

### Phase 13: Timeline Engine

- **Files:** `app/models/document.py`, new `app/timeline/`
- **Tasks:**
  - [ ] Add `TimelineEvent` model (event_type, case_id, timestamp, document_ref, description)
  - [ ] New migration: `add_timeline_event_table.py`
  - [ ] Implement `app/timeline/engine.py` — derive events from case dates (complaint → inspection → sampling → dispatch → lab → notice → reply → petition → order)
  - [ ] Create `app/timeline/routes.py` + Gantt-style template
  - [ ] Link events to supporting documents
- **Acceptance:** Timeline auto-generates from an existing case's dates; events displayed in Gantt view

---

## Priority 2 — Core Features (Phases 13–19)

### Phase 15: Analytics Dashboard

- **Files:** New `app/analytics/`
- **Tasks:**
  - [ ] Create `app/analytics/__init__.py`, `routes.py`, `templates/`
  - [ ] Implement aggregate queries:
    - [ ] Pending/disposed cases (from CaseFile + Adjudication)
    - [ ] Inspection counts (by compliance status)
    - [ ] Sample status pipeline (collected → sent_to_lab → result_received → conforms/non_conforming)
    - [ ] Evidence count per case
    - [ ] Legal provisions used (from `applicable_sections`)
    - [ ] Violation trends (checklist data)
    - [ ] Geographic distribution (geo coords from FboIssue)
  - [ ] Add Chart.js for visualizations
  - [ ] Add Leaflet map for geographic distribution
  - [ ] Add nav link in `app/templates/base.html`
  - [ ] Register blueprint in `app/__init__.py`
- **Acceptance:** `/analytics/` renders charts showing case/inspection/sample metrics

### Phase 18: Multi-user RBAC

- **Files:** `app/models/`, new `app/decorators.py`
- **Tasks:**
  - [ ] Add `Role` model + `UserRole` association table
  - [ ] Add `Comment` model (for approval workflow)
  - [ ] New migration: `add_role_user_role_comment_tables.py`
  - [ ] Create `@role_required` decorator in `app/decorators.py`
  - [ ] Add approval status field to `CaseFile` / `Adjudication`
  - [ ] Add user-management UI (admin only)
  - [ ] Add comments system to document viewer
- **Acceptance:** Non-admin users get 403 on admin routes; `@role_required("admin")` works

### Phase 16: Backup & Export

- **Files:** `app/case_file_generator/routes.py`, `app/case_file_generator/tasks.py`, `celery_app.py`
- **Tasks:**
  - [ ] Add `GET /api/cases/<id>/export.json` (full case: CaseFile + annexures + evidence + versions)
  - [ ] Extend ZIP export to include annexures, evidence, versions
  - [ ] Implement case import (JSON → DB restore)
  - [ ] Add Celery beat schedule for periodic backup snapshots (reuse `app/utils/backup.py`)
  - [ ] Add backup download UI in settings
- **Acceptance:** JSON export returns complete case data; scheduled backup runs daily

### Phase 11: AI Assistant

- **Files:** New `app/ai_assistant/`
- **Tasks:**
  - [ ] Create `app/ai_assistant/__init__.py`, `service.py`, `routes.py`, `templates/`, `static/js/`
  - [ ] **Provider decision:** OpenRouter vs OpenAI — choose one and document
  - [ ] Add `openai` or `httpx` to `pyproject.toml`
  - [ ] Implement LLM service with prompt templates:
    - [ ] Grammar correction
    - [ ] Legal language improvement
    - [ ] Summarize evidence
    - [ ] Detect contradictions
    - [ ] Identify missing annexures
    - [ ] Draft prayers/facts/grounds
  - [ ] Wire AI features into document editor UI (sidebar)
  - [ ] Add API key env var (`AI_ASSISTANT_API_KEY` + provider)
- **Acceptance:** Editor sidebar has "AI: Summarize this section" button; returns LLM response
- **Risk:** Cost monitoring needed — add token tracking (see S10c in task.md §Operational)

### Phase 19: AI Case Intelligence

- **Files:** New `app/case_intelligence/`
- **Tasks:**
  - [ ] Create `app/case_intelligence/engine.py` (composes Phase 12 + Phase 11)
  - [ ] Implement evidence strength analysis (OCR quality, photo verification status)
  - [ ] Implement allegation-to-evidence traceability
  - [ ] Implement date conflict detection
  - [ ] Compute composite readiness score (0–100)
  - [ ] Wire into `app/legal_analysis/routes.py`
- **Acceptance:** `/case_intelligence/<case_id>` returns readiness score + evidence gaps

### Phase 14: Knowledge Graph

- **Files:** New `app/knowledge_graph/`
- **Tasks:**
  - [ ] Create `app/knowledge_graph/models.py`, `engine.py`, `routes.py`
  - [ ] OR use Neo4j (README mentions as Level 8 target) — decide: SQL graph tables vs Neo4j
  - [ ] Extract entities from case data (parties, products, dates, sections, evidence)
  - [ ] Build relationship mappings (Complaint → Inspection → Observation → Evidence → Sample → Lab Report → Violation → Ground → Prayer)
  - [ ] Provide graph traversal API (`GET /knowledge_graph/case/<id>/path`)
- **Acceptance:** API returns entity relationships for a given case

### Phase 20: Plugin Architecture

- **Files:** New `app/plugins/`
- **Tasks:**
  - [ ] Create `app/plugins/__init__.py`, `registry.py`, `base.py`
  - [ ] Define interfaces: `OCRProvider`, `AIProvider`, `RuleProvider`, `PDFProvider`
  - [ ] Refactor `app/ocr_pipeline/ocr_engine.py` behind `OCRProvider` interface
  - [ ] Refactor `app/utils/suggester.py` behind `RuleProvider` interface
  - [ ] Refactor `app/pdf_assembly/__init__.py` behind `PDFProvider` interface

---

## Priority 3 — Pipeline (Extraction → Storage → Autopopulation)

> Source: `ROADMAP_ALIGNMENT_REPORT.md` §9–§10. This is the OCR→review→conflict→autopopulation pipeline described in section 9.

### Phase A: Foundation (DB Schema + Services)

- [ ] Create models: `OCRDocument`, `LabTestParameter`, `OCRCorrection`, `FieldAuthority`, `ConflictLog` in `app/models/`
- [ ] Extend `Sample` model with OCR-discovered fields (nature_of_food, batch_no, mfd, exp, etc.) + status enum
- [ ] New migration for all above
- [ ] Create `app/services/ocr_extraction.py` — Vision-LLM + zonal OCR orchestration
- [ ] Create `app/services/page_splitter.py` — multi-sample PDF bundle processing
- [ ] Create Celery tasks in `app/ocr_extraction/tasks.py`: `process_ocr_document_async`, `process_batch_ocr_job`

### Phase B: Review Workflow

- [ ] Create `app/ocr_extraction/` blueprint with `routes.py` + `service.py` + `schemas/`
- [ ] Create `app/conflict_resolution/` blueprint with `routes.py` + `templates/`
- [ ] Implement editable review form (pre-filled from `raw_extracted_json`)
- [ ] Implement diff detection → auto-create `OCRCorrection` rows for changed fields
- [ ] Implement conflict detection → write to `ConflictLog` → surface in resolution queue

### Phase C: Autopopulation

- [ ] Create `app/autopopulation/` with `service.py` + `mappings.py`
- [ ] Build unified field mapping dictionary (DO Letter, Bill Generator, Case File, Adjudication, Notices)
- [ ] Integrate with existing generators (`case_file_generator`, `bill_generator`, `adjudication`)
- [ ] Auto-draft FboIssue for non-conforming lab results

### Phase D: Feedback Loop

- [ ] Create `app/feedback_dashboard/` blueprint
- [ ] Track per (doc_type, field_name) correction rates
- [ ] Aggregate from `OCRCorrection` table
- [ ] Implement few-shot learning refresh (fold corrected examples into Vision-LLM prompt)
- [ ] Celery task: `refresh_few_shot_examples` (periodic or on correction threshold)

### Phase E: Operational Modes

- [ ] Backfill mode: bulk upload endpoint for historical PDFs
- [ ] Forward mode: real-time processing on sample creation
- [ ] Both share identical pipeline (configuration at entry point only)

---

## Priority 4 — Hardening & Debt

### Security Hardening (Security TODO)

- [ ] **S2:** Enforce CSP — flip from report-only to enforcement
  - File: `app/__init__.py` line ~131 (`content_security_policy_report_only`)
  - Check: verify no violations in production CSP reports first
  - Then: flip `report_only=True` → `False`

- [ ] **S9a:** Extend StaleDataError handling to Inspection + Sample blueprints
  - File: `app/inspection/routes/inspection_routes.py` (PUT/DELETE), `app/sample/routes.py` (PUT/DELETE)
  - Check: do models already have `version_id_col`? If not, add `version_id` + `__mapper_args__`
  - Add try/except `StaleDataError` → return 409 with user-friendly message

- [ ] **S10a:** Create `.github/dependabot.yml` — weekly pip check
- [ ] **S10b:** Create `.github/workflows/pip-audit.yml` — run on every push
- [ ] **S10c:** Database backup monitoring — health-check endpoint reporting last successful sync; document 90-day free-tier expiry

### Engine Repairs (already done — verify these are still clean)

- [x] `app/pdf_assembly/__init__.py` — missing `_get_default_header_template`/`_get_default_footer_template`/`_apply_headers_footers` **done**
- [x] Guarded WeasyPrint import via `import_weasyprint()` **done**
- [x] Fixed `_add_pdf_bookmarks` + footer injection using `rfind` instead of `endswith` **done**
- [x] `tasks.py` no longer imports WeasyPrint directly **done**

### Technical Debt Cleanup (already done — verify)

- [x] `app/services/legal_engine.py` — singleton removed, `LegalEngineUnavailable` removed, `get_legal_engine()` returns class directly **done**
- [x] `app/models.py` → `app/models/` package with submodules + backward-compatible `__init__.py` **done**
- [x] `app/inspection/routes.py` (1077 lines) → `app/inspection/routes/` package (4 modules) **done**
- [x] `datetime.utcnow()` → `datetime.now(timezone.utc)` (all 11 occurrences) **done**
- [x] `Model.query.get()` → `db.session.get()` (all occurrences) **done**
- [x] `db.get_engine()` → `db.engines['default']` in `migrations/env.py` **done**
- [x] CSV data files untracked (~70MB reduction) **done**

### Performance Quick Wins (1–2 days)

- [ ] Add SQLAlchemy connection pooling config to `app/__init__.py` / `app/extensions.py`
- [ ] Cache FSO names in Redis (or in-memory fallback) in `app/utils/fso_data.py`
- [ ] Enable Jinja2 template caching (`app.jinja_env.cache = ...`)
- [ ] Add response compression (Flask-Compress or Gzip)
- [ ] Add `/health` endpoint
- [ ] Fix N+1 queries with eager loading (`joinedload`/`selectinload`)

---

## Deepening Tasks (Architectural Refactoring)

> Source: `REFACTORING_PLAN.md`. Goal: increase Module Depth in 5 shallow areas. Implementation order prioritized by dependency (1 is a prerequisite for none; 5 depends on 1+2).

### D1: Extract CaseResolver (from cross-module case resolution)

**Module Depth target:** 1 → 4 · Effort: 1 day · Risk: Low

- [ ] Create `app/shared/case_resolver.py` — `CaseResolver` class + `ResolvedCase` dataclass
  - Interface: `resolve(case_id, kind=None) -> ResolvedCase | None`
  - Returns: `{case_id, adjudication_id, case_type, case_number, record}`
- [ ] Replace `_resolve_case()` in `app/document_viewer/routes.py`
- [ ] Replace `_resolve_target()` + `_kind_param()` in `app/version_control/routes.py`
- [ ] Replace inline `db.session.get` lookups in `app/evidence/routes.py`, `app/search/indexer.py`, `app/annexure/routes.py`
- [ ] Create `tests/test_case_resolver.py` — ID collision, missing records, kind hints

### D2: Extract DocumentSaveCoordinator (from document viewer inlined concerns)

**Module Depth target:** 2 → 4 · Effort: 1 day · Risk: Low

- [ ] Create `app/services/document_lifecycle.py` — `DocumentSaveCoordinator` class + `SaveResult` dataclass
  - Interface: `coordinator.save(case_id, case_type, doc_type, html, delta, force_snapshot=False) -> SaveResult`
  - Encapsulates: `save_saved_document`, `VersionService` (force + dedup), `log_audit`
- [ ] Replace 5 private helpers in `app/document_viewer/routes.py` (`_resolve_case`, `_save_document_content`, `_log_audit`, `_snapshot_version`, `_actor`)
- [ ] Create `tests/test_document_lifecycle.py` — mock VersionService + save_saved_document

### D3: Fill PDFAssemblyEngine (from pdf_utils grab-bag)

**Module Depth target:** 3 → 4 · Effort: 2 days · Risk: Medium

- [ ] Create `app/pdf_assembly/engine.py` — `PDFAssemblyEngine` class
  - Interface: `assemble(html, case_id, adjudication_id, photo_urls) -> (bytes, error)`, `post_process(html, case_id, adjudication_id) -> str`, `embed_photos(urls) -> list[dict]`, `generate_from_html(html) -> (bytes, error)`
  - Migrate all PDF concerns from `pdf_utils.py` + existing `pdf_assembly/__init__.py`
- [ ] Add backward-compatible shims in `app/utils/pdf_utils.py` (re-export from engine)
- [ ] Update 5 callers (`adjudication/routes.py`, `case_file_generator/tasks.py`, `document_viewer/renderer.py`, `document_viewer/routes.py`, `pdf_assembly/__init__.py`)
- [ ] Consolidate tests: merge `test_phase8_pdf_assembly.py` + `test_pdf_photo_embedding.py` behind engine interface

### D4: Extract InspectionPhotoService (from mechanical split)

**Module Depth target:** 1 → 4 · Effort: 2 days · Risk: Medium

- [ ] Create `app/inspection/photo_service.py` — `InspectionPhotoService` class + `PhotoUploadResult` dataclass
  - Interface: `upload_evidence(inspection_id, file_obj, lat, lng, accuracy, captured_at)`, `upload_adjudication_photo(adjudication_id, file_obj)`, `delete(photo_id)`, `list_for_inspection(inspection_id)`, `list_adjudication(adjudication_id)`
  - Encapsulates: EXIF extraction, image validation (PIL), secure naming, storage, OCR dispatch, geo verification, stamping, audit logging
- [ ] Thin down `app/inspection/routes/photo_routes.py` to HTTP adapters only
- [ ] Simplify `app/inspection/routes/__init__.py` — export only `inspection_bp`
- [ ] Create `tests/test_inspection_photo_service.py`

### D5: Extract DocumentCaseManager (from Case/Adjudication duplication)

**Module Depth target:** 2 → 4 · Effort: 3 days · Risk: Medium

- [ ] Create `app/shared/document_case_manager.py` — `DocumentCaseManager` class
  - Interface: parameterized by `(model, template_dir, bp, case_type, sections_fn)`
  - Methods: `register_routes(bp)`, `get_case(id)`, `get_case_by_number(num)`, `list_cases(filters)`, `render_editor(id)`, `generate_documents(id, form_data)`, `regenerate(id)`, `xref_report(id)`, `toc_report(id)`, `renumber_annexures(id, letters)`
- [ ] Thin down `app/case_file_generator/routes.py` to config + 1 extra route (`lookup_sample`)
- [ ] Thin down `app/adjudication/routes.py` to config + 2 extra routes (`lookup_ce_route`, `suggest_sections_route`)
- [ ] Consolidate `test_step1.py`–`test_step5_integration.py` into parametrized `tests/test_document_case_manager.py`

---

## Priority 5 — Cloudinary (testing & hardening)

> Source: `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md` (score 4.3/5, implemented)

- [ ] Add unit tests for Cloudinary helpers:
  - `test_extract_cloudinary_public_id` with various URL formats
  - Mock `cloudinary.uploader.upload`/`destroy` for `_upload_to_cloudinary` / `_delete_from_cloudinary`
  - File: `tests/test_storage_cloudinary.py` (new)
- [ ] Add retry logic for Cloudinary operations (use `tenacity`)
  - File: `app/utils/storage.py`
- [ ] Add credential validation health endpoint
  - `GET /health/cloudinary` — validates config, returns 200/500
- [ ] Support `CLOUDINARY_URL` convenience env var (parse to components)

---

## Priority 6 — Future Levels 1–3 (Infra)

> Source: README.md roadmap + `ENGINEERING_ASSESSMENT.md`

### Level 1 Hardening

- [ ] PostgreSQL production migration (schema ready, production pending)
- [ ] Persistent Celery worker deployment
- [ ] TLS fix for KMC scraper (S7 above)
- [ ] End-to-end test suite
- [ ] Docker containerization

### Level 2 Platform Upgrade

- [ ] FastAPI migration assessment (keep Flask vs migrate)
- [ ] OpenAPI/Swagger documentation
- [ ] Structured logging (structlog)
- [ ] Redis caching layer (request cache)
- [ ] Health check + metrics endpoints
- [ ] Sentry + Prometheus monitoring

### Level 3 AI Integration

- [ ] Vector DB setup (Qdrant/Pinecone/pgvector)
- [ ] Document chunking + embedding pipeline (Phase 11 foundation)
- [ ] Conversation history tables (Phase 11)
- [ ] LangGraph workflow orchestration
- [ ] OpenRouter multi-LLM gateway

---

*End of task.md*
