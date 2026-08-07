# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> Status: Phases 0–10, Deepening D1–D5, Infrastructure, S9a, Phase 16, Phase A, Phase 13,
> Phase 21, and Priority 7 are implemented and verified. Phases 11–12, 14–15, 17–20 pending.

### Added

#### Priority 7 — Multi-Target Sheets Redundancy (Airtable + MS Excel Online)
- **Airtable sync service** (`app/services/airtable_sync.py`): parallel real-time sync to
  Airtable alongside Google Sheets; automatic base rotation at Airtable's 1,200-record
  free-tier limit (new base created via `/v0/meta/bases`); `AirtableBaseMap` tracking model;
  formula-injection escaping; R2 CSV export for the restore chain
- **Microsoft Excel Online sync service** (`app/services/excel_sync.py`): MS Graph API sync
  via `msal` OAuth2 client-credentials flow; thread-local token caching (~50 min); R2 CSV
  export. **Dormant** by default (`ENABLE_EXCEL_SYNC=false`) pending M365/Azure credentials
- **Backup coordinator** (`app/services/backup_coordinator.py`): orchestrates parallel export
  of all three targets (Sheets, Airtable, Excel) to R2; per-target isolation (one failure
  does not block the others); registered in the QStash `TASK_REGISTRY`
- **Settings routes**: restored the `backup_restore` route; added
  `POST/GET /settings/backup-redundant-to-r2` (admin-only) redundant-backup triggers
- **Restore chain** (`app/utils/sync.py`): 12 new functions/variables —
  `restore_from_airtable_csv()`, `restore_from_excel_csv()`, `restore_from_sheets_csv()`,
  `restore_if_empty()` (Airtable → Excel → Sheets), `trigger_backup()`, `_restore_from_records()`,
  `_restore_module()`, `_parse_csv_value()`, `_is_empty_sqlite_db()`, and module-level maps
- **QStash daily backup schedule**: recurring task at 02:00 UTC (`0 2 * * *`) gated behind
  `ENABLE_BACKUP_SCHEDULE`
- **Tests**: `tests/test_priority7_redundancy.py` — **43/43 pass** (no regressions)

#### Phase 21 — Food Cell DO Intimation
- **`app/food_cell/` blueprint** (`/food-cell`): `generate_and_forward_do_intimation()`
  service (templated HTML + WeasyPrint PDF with stub fallback), `DoIntimation` model +
  `food_cell_forwarded` column on `Sample`, `send_do_intimation` Celery task wired post-save
  in `app/sample/routes.py::create_sample()`
- **Routes**: `GET /food-cell/do-intimation/<sample_id>/pdf|html|status` and
  `POST .../regenerate`
- **Sync integration**: best-effort forwarding to Sheets + Airtable + Excel
  (`food_cell_do_intimations` worksheet/table)
- **Tests**: `tests/test_food_cell_do_intimation.py` — **15/15 pass**

#### Phase 13 — Timeline Engine & Gantt Visualization
- **`app/timeline/` blueprint**: `TimelineEngine` (extract/refresh/validate_sequence/build_payload)
  persisting case_file events to the `timeline_event` table; adjudication timelines served
  ephemerally; vertical timeline + Gantt UI with document links
- **Global case-picker**: keyboard-navigable search dropdown in `base.html` reachable from
  every page; entry points across search, evidence, annexure, inspection, audit,
  version-control, and sample surfaces; `case_id`/`timeline_url` on the sample-detail JSON
- **Wired orphaned `app/audit` routes** (audit log viewer was previously 404) and fixed stale
  `edit_case_file`/`edit_adjudication` url_for names
- **Tests**: `tests/test_timeline.py` — **21/21 pass**

#### Phase A — OCR Pipeline Foundation
- **`app/services/ocr_extraction.py`**: `process_document_ocr()` (regex + NER field extraction)
- **`app/services/page_splitter.py`**: `split_pdf_bundle()` via PyMuPDF
- **`app/ocr_pipeline/tasks.py`**: `process_ocr_document_async` Celery task persisting
  `OCRDocument` + `LabTestParameter`
- **Tests**: `tests/test_ocr_extraction.py` — **14/14 pass**

#### Phase 16 — Backup & Export
- **`export_case_as_json()` / `export_case_as_zip()` / `import_case_from_json()`** in
  `app/case_file_generator/services.py` + 3 routes in `routes.py`
- **Celery beat** `daily-db-snapshot` at midnight UTC + settings backup/restore UI;
  `tests/test_case_backup.py` — **14 pass**

#### Phase 10 — Fuzzy Search
- **Fuzzy search fallback** via `rapidfuzz` (`fuzzy_search_fallback()`) with `<mark>` snippet
  highlighting, `fuzzy` API/UI toggle, and `rapidfuzz>=3.0.0` + `numpy` declared in
  `pyproject.toml`; `tests/test_search.py` — **56 pass**

#### Deepening Tasks D1–D5
- **`CaseResolver`** (`app/shared/case_resolver.py`): `ResolvedCase` disambiguation of
  CaseFile vs Adjudication IDs (D1)
- **`DocumentSaveCoordinator`** (`app/services/document_lifecycle.py`): `save()` encapsulating
  save/version/audit (D2)
- **`PDFAssemblyEngine`** (`app/pdf_assembly/engine.py`): `assemble()`, `post_process()`,
  `embed_photos()`, `generate_from_html()` (D3)
- **`InspectionPhotoService`** (`app/inspection/photo_service.py`): upload/verify/stamp (D4)
- **`DocumentCaseManager`** (`app/shared/document_case_manager.py`): parameterized CRUD,
  rendering, document generation (D5)

