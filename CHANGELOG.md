# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> Status: Phases 0–10, Deepening D1–D5, Infrastructure, S9a, Phase 16, Phase A, Phase 13,
> Phase 21, and Priority 7 are implemented and verified. Phases 11–12, 14–15, 17–20 pending.

### Changed (2026-08-22 architecture deepening)

- **Bill issuance is atomic** (ADR-0001): the `Bill` row, `billed` flags, and
  `BillSample` links now commit in ONE transaction — no Bill can exist without
  its Samples marked billed. PDF-dispatch failures return the persisted
  `bill_id` in the error body so the UI warns against duplicate re-submission.
- **Retrieval composition root** (`app/rag/retrieval/factory.py`): the dense/
  sparse/reranker stack is built in one module; `run_retrieval_pipeline` and the
  evaluation harnesses delegate to it (eliminates the wrong-collection drift bug class).
- **BackupTarget registry**: redundant backup targets (Sheets/Airtable/Excel/full
  archive) are declared adapters; a new target is one registry row. The restore
  engine is parameterized by target with one canonical module→worksheet table.
- **Config seam completion**: all remaining hand-rolled `os.environ` resolvers in
  `create_app()` removed; every declared flag is seeded (env or default); QStash
  schedules register via the enumerable `ScheduledJobs` registry.
- **ASGI fix**: `/api/v2/search/reindex` no longer 500s on its audit write
  (Flask app context now provided via `app.api.deps.get_flask_app`).

### Added (2026-08-22)

#### OCR Pipeline Phases B-E (Extraction -> Review -> Autopopulation -> Feedback)

- **Phase B Review Workflow**: pp/ocr_extraction/ blueprint (GET /ocr/documents,
  GET /ocr/documents/<id>/review, POST .../corrections) - manual edits write
  OCRCorrection rows and update extracted_json; corrections disagreeing with
  lab-report values open ConflictLog entries. pp/conflict_resolution/ queue
  at /conflict-resolution/ resolves them (chosen value applied as a correction).
- **Phase C Autopopulation**: pp/autopopulation/ - verified-record builder
  (Sample + reviewed OCR + lab params), per-consumer prefill bundles via
  MAPPINGS (GET /autopopulation/prefill/<sample_id>), and idempotent
  auto-drafting of FBO issues for non-conforming lab reports.
- **Phase D Feedback Loop**: pp/feedback_dashboard/ - per-field accuracy from
  correction history + few-shot example store (
efresh_few_shot_examples
  Celery task / dashboard trigger).
- **Phase E Bulk Upload**: POST /ocr/bulk-upload - ZIP batches processed per-PDF
  (async when Celery is configured, sync fallback otherwise), SHA-256 dedupe,
  per-file failure isolation.
- **Bug fix**: EasyOCR plugin crashed on every extraction
  (OCRResult has no attribute ocr_engine_used) - extractions silently returned
  empty text; now falls back to the engine name.
- Shared persistence extracted to pp/ocr_pipeline/persistence.py so the async
  task and bulk path cannot drift. Tests: review (14), autopopulation (14),
  feedback (8), bulk upload (9); Phase A suite 14/14 still green.


#### Phase 15 — Analytics Dashboard

- **`app/analytics/`** blueprint at `/analytics`: interactive dashboard
  (Chart.js charts + Leaflet FBO location map) fed by a single
  `GET /analytics/api/metrics` JSON endpoint — summary counts across all six
  major tables, monthly CaseFile/Adjudication trends, inspection compliance,
  sample pipeline by billed state, legal provisions cited, FSO activity,
  FBO issue states, evidence types, and geo coordinates.
- Lightweight aggregate SQL (group-by + count only, no ORM hydration).
- Nav link in `base.html`; auth-gated by the global login gate.
- `tests/test_analytics.py` — **15 tests pass**.

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

### Added

#### RAG Phase 2–5, Multi-Domain, KG, Evaluate, Benchmark, Rust

