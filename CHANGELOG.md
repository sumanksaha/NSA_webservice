# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> Status: Phases 0–16, 20, 21, Phase A + OCR Phases B–E, Deepening D1–D5, S9a, Priority 6/7,
> RAG Phases 1–5, Multi-Domain Phase 1, Evaluation Framework, Benchmark v1.0, Rust PyO3,
> Remote Inference (Modal), LangGraph Agent Pipeline + M5, FastAPI Gateway, and the Config
> seam are implemented and verified (~1,900 tests). **CI/CD gates G1–G14 complete
> (2026-08-23) — deploy gating, staging env, pre-deploy migrations, health check, full
> security blocking (Bandit+Safety+pip-audit), coverage gate, Docker ASGI path, release
> automation, Dependabot, workflow hygiene, ce-v2 dispatch-only gate, env parity, deploy
> serialization, dev-dep scanning — `tests/test_cicd_gates.py` 46/46 pass.** Pending:
> Phase 17 remainder (Supabase bridge, conflict resolution, sync-status UI), Phase 18
> (~30% — RBAC decorator, comments, role assignment), Phase 19, Rust Parts 1.6+ / 2–5,
> CE-v2 retrain.

### Added (2026-08-23)

#### Priority 5 — Cloudinary Testing & Hardening

- **`CLOUDINARY_URL` shorthand**: the single `cloudinary://<api_key>:<api_secret>@<cloud_name>`
  variable is now honoured (`_parse_cloudinary_url` / `_cloudinary_credentials`); it wins over
  the three discrete variables, and a _malformed_ URL falls back to them instead of
  hard-disabling the backend.
- **Network retries**: upload/destroy SDK calls run through a tenacity exponential-backoff
  policy (3 attempts, 0.5 s→4 s, transient ConnectionError/Timeout only — non-transient
  errors fail fast without burning the budget). An exhausted upload degrades to `None`
  so `upload_photo` falls back to R2/B2 instead of raising.
- **`GET /health/cloudinary`** (public, auth-exempt like `/health`): reports credential
  source, cloud name, SDK availability, and a best-effort API reachability ping; always
  200 so monitoring can distinguish not-configured from configured-but-broken.
- Tests: `tests/test_storage_cloudinary.py` — 30 tests (URL parsing, credential
  precedence, retry semantics, public-id extraction, health probe), all mocked, no network.
- Dependency: `tenacity>=8.2.0` added to core dependencies.

#### Environment debt closed (task.md)

- **ENV-6 (cv2/OpenCV)**: RESOLVED — `opencv-python-headless` was already declared under
  the `[ocr]` extra; the environment now has OpenCV installed and `tests/test_ocr_pipeline.py`
  passes 24/24. `ImagePreprocessor.process()` still degrades gracefully (no-op) when cv2
  is absent.
- **ENV-7 (Dependabot staleness)**: `.github/dependabot.yml` now sets `rebase-strategy: "all"`
  (plus the existing `target-branch: main`) so version-bump PRs are rebased onto main every
  run instead of piling up stale branches. Existing stale branches need a one-time manual
  close/rebase.
- **ENV-8 (Python floor vs reality)**: `requires-python` relaxed to `">=3.11"` with a 3.11
  classifier added and black/ruff `target-version` set to `py311` — the entire suite
  (~1,900 tests) verifiably runs on 3.11.15 locally, and the codebase uses no 3.12-only
  syntax (verified: zero PEP 695 usages). CI keeps running 3.12.

### Changed (2026-08-23)

#### CI/CD gate package (docs/CI_CD_RESEARCH.md §4–§5)

- **Deploys are now gated** (G1/G13): `render.yaml` sets `autoDeploy: false` on both
  services; the rewritten `.github/workflows/deploy.yml` triggers via `workflow_run`
  on a _successful_ "Repository Validation" run on main, curls the Render deploy hook
  pinned to the validated SHA (`?ref=`), serializes deploys with a `render-deploy`
  concurrency group, and verifies `/health` post-deploy. Manual deploys remain via
  `workflow_dispatch`. **Setup required:** create the Render Deploy Hook and store it
  as the `RENDER_DEPLOY_HOOK_URL` repo secret (checklist in deploy.yml header).
- **`healthCheckPath: /health`** on the web service (G4): Render now probes real DB
  connectivity before traffic cutover and cancels failed deploys automatically.
- **Migrations moved to `preDeployCommand`** (G3): `flask db upgrade` runs in Render's
  pre-deploy step (build → pre-deploy → start) so a failing migration cancels the deploy
  before cutover instead of killing boot; startCommand is now just uvicorn. Verify the
  instance plan supports pre-deploy commands; fallback documented in render.yaml.
- **Hygiene batch** (G10/G11): ce-v2 `real-gate` job restricted to `workflow_dispatch`
  (stops ~40-min torch runs on qualifying pushes); lint.yml gains a cancel-in-progress
  concurrency group, aligns to checkout@v7/setup-python@v7/`ruff>=0.16.3`
  (pre-commit parity) and pins ubuntu-24.04; pip-audit.yml gains a concurrency group.

#### G2 — Staging environment