#### Priority 6 — Infrastructure
- **PostgreSQL migration** (Alembic migrations incl. `add_rbac_and_comment_tables`,
  `add_timeline_event_table`, `add_entity_relationship_tables`, `add_ocr_pipeline_models`)
- **CI** `test-postgres` job in `validation.yml`; multi-stage `Dockerfile` +
  `docker-compose.yml`; `.gitignore` fix to ship `app/models/`
- **OpenAPI/Swagger** at `/apidocs/` via `flasgger`; **structured JSON logging** via `structlog`
- **`GET /health`** public probe endpoint

### Changed
- **Eager-loading & N+1 fixes**: `load_only` column trimming in
  `DocumentCaseManager._list_cases_query()`, `lazy="selectin"` on `Bill.samples` + `bills`
  backref, `distinct()` on the evidence tag-cloud query
- **`app/__init__.py`**: corrected Priority 7 config indentation; registered `food_cell_bp`
  (`/food-cell`) and `timeline_bp` (`/timeline`) which were previously never wired — both
  blueprint template folders now enter Jinja's search path (fixes `TemplateNotFound`);
  QStash `ENABLE_AIRTABLE_SYNC` / `ENABLE_EXCEL_SYNC` / `ENABLE_BACKUP_SCHEDULE` flags
- **Model splitting**: monolithic `app/models.py` split into `app/models/` package
  (`auth.py`, `document.py`, `inspection.py`, `issue.py`, `billing.py`, `config.py`,
  `food_cell.py`)
- **`app/inspection/routes.py`** (1077 lines) modularized into `routes/` package
  (`inspection_routes`, `lookup_routes`, `photo_routes`, `derived_views`)
- **`datetime.utcnow()` → `datetime.now(timezone.utc)`** across the codebase;
  `Model.query.get()` → `db.session.get()`; `db.get_engine()` → `db.engines['default']`
- **Dependabot**: applied all 14 open dependency-update PRs (pytest, black, pip-audit,
  py-spy, bandit, safety, setuptools, types-pyyaml, pytest-cov, pytest-xdist, GitHub Actions)
- **`run_task()` webhook**: aligned env-var check with `qstash_configured()` (all 4 vars)
  and narrowed the `record` type in `task_status()`

### Fixed

#### Security
- **S7 — TLS certificate verification** for KMC license lookup: removed insecure SSL settings
  (`check_hostname=False`, `verify_mode=CERT_NONE`) (P0)
- **S9a — Concurrency guard**: fixed the one-line inspection-PUT bug (`409` was passed inside
  `jsonify()` → returned HTTP 200); now returns `jsonify({...}), 409`;
  `tests/test_concurrency_inspection.py` — **4/4 pass**
- **S6d — Inverted `Expired_item` rule**: `Expired_item` moved to a positive-flag set so
  `"yes"` (non-compliant) triggers Section 55 instead of the compliant default `"no"`

#### Environment / Webhook
- **ENV-9a**: aligned QStash webhook receiver env-var check with `/health`
  (`qstash_configured()`); **ENV-9b**: `clock_tolerance=5` passed to `Receiver.verify()`
  (prevents valid-webhook 401s from clock skew); **ENV-9d**: added the QStash **DLQ gap fix**
  — `failure_callback` now points to `POST /tasks/failed/<task_name>` which verifies the
  Upstash-Signature, marks the Redis status `"failed"` with the error, and logs for operators.
  `tests/test_qstash_webhook.py` — **16/16 pass**
- **ENV-4**: `food_cell_bp` registration resolved the `TemplateNotFound` for the DO intimation
  template (no template was missing — the blueprint was never registered)

### Performance
- **Performance Quick Wins 7/7**: SQLAlchemy connection pooling, FSO `@lru_cache`, Jinja2
  `FileSystemBytecodeCache`, Flask-Compress, `GET /health`, DB indexes, eager loading

### Cleanup / Refactor
- **S6a–c**: removed legacy root-level `suggester.py` + `sections_data.py`;
  wired canonical `app/utils/sections_data.py` into the suggester as the single source of
  section IDs
- Removed singleton pattern from `app/services/legal_engine.py`; untracked ~70MB of CSV data
  from the repository index; deleted 22 stale fully-merged feature/dependabot branches
- Consolidated AI planning docs into `agents.md`, `plan.md`, `task.md`

### Docs
- `agents.md`, `plan.md`, `task.md`, `CHANGELOG.md`, `README.md` updated to reflect all
  implemented phases and status

## [1.0.0] - Initial Release

### Added

- Modular Flask blueprints for:
  - Inspection module
  - Sample module
  - Adjudication module
  - Case file generator
  - Bill generator
  - Billing module
  - FBO issue management
  - Settings
- OCR pipeline with Celery
- Photo evidence system with geo-verification
- Google Sheets integration
- State machine for FBO issues
- Canonical key system for cross-module consistency
- Derived context helpers
- Alembic database migrations
- Comprehensive test suite

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 1.0.0 | 2026-01-01 | Initial release |
| 1.0.1 | 2026-07-26 | Security updates (authentication, CSRF, CSP, TLS fix) |
| Unreleased | 2026-08-07 | Priority 7 (Airtable + Excel redundancy), Phase 21 (Food Cell DO Intimation), Phase 13 (Timeline + Gantt), Phase A (OCR), Phase 16 (Backup/Export), Phase 10 (Fuzzy search), Deepening D1–D5, S9a concurrency guard, Priority 6 infra, Performance Quick Wins 7/7, ENV-9 webhook fixes |

---

For older versions, see the git history.