- **Phase 2 — Grounded Generation** (`app/rag/generation/`): `ContextBuilder`, `PromptTemplate`, `GroundedLLMClient`, `CitationTracker`, `ResponseSanitizer`, `GroundedGenerationService`, `GenerationLogger`; `run_generation_pipeline` / `generate_task` + `POST /api/rag/generate` (stub-LLM mode, no Qdrant/network required); `tests/test_rag_generation.py` — **40/40 pass**
- **Phase 3 — Hallucination Detection** (`app/rag/verification/`): `ClaimExtractor`, `EvidenceVerifier`, `CitationValidator`, `GroundednessScorer`, `HallucinationDetector`, `TokenCounter`; hash-chained audit; 44 tests across 3 modules — all pass
- **Phase 4 — Evaluation** (`app/rag/evaluation/`): 6 metrics, `EvalRunner`, `EvalStorage`, `EvalReport`/`EvalSummary`; `run_evaluate` / `evaluate_task` + `POST /api/rag/eval`; `tests/test_eval_framework.py` (39) + `tests/test_eval_batch.py` (10) — all pass
- **Phase 5 — Resilient Integration** (`app/rag/resilient.py`): `ResilientRAGPipeline` circuit breaker (closed→open→half-open→closed) + fallback; `POST /api/rag/query` full pipeline route; token counting in `RAGQueryLog.context_length`; 29 tests — all pass
- **Agent A §6.2**: corpus ingestion E2E (8), batch ingestion (5), reindexing (3), benchmarks (11) — 27 tests, all pass
- **Multi-Domain Phase 1**: `app/rag/legal_sections.py` (BNS 1–358), `app/rag/collections.py` (`criminal_legal_768` etc.), `act_name` payload, act-aware crossrefs, domain prompts; `tests/test_multidomain_phase1.py` — **37/37 pass**
- **Knowledge Graph — Option B + Semantic + Hybrid**: `kg/` (12 modules), `scripts/build_kg_corpus.py` (58 instruments, 1,861 provisions, 27,343 chunks), `scripts/enrich_kg_semantics.py` (751 evidence-backed edges), and 6 additional scripts; `NEO4J_ALLOW_WRITE` fail-closed guard; `RAG_KG_EXPANSION`/`RAG_KG_FUSION`/`RAG_KG_MAX_PROVISIONS` env vars; 49+17 KG tests — all pass
- **FSSAI Re-ingest (P1-4)**: `scripts/reingest_fssai_from_db.py` (identity-preserving, 12,819 chunks), `scripts/export_fssai_backup.py` (rollback); `tests/test_reingest_fssai.py` — **15/15 pass**
- **Evaluation Framework** (`evaluation/`): 28 modules — retrieval arms A–G, metrics, ceiling analysis, root-cause analysis, batch orchestration
- **Benchmark v1.0** (`benchmark/`): 150-question frozen multi-domain JSONL with gold provisions, sources, rubric, review-conflict report
- **Rust PyO3 Normalizers** (`rust/`): 4 modules with deterministic legal-text normalizers; `docs/RUST_REFACTORING_EVALUATION.md`; `tests/test_rust_normalizers.py`
- **`app/food_cell/renderer.py`**: DO intimation HTML/PDF renderer service
- **`app/services/sync_orchestrator.py`**: Multi-target sync orchestration (Sheets + Airtable + Excel)
- **`app/shared/case_query_service.py`**: Shared case query resolution service
- **`docs/`**: DEEPENING.md, FSSAI_REINGEST_PLAN.md, INGESTION_READINESS.md, MULTIDOMAIN_INTEGRATION.md, RUST_REFACTORING_EVALUATION.md
- **Audit reports**: CORPUS_IDENTITY_REPORT.md, KG_READINESS_AUDIT_POST_REBUILD.md, KG_SEMANTIC_REMEDIATION_REPORT.md, NEO4J_QDRANT_AUDIT_REPORT.md

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

| Version    | Date       | Description                                                                                                                                                                                                                                                                            |
| ---------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0.0      | 2026-01-01 | Initial release                                                                                                                                                                                                                                                                        |
| 1.0.1      | 2026-07-26 | Security updates (authentication, CSRF, CSP, TLS fix)                                                                                                                                                                                                                                  |
| Unreleased | 2026-08-07 | Priority 7 (Airtable + Excel redundancy), Phase 21 (Food Cell DO Intimation), Phase 13 (Timeline + Gantt), Phase A (OCR), Phase 16 (Backup/Export), Phase 10 (Fuzzy search), Deepening D1–D5, S9a concurrency guard, Priority 6 infra, Performance Quick Wins 7/7, ENV-9 webhook fixes |

---

For older versions, see the git history.