- **Staging deploy leg** (deploy.yml): on validation success, a `deploy_staging` job
  targets the `staging` GitHub environment (open, no required reviewer) and triggers
  the staging Render deploy hook (`RENDER_STAGING_DEPLOY_HOOK_URL`). Production deploy
  (`needs: deploy_staging`) waits for staging to succeed before cutover.
  **Setup required:** create the Render Staging Deploy Hook and store as
  `RENDER_STAGING_DEPLOY_HOOK_URL`; create a `staging` GitHub environment
  (Settings → Environments → New environment).
- **Staging web service** (render.yaml): `food-adjudication-portal-staging` deploys
  from the `upgradation` branch (`branch: upgradation`), `autoDeploy: false`,
  with `healthCheckPath: /health`, `preDeployCommand: flask db upgrade`, and the
  shared `shared-secrets` envVarGroup. Separate free-tier staging database
  (`nsa-webservice-staging-db`) avoids polluting prod data.

#### G8 — Release automation activated

- **release.yml is now active**: `workflow_dispatch` (create tag + release from a
  chosen branch) and `push: tags: "v*.*.*"` (create release when a tag is pushed) both
  supported. Uses `softprops/action-gh-release@v2` with `generate_release_notes: true`.
  `if: false` placeholder removed; `permissions: contents: write, discussions: write`
  preserved. Version is declared by the tag name — `pyproject.toml` version is not
  auto-bumped by this workflow.

#### G6 — Coverage gate added

- `fail_under = 60` declared in `[tool.coverage.report]` (pyproject.toml). Coverage is
  measured only in the slow-test shard (`--cov=app`); the threshold is conservative and
  should be ratcheted up after first CI run.

#### G14 — Dev dependency scanning

- pip-audit now scans `requirements-dev.txt` alongside `requirements.txt` in both
  `validation.yml` (blocking) and `pip-audit.yml` (blocking, weekly schedule).

#### G5 — Full security scan blocking

- Bandit and Safety in `validation.yml` security job are now **blocking** (removed
  `continue-on-error: true` and `|| true`). Bandit runs with `--confidence HIGH
--severity HIGH -s B101,B311,B324` (only HIGH/HIGH findings, known false positives
  skipped). Safety scans `requirements.txt` with `--full-report`. The SARIF _upload_
  step remains `if: always()` / `continue-on-error` so SARIF reporting degrades
  gracefully without masking scan failures.

#### G7 — Docker path wired

- Dockerfile gains `ENTRYPOINT ["./docker-entrypoint.sh"]` (was missing — entrypoint
  migrations were unreachable); `CMD` changed from `gunicorn app:app` (WSGI) to
  `uvicorn asgi:app` (ASGI) to match render.yaml; `chmod +x` added for Windows checkout.
  docker-compose.yml web service aligned to `uvicorn asgi:app`; `FLASK_APP` corrected
  to `app:create_app` (factory reference). docker-entrypoint.sh now fails loudly on
  migration errors instead of swallowed `|| true` warnings.

#### Regression shield

- `tests/test_cicd_gates.py` — 46 structural tests pinning G1–G14
  invariants directly against YAML/TOML files. Runs in the `test-fast` CI job
  (`-m "not slow"`), zero external dependencies.

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

- **Phase B Review Workflow**: app/ocr_extraction/ blueprint (GET /ocr/documents,
  GET /ocr/documents/<id>/review, POST .../corrections) - manual edits write
  OCRCorrection rows and update extracted_json; corrections disagreeing with
  lab-report values open ConflictLog entries. app/conflict_resolution/ queue
  at /conflict-resolution/ resolves them (chosen value applied as a correction).
- **Phase C Autopopulation**: app/autopopulation/ - verified-record builder
  (Sample + reviewed OCR + lab params), per-consumer prefill bundles via
  MAPPINGS (GET /autopopulation/prefill/<sample_id>), and idempotent
  auto-drafting of FBO issues for non-conforming lab reports.
- **Phase D Feedback Loop**: app/feedback_dashboard/ - per-field accuracy from
  correction history + few-shot example store (`refresh_few_shot_examples` Celery
  task / dashboard trigger).
- **Phase E Bulk Upload**: POST /ocr/bulk-upload - ZIP batches processed per-PDF
  (async when Celery is configured, sync fallback otherwise), SHA-256 dedupe,
  per-file failure isolation.
- **Bug fix**: EasyOCR plugin crashed on every extraction
  (OCRResult has no attribute ocr_engine_used) - extractions silently returned
  empty text; now falls back to the engine name.
- Shared persistence extracted to app/ocr_pipeline/persistence.py so the async
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

| Version    | Date       | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ---------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0.0      | 2026-01-01 | Initial release                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 1.0.1      | 2026-07-26 | Security updates (authentication, CSRF, CSP, TLS fix)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Unreleased | 2026-08-23 | CI/CD gates G1–G14 complete (deploy gating, staging env, pre-deploy migrations, health check, full security blocking Bandit+Safety+pip-audit, coverage gate fail_under=60, Docker ASGI path, release automation, Dependabot, workflow hygiene, ce-v2 dispatch-only gate, env parity, deploy serialization, dev-dep scanning — 46 test_cicd_gates.py tests). Cloudinary hardening (CLOUDINARY_URL parsing, tenacity retries, /health/cloudinary). ENV-6/7/8 resolved. OCR Phases B–E complete (45 tests). Phase 15 Analytics complete (15 tests). RAG UI gaps 1–8 resolved. Rust Part 1 scaffold complete. |

---

For older versions, see the git history.
