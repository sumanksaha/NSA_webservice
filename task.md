# Task List & Detailed Implementation Plan — NSA Webservice

> **Status:** ✅ Deepening Tasks D1–D5, S6a–d, S7, S2, S10a–c, Priority 6 infra, S9a (concurrency guard — fully fixed), Phase 16 (backup/export/import), Phase A (OCR pipeline foundation), Phase 13 (timeline engine + Gantt UI + global case-picker + entry points — fully wired & verified 2026-08-07, 21/21 tests), **Phase 21 (Food Cell DO Intimation — 15/15 tests)**, **Phase 12 (Legal Validation Engine — verified 2026-08-07, `tests/test_validation.py` 46/46 pass)**, **ENV-1 + ENV-4 resolved (2026-08-07)**, **Priority 7 (Multi-Target Sheets Redundancy — Airtable + MS Excel ✅ Complete, 43 tests pass)**, and **7/7** Performance Quick Wins all implemented & verified. Phases 11, 14–15, 17, 19–20 pending; **Phase 18 partial** (models + migration + admin UI done; `@role_required` decorator, comment API/UI, and `tests/test_rbac.py` pending); ENV-2/3/5/6/7/8 open.

> **Purpose:** Consolidated, actionable, highly detailed implementation plan and TODO list for AI agents and developers. Organized by priority with checkboxes, explicit file targets, data schemas, function signatures, routes, acceptance criteria, and testing strategies.
> **Sources & Alignment:** `SECURITY_TODO.md`, `ALL_TODO_MERGED.md`, `ROADMAP_ALIGNMENT_REPORT.md`, `ENGINEERING_ASSESSMENT.md`, `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md`, `REFACTORING_PLAN.md`.

---

## Rust Refactoring — 5-Part Implementation Plan (created 2026-08-12)

> **Source:** `docs/RUST_REFACTORING_EVALUATION.md`. Strategy: **PyO3 + maturin
> extension modules** compiled to a Python-importable `nsa_rust` module, with a
> **pure-Python fallback** always intact (graceful degradation, matching the
> project's existing pattern). The 1,757-test suite must pass unchanged.
> **Ordering rationale:** Part 1 is the *easiest* (pure string functions, clean
> boundary, 45-test parity net) — done first to prove the build+pipeline end to
> end with low risk. Part 5 (Legal Engine) is the *highest ROI* but the *hardest*
> port, so it is last. No Rust toolchain was present on 2026-08-12; it was
> installed (rustup → rustc/cargo 1.97.1) and `maturin 1.14.1` added to the venv
> as the first action of Part 1.

### Part 1 — Rust Toolchain + Document Cleaner Port (`nsa_rust::cleaner`) — IN PROGRESS

**Status:** Step 1.1 (toolchain) ✅ DONE. Step 1.2 (workspace scaffold) ✅ DONE.
Step 1.3 (normalizers Rust port) ✅ DONE. Step 1.4 (removers + `_should_preserve`
Rust port in `rust/src/removers.rs`) ✅ DONE. Step 1.5 (Python fallback wiring in
`pipeline.py::_run_removers` / `_run_normalizers` / Phase-2 OCR) ✅ DONE.
Step 1.6 (maturin build) — **blocked on Windows 10 SDK** (see build note below);
VS 2022 Build Tools (VCTools) was installed, but the SDK requires admin elevation.
Step 1.7 (parity) — parity test `tests/test_rust_normalizers.py` extended to cover
normalizers + removers + OCR + full `clean()`; it skips until the extension is
built. Steps 1.6/1.7 pending.

**Build note (2026-08-12):** PyO3 extensions on Windows must be compiled with the
MSVC linker (`link.exe`) against the MSVC-built CPython. VS 2022 Build Tools
(VCTools workload) was installed — `link.exe`/`cl.exe` are now present — but
linking also needs the **Windows 10 SDK** (`Windows Kits\10\Lib\…\kernel32.lib`
etc.), which is **not installed and cannot be installed non-interactively**:
`setup.exe --quiet` requires admin elevation (exit code 5007, UAC prompt), which
a headless shell cannot provide. Until the Windows 10 SDK is installed (elevated),
`cargo build`/`maturin build` fail at the link step. Build command once the SDK is
present: `maturin develop --manifest-path rust/Cargo.toml` (or
`maturin build --manifest-path rust/Cargo.toml --release` for a wheel). The
pure-Python fallback keeps `tests/test_document_cleaner.py` green in the
meantime.

- **Goal:** Ship the first PyO3 module accelerating the document cleaner, with
  a Python fallback, verified by the existing 45 `test_document_cleaner.py` tests.
- **Targets:** `app/document_cleaner/` (`normalizers.py` 9 pure `str→str`
  functions + `removers.py` line filters + `pipeline.py::DocumentCleaner.clean()`).
- **Steps:**
  1. ✅ Install Rust toolchain (`rustup` → rustc/cargo 1.97.1) + `maturin` in venv.
  2. ✅ Scaffold `rust/` PyO3 workspace (`Cargo.toml`, `src/lib.rs` `#![pymodule] nsa_rust`).
  3. Port `normalizers.py` → `rust/src/normalizers.rs` (regex `crate` + `unicode-normalization` NFKC + Levenshtein/Indel `fuzz.ratio` for hyphens).
  4. Port `removers.py` + `_should_preserve` → `rust/src/removers.rs`.
  5. Wire `DocumentCleaner.clean()` to try `nsa_rust.clean_document` first, fall back to Python.
  6. Build with `maturin develop --manifest-path rust/Cargo.toml`.
  7. Prove parity: `tests/test_document_cleaner.py` (45) + a Rust↔Python A/B parity test.
- **Acceptance:** ≥3× cleaning throughput; 45/45 tests identical output; Python fallback works when `nsa_rust` is absent.

### Part 2 — Search Fuzzy Helpers Port (`nsa_rust::search_fuzzy`)

- **Goal:** Accelerate the pure helper functions behind fuzzy search.
- **Targets:** `app/search/indexer.py` — `_field_score`, `_find_match_spans`,
  `_snippet_around_matches`, `_apply_marks`, `_expand_to_word` (pure, ~150 LOC).
  The main `fuzzy_search_fallback()` stays in Python (it is DB/ORM-coupled); only
  the pure helpers move to Rust.
- **Steps:** port helpers → build → wire into `fuzzy_search_fallback()` → parity
  vs `tests/test_search.py` (56).
- **Parity risk:** `_field_score` uses rapidfuzz `token_set_ratio` + `partial_ratio`
  — must reproduce both algorithms exactly (see Part 1 note on Indel ratio).
- **Acceptance:** ≥2× fuzzy search; 56/56 tests identical.

### Part 3 — TOC Generator + Cross-Reference Port (`nsa_rust::toc`, `nsa_rust::cross_reference`)

- **Goal:** Accelerate HTML pre-processing for PDF generation.
- **Targets:** `app/toc_generator/engine.py` (293 LOC), `app/cross_reference/engine.py` (495 LOC).
- **Steps:** port both → build → wire into `PDFAssemblyEngine.post_process()` →
  parity vs `tests/test_phase7_toc_generator.py` (37) + `tests/test_cross_reference.py` (27).
- **Acceptance:** ≥2–3× pre-processing; 64/64 tests identical.

### Part 4 — RAG Enrichment + Verification Port (`nsa_rust::enrichment`, `nsa_rust::verification`)

- **Goal:** Accelerate ingestion enrichment and hallucination detection.
- **Targets:** `app/rag/enrichment/deterministic.py`, `entity_extractor.py`,
  `citation_adapter.py`, `crossref_adapter.py`, `metadata_adapter.py`;
  `app/rag/verification/claim_extractor.py`, `evidence_verifier.py`,
  `hallucination_detector.py`, `citation_validator.py`, `token_counter.py`.
- **Steps:** port enrichment (+ ray-pll `rayon` for the evidence-verifier per-pair
  loop) → build → wire into `IngestionPipeline` + `GroundedGenerationService` →
  parity vs 255 enrichment + 48 verification tests.
- **Acceptance:** ≥3× enrichment; ≥5× verification; zero test regressions.

### Part 5 — Legal Paragraph Engine Port (`nsa_rust::legal_engine`) — Highest ROI, hardest

- **Goal:** Replace `legal_paragraph_detection_engine` with a Rust crate exposing
  the same `process_document(text, doc_type) -> list[dict]` API.
- **Targets:** `legal_paragraph_detection_engine/src/` — `TextNormalizer`,
  `ParagraphBoundaryDetector`, `HierarchyDetector`, `ClauseParser`,
  `SectionParser`, `CitationExtractor`, `DocumentTypeClassifier`, and
  `LegalParagraphEngine.process_document`.
- **Steps:** port each sub-module → `nsa_rust::legal_engine::process_document` →
  wire `app/services/legal_engine.py::get_legal_engine()` to prefer Rust, fall
  back to Python → parity vs 282 RAG tests + `legal_paragraph_detection_engine/tests/`.
- **Note:** `_make_citation_pattern` uses `(?<!\w)` lookbehind — the Rust `regex`
  crate lacks lookbehind; use `fancy-regex` or a lookahead-based alternative.
- **Acceptance:** ≥5× chunking throughput; 100% test parity.

---

## Completed Milestones

> Items finished and verified are tracked here so agents can trust implementation status at a glance.

- [x] **Phase 4 — Annexure Replace** (`POST /annexure/<id>/replace` + UI button + 8 tests). Replaces the stored file on an existing annexure in place: re-extracts hash/page-count/OCR/size/MIME, keeps the annexure id + letter so document references stay valid, rejects content-hash duplicates of _other_ annexures (self re-upload allowed), deletes the old file after commit, and audit-logs `ANNEXURE_REPLACED`. Files: `app/annexure/routes.py`, `app/annexure/templates/annexure/index.html`, `tests/test_annexure.py`.
- [x] **PDF Engine Repairs**. Consolidated WeasyPrint import guards (`import_weasyprint()`), header/footer template injection (`_get_default_header_template`, `_get_default_footer_template`, `_apply_headers_footers`), fixed PDF bookmarking and footer insertion (`rfind` instead of `endswith`), detached direct WeasyPrint imports from `tasks.py`. Files: `app/pdf_assembly/__init__.py`, `app/utils/pdf_utils.py`, `app/case_file_generator/tasks.py`.
- [x] **Technical Debt Cleanup**. Removed singleton pattern from `app/services/legal_engine.py`, split `app/models.py` into `app/models/` package (`auth.py`, `document.py`, `inspection.py`, `issue.py`, `billing.py`, `config.py`), modularized `app/inspection/routes.py` (1077 lines) into 4 submodules, updated `datetime.utcnow()` to `datetime.now(timezone.utc)` across 11 occurrences, migrated `Model.query.get()` to `db.session.get()`, updated `db.get_engine()` to `db.engines['default']` in migrations, untracked ~70MB of CSV data files from repository index.
- [x] **Phase 10 — Fuzzy Search Integration & Rapidfuzz Dependency** (`fuzzy_search_fallback()` + `fuzzy` API param + UI toggle + 56 tests). Added `rapidfuzz>=3.0.0` and `numpy>=1.26.0` as declared dependencies in `pyproject.toml`. Implemented `fuzzy_search_fallback()` in `app/search/indexer.py` using `fuzz.token_set_ratio` + `fuzz.partial_ratio` scoring with threshold filtering (default 65.0) and `<mark>`-wrapped snippet highlighting. Updated `search()` to auto-fall-back to fuzzy when FTS5/LIKE yields zero results or when `fuzzy=True`. Updated `app/search/routes.py` to read `fuzzy` query param and return effective `fuzzy` flag + match `score` keys in JSON. Added styled toggle switch (`#fuzzyToggle`) in `app/search/templates/search/index.html`. Verified `app/document_cleaner/normalizers.py` imports `rapidfuzz` cleanly. `pytest tests/test_search.py` passes 56/56 (TestFuzzySearch: 19, TestSearchAPI: 9, TestSearchPage: 2, plus existing FTS5/indexing tests). Lint clean (`ruff check`). Files: `pyproject.toml`, `app/search/indexer.py`, `app/search/routes.py`, `app/search/templates/search/index.html`, `tests/test_search.py`.
- [x] **S7: Scraper TLS Security Fix**. Verified and enforced TLS certificate checking for KMC trade license lookup in `app/utils/lookup.py`. Removed `check_hostname = False` and `verify_mode = ssl.CERT_NONE`. Maintained cipher string `DEFAULT@SECLEVEL=1`. Files: `app/utils/lookup.py`.
- [x] **Priority 6 — Infrastructure & Future Levels** (PostgreSQL Migration + CI + Docker + OpenAPI + Structured Logging). Four Alembic migrations authored & verified clean against head (`add_ocr_pipeline_models`, `add_timeline_event_table`, `add_role_user_role_comment_tables` [Rev `a1b2c3d4e5f6`], `add_entity_relationship_tables` → head); models model==migration-parity confirmed via autogen (zero drift for new objects). SQLAlchemy connection pooling in `create_app`; CI `test-postgres` job in `validation.yml`. `.gitignore` fix (`models/`→`/models/`) so `app/models/` ships; `migrations/env.py` `include_object` hook suppressing destructive FTS5 virtual-table auto-drops. Multi-stage `Dockerfile` + `docker-compose.yml` + `.dockerignore` (`docker compose config` validates). `flasgger` Swagger UI at `/apidocs/`; `structlog` structured logging via `app/utils/logging.py::setup_logging` (JSON prod / console dev + stdlib bridge); `GET /health` endpoint. Verified: app boots, `ruff` clean on changed files, 86 targeted tests pass. (Celery worker was already deployed via `render.yaml` — no change needed.)
- [x] **S9a Concurrency Guard — full fix (2026-08-06).** The one-line inspection-PUT bug (`409` passed _inside_ `jsonify()` → HTTP 200) is fixed: `app/inspection/routes/inspection_routes.py` now returns `jsonify({...}), 409`. `tests/test_concurrency_inspection.py` — 4/4 pass.
- [x] **Eager Loading Optimization — Perf Quick Win #5 (2026-08-06).** `load_only` column trimming on `DocumentCaseManager._list_cases_query()` (wide-table `/cases` JSON endpoints), `lazy="selectin"` on `Bill.samples` + `bills` backref, `distinct()` on the evidence tag-cloud query. All 7 Performance Quick Wins now complete.
- [x] **Phase A — OCR Pipeline Foundation (2026-08-06).** `app/services/ocr_extraction.py`, `app/services/page_splitter.py`, `app/ocr_pipeline/tasks.py` implemented and polished (clean `db.session` import replacing a `__import__` hack; single `to_flat_dict()` call). `tests/test_ocr_extraction.py` — 14/14 pass.
- [x] **Phase 13 — Timeline Engine & Gantt UI (completed 2026-08-07).** New `app/timeline/` blueprint: `TimelineEngine` extracts milestones from CaseFile/Adjudication/Inspection/Sample/Annexure/Evidence dates, persists case_file events to `timeline_event` (idempotent), validates chronological sequences, and serves a vertical-timeline + Gantt page with document links. **Access:** global nav case-picker (keyboard-navigable search dropdown with `<mark>` highlighting + server-injected URL bases) in `base.html`, "Case Timelines" panels on both index pages, document-editor button, Timeline buttons in search results / evidence / annexure / inspection (list + detail, when adjudicated) / audit log (CaseFile/Adjudication rows) / version-control history / sample list (batched sample→case map) + `case_id`/`timeline_url` on the sample detail JSON. Also wired the orphaned `app/audit` routes (audit log viewer was unreachable) and fixed stale `edit_case_file`/`edit_adjudication` url_for names → `edit_case`. **Completion note (2026-08-07):** the blueprint was never registered in `create_app()` and the UI entry points were never committed — both were built + wired on this date (see ENV-1 below). `tests/test_timeline.py` — **21/21 pass**; route-collision + app-boot regression green.
- [x] **Phase 21 — Food Cell DO Intimation (completed 2026-08-07).** New `app/food_cell/` blueprint (`/food-cell`): DO Intimation PDF download / HTML view / regenerate / status routes; `DoIntimation` model + `food_cell_forwarded` on `Sample` (`add_food_cell_do_intimation` migration); HTML→PDF via WeasyPrint with stub fallback; Celery `send_do_intimation` task wired post-save in `app/sample/routes.py::create_sample()`; best-effort sync to Sheets + Airtable + Excel. **Completion note (2026-08-07):** `food_cell_bp` was never registered in `create_app()`, so the blueprint template folder never entered Jinja's search path (see ENV-4 below); registration added on this date. `tests/test_food_cell_do_intimation.py` — **15/15 pass**.
- [x] **Phase 12 — Legal Validation Engine (status verified 2026-08-07; code on disk since commit `c020fcc`).** `app/validation/` package: `rules.py` (7 rule classes — MandatorySections, SignaturePlaceholder, NumberingFormat, StatutoryReference, DuplicateEvidence, TimelineConsistency, DocumentCompleteness — + `ValidationResult`/`BaseRule`/`RULES` registry), `engine.py` (`ValidationEngine.validate_case()` — `CaseResolver` integration, score `clamp(100 − 15·errors − 5·warnings, 0, 100)` + grades), `routes.py` (`POST /validation/validate` with 400/404 handling; `GET /validation/case/<id>?kind=`), blueprint registered in `app/__init__.py` (import L365, register L389). `tests/test_validation.py` — **46/46 pass** (verified live 2026-08-07). **UI integration (plan step 5) — also done (verified 2026-08-07):** the "Run Legal Validation" button + validation drawer live in the workbench template `app/legal_analysis/templates/legal_analysis/index.html` (client-side JS POSTing to `/validation/validate`; no server-side route needed). Verified end-to-end: `GET /legal/` renders the button/case-id input/case-type select/drawer and a live case scores 100/"Ready" via the API. Covered by `TestValidationUIIntegration` (2 tests). **Entry points extended (2026-08-07):** the drawer logic was extracted into the shared `app/static/js/validation_drawer.js` module (single source of truth — the workbench's inline JS now calls `ValidationDrawer.initForm`, and per-row "Validate" buttons on the case-file (`/case_file_generator/`) and adjudication (`/adjudication/`) index pages call `ValidationDrawer.initRowButtons`), covered by `TestValidationUIEntryPoints` (4 tests — both index pages + both editors). **Correction note (2026-08-07):** an earlier evaluation reported the UI missing because it inspected only `app/legal_analysis/routes.py`; the template has carried the UI since commit `c020fcc`.

### 📌 Suggested Next 3 Steps (updated 2026-08-09)

> Highest future impact, smallest effort — in this order. **Phases 12, 13, 14, 16, A are now ✅ DONE** (2026-08-04 through 2026-08-09); the list was re-rolled.

1. **Phase 15 — Analytics Dashboard** (`app/analytics/`). Aggregate SQL over `CaseFile`/`Adjudication`/`Inspection`/`Sample`/`FboIssue` + Chart.js/Leaflet dashboard. Natural consumer of the new `selectin`/`load_only` query patterns. Deliverable: `GET /analytics/api/metrics` + `tests/test_analytics.py`. **Verified 2026-08-07: not started** — no `app/analytics/` package, no routes, no nav link, no tests.
2. **Phase 18 — Multi-User RBAC & Comments** (finish the remaining ~70%). `Role`/`user_roles`/`Comment` models + migration and the `is_admin`-based admin UI (`/auth/users`) already exist; only the `@role_required` decorator, comment API/UI, role assignment in the admin UI, and `tests/test_rbac.py` remain. Deliverable: `app/decorators.py` + comment routes + `tests/test_rbac.py`.
3. **Phase 19 — AI Case Intelligence** (`app/case_intelligence/`). Synthesize Legal Validation Engine (Phase 12) outputs + AI LLM (Phase 11) to produce a composite Case Readiness Score (0–100), evidence strength index, and allegation-to-evidence matrix. Deliverable: `GET /case_intelligence/<id>` + `tests/test_case_intelligence.py`. **Not started** — no `app/case_intelligence/` package, no routes, no tests.

---

## Priority 0 — Security Risk (MUST FIX)

### S6a: Remove Legacy Root-Level `suggester.py` — ✅ DONE (2026-08-04)

- **Goal & Rationale:** Eliminated the duplicate rule suggester module that resided at project root (`suggester.py`). The authoritative, maintained implementation lives in `app/utils/suggester.py`.
- **Changes Applied:**
    - Backported root's module docstring + PEP 484 type annotations (`sections: list[str]`, `reasoning: dict[str, str]`) into `app/utils/suggester.py`.
    - Deleted root `suggester.py` + tracked `__pycache__/suggester.cpython-313.pyc`.
    - Verified only live consumer is `app/adjudication/routes.py` (imports `from app.utils.suggester import suggest_sections`) — **no code change needed**. The previously listed consumers `app/inspection/routes/lookup_routes.py` and `app/legal_analysis/routes.py` **do not import suggester** (task.md list was stale).

### S6b: Remove Legacy Root-Level `sections_data.py` — ✅ DONE (2026-08-04)

- **Goal & Rationale:** Eliminated the duplicate section-data module at project root (`sections_data.py`). Canonical: `app/utils/sections_data.py` (pathlib-based, CWD-independent, typed). Neither version had any live importer.
- **Changes Applied:**
    - Deleted root `sections_data.py` + tracked `__pycache__/sections_data.cpython-313.pyc` and `__pycache__/app.cpython-313.pyc`.
    - **Kept:** `fss_sections.md` (statutory text data), `fso_list.md` (FSO sync data), `app/utils/sections_data.py`.

### S6c: Wire Canonical `sections_data` into Suggester — ✅ DONE (2026-08-04)

- **Goal & Rationale:** Make `app/utils/sections_data.py` the single source of truth for section IDs consumed by the suggester, eliminating the duplicated hardcoded `{"55", "56", "58", "63", "64"}` set.
- **Changes Applied (`app/utils/suggester.py`):**
    - Import `SECTIONS`, `VALID_SECTION_IDS` from `app.utils.sections_data`.
    - `_MANUAL_ONLY_SECTIONS` (now `frozenset`) asserted `<= VALID_SECTION_IDS` at import.
    - Rule-4 filter now whitelists against `VALID_SECTION_IDS`.
    - Added `section_title(section_id)` helper for future statutory-text consumers.
    - **Behavior unchanged** — `suggest_sections` output is byte-stable for all inputs.
- **Tests:** New `tests/test_suggester_sections_data.py` (9 tests) covering subset-of-whitelist invariant, rule behavior, and statute loading.
- **Note (import-time coupling):** importing `app.utils.suggester` now eagerly loads `fss_sections.md` via `app.utils.sections_data` and hard-fails at import if the file is missing — intended "fail loudly" design; safe for git-based deploys (Render) where the tracked file is always present.

### S6d: Fix `Expired_item` inversion in suggester Section 55 rule — ✅ DONE (2026-08-05)

- **What:** `Expired_item` was mis-grouped under the `"no"`-is-a-violation logic in `_DIRECTION_COMPLIANCE_ITEMS`, flagging the compliant default `"no"` as a Section 55 violation.
- **Fix Applied:** Moved `Expired_item` to a new `_POSITIVE_FLAG_ITEMS` dict where `"yes"` means non-compliant (matching the form's "are expired items present?" semantics). `_detect_section_55_from_checklist()` now checks `== "no"` for direction-compliance items and `== "yes"` for positive-flag items. Behavior changed only for `Expired_item`: `"no"` (compliant) no longer triggers Section 55; `"yes"` (non-compliant) now does. All other `suggest_sections()` output is byte-stable.
- **Files:** `app/utils/suggester.py` — new `_POSITIVE_FLAG_ITEMS` dict and updated `_detect_section_55_from_checklist()`.
- **Tests:** `tests/test_suggester_sections_data.py` — the `_checklist()` fixture comment was updated to reflect the corrected semantics.

---

## Priority 1 — Foundational (Phases 10–12)

### Phase 10: Fuzzy Search Integration & Rapidfuzz Dependency — ✅ DONE (2026-08-05)

- **Goal & Rationale:** Enhance search functionality across documents, section content, and annexures by adding fuzzy string matching (`rapidfuzz`) as a fallback when SQLite FTS5 or SQL `LIKE` queries return no matches or fall below relevance thresholds.
- **Target Files to Edit/Create:**
    - `pyproject.toml` (dependency declaration)
    - `app/search/indexer.py` (fuzzy search fallback logic)
    - `app/search/routes.py` (query parameter handling & response formatting)
    - `app/search/templates/search/index.html` (UI toggle element)
    - `app/document_cleaner/normalizers.py` (import audit)
    - `tests/test_search.py` (unit & integration tests)
- **Detailed Implementation Plan:**
    1. **Dependencies:** Add `rapidfuzz>=3.0.0` under `[project.dependencies]` in `pyproject.toml`. Also declare `numpy` — it is an undeclared runtime import for the OCR pipeline and `tests/test_ocr_pipeline.py` fails to collect on fresh environments without it (confirmed during S6 verification).
    2. **Indexer Implementation (`app/search/indexer.py`):**
        - Import `rapidfuzz.process` and `rapidfuzz.fuzz`.
        - Implement `fuzzy_search_fallback(query: str, limit: int = 20, threshold: float = 65.0) -> list[dict]`:
            - Fetch candidate records (`CaseFile`, `Adjudication`, `Annexure`) from SQLite DB.
            - Extract text fields (`title`, `content`, `annexure_name`, `extracted_text`).
            - Compute `fuzz.token_set_ratio(query, target_text)`.
            - Filter results where `score >= threshold` and sort descending by score.
        - Update primary `search()` method in `indexer.py` to trigger `fuzzy_search_fallback` when FTS/LIKE yields 0 results OR when `fuzzy=True` flag is passed.
    3. **Route Update (`app/search/routes.py`):**
        - Read `fuzzy = request.args.get('fuzzy', 'false').lower() == 'true'`.
        - Pass `fuzzy` parameter into `indexer.search(query, fuzzy=fuzzy)`.
        - Return JSON or rendered HTML containing fuzzy match scores.
    4. **UI Update (`app/search/templates/search/index.html`):**
        - Add a styled toggle switch `<input type="checkbox" id="fuzzyToggle" name="fuzzy" value="true">` alongside the search input bar.
    5. **Normalizer Cleanup (`app/document_cleaner/normalizers.py`):**
        - Verify import `from rapidfuzz import process, fuzz` functions cleanly without fallback import warnings.
- **Acceptance Criteria & Test Plan:**
    - `pip install -e .` succeeds without dependency conflicts.
    - Search query `"inspectn"` (typo of "inspection") with `fuzzy=true` returns matching inspection documents with match confidence scores.
    - `pytest tests/test_search.py` passes all tests.

---

### Phase 12: Legal Validation Engine — ✅ DONE (verified 2026-08-07)

- **Goal & Rationale:** Build an automated rule-based validation engine that analyzes case documents and adjudications for legal completeness, mandatory section presence, statutory reference accuracy (FSSA 2006), signature placeholders, date sequence consistency, and evidence duplication.
- **Implemented (verified 2026-08-07):** `app/validation/__init__.py` (`validation_bp`, url_prefix `/validation`); `app/validation/rules.py` — all 7 rules (`MandatorySectionsRule`, `SignaturePlaceholderRule`, `NumberingFormatRule`, `StatutoryReferenceRule`, `DuplicateEvidenceRule`, `TimelineConsistencyRule`, `DocumentCompletenessRule`) + `ValidationResult`/`BaseRule`/`RULES`; `app/validation/engine.py` — `ValidationEngine.validate_case()` resolves via `CaseResolver`, builds a pure-dict `case_data` payload (rules never touch the ORM), runs the registry, and returns `{score, grade, errors, warnings, suggestions, info, rules_run, case_id, adjudication_id, case_type, case_number}`; `app/validation/routes.py` — `POST /validation/validate` (400 on bad payload / 404 on unknown case) + `GET /validation/case/<int:case_id>?kind=`; blueprint registered in `app/__init__.py` (import L365, `register_blueprint` L389). **Provenance note:** the module reached main via commit `c020fcc` ("Track untracked production files") — it was fully built on disk but never reported in task.md and never given a dedicated commit.
- **UI integration (plan step 5) — done (verified 2026-08-07):** the "Run Legal Validation" button + validation drawer are implemented in the workbench template `app/legal_analysis/templates/legal_analysis/index.html` (second card + second `<script>` block): a numeric case-id input, a case-type select (`case_file` | `adjudication`), a "Validate case" button (`#validate-btn`, `type="button"`) that POSTs JSON to `/validation/validate`, and a drawer (`#val-results`) rendering a circular score badge color-coded by grade, the case summary line (case number · case type · rules evaluated), and errors / warnings / suggestions lists (each finding shows `message`, `field_name`, `suggestion`). No change to `app/legal_analysis/routes.py` is required — the UI is client-side against the existing validation API, mirroring the analyze feature in the same template. **Correction note (2026-08-07):** an earlier evaluation of task.md reported this as missing because it inspected only `app/legal_analysis/routes.py`; the template has contained the UI since commit `c020fcc`. New `TestValidationUIIntegration` (2 tests) pins the render + report contract. **Entry points extended (2026-08-07):** the drawer rendering moved into the shared `app/static/js/validation_drawer.js` module — `ValidationDrawer.initForm` (workbench: `#validate-btn` + `#val-case-id` + `#val-case-type` + `#val-results`) and `ValidationDrawer.initRowButtons` (per-row `#…-val-drawer` on `/case_file_generator/` and `/adjudication/` index pages, buttons carry `data-case-id` + `data-case-type`; the workbench inline JS was refactored onto the module). Covered by `TestValidationUIEntryPoints` (4 tests — index pages + document editors). Suite: **46/46**. Document-viewer suites remain green (66/66) after the editor.html change.
- **Target Files to Edit/Create:**
    - `app/validation/__init__.py` (new blueprint package)
    - `app/validation/rules.py` (rule classes & registry)
    - `app/validation/engine.py` (validation orchestrator)
    - `app/validation/routes.py` (HTTP endpoints)
    - `app/legal_analysis/routes.py` (integration trigger)
    - `app/__init__.py` (register blueprint)
    - `tests/test_validation.py` (new test suite)
- **Detailed Implementation Plan:**
    1. **Data Models & Structures (`app/validation/rules.py`):**
        - Dataclass `ValidationResult`:

            ```python
            @dataclass
            class ValidationResult:
                rule_id: str
                severity: str  # 'ERROR', 'WARNING', 'INFO'
                message: str
                field_name: str | None = None
                suggestion: str | None = None
            ```

        - Abstract Base Class `BaseRule`:

            ```python
            class BaseRule(ABC):
                rule_id: str
                description: str
                @abstractmethod
                def evaluate(self, case_data: dict) -> list[ValidationResult]: ...
            ```

        - Concrete Rule Implementations:
            - `MandatorySectionsRule`: Checks required document sections using `app.utils.suggester`.
            - `SignaturePlaceholderRule`: Scans template HTML for missing `{{ signature }}` or `[Signature]` placeholders.
            - `NumberingFormatRule`: Validates formats of `case_number`, `sample_code`, `lab_reg_no` using regex (`^[A-Z0-9\/-]+$`).
            - `StatutoryReferenceRule`: Validates applicability of FSSA 2006 Sections 55 (penalty for non-compliance), 56 (unhygienic processing), 58 (sub-standard food), 63 (unlicensed business), 64 (repeated offense).
            - `DuplicateEvidenceRule`: Identifies duplicate evidence files via SHA-256 content hashes.
            - `TimelineConsistencyRule`: Asserts that `sampling_date <= lab_dispatch_date <= lab_report_date`.
            - `DocumentCompletenessRule`: Scans for empty section blocks or unlinked annexures.
    2. **Engine Orchestrator (`app/validation/engine.py`):**
        - Class `ValidationEngine`:
            - `__init__()`: Loads all registered rules.
            - `validate_case(case_id: int, case_type: str) -> dict`:
                - Gathers case record (`CaseFile` or `Adjudication`), annexures, evidence records, and section texts.
                - Executes registered rules.
                - Computes composite score: `100 - (15 * error_count + 5 * warning_count)`, clamped between 0 and 100.
                - Returns structured dict: `{score: int, errors: list, warnings: list, suggestions: list}`.
    3. **Blueprint & Routes (`app/validation/routes.py`):**
        - Register `validation_bp = Blueprint('validation', __name__, url_prefix='/validation')`.
        - Route `POST /validation/validate`: Accepts JSON `{"case_id": int, "case_type": str}`. Returns validation JSON.
        - Route `GET /validation/case/<case_id>`: Returns validation summary for a case.
    4. **App Registration (`app/__init__.py`):** Register `validation_bp`.
    5. **Legal Analysis Integration (`app/legal_analysis/routes.py`):**
        - Add "Run Legal Validation" button in UI.
        - Trigger `ValidationEngine` and render validation drawer with score badge and warning list.
- **Acceptance Criteria & Test Plan:** ✅ MET (2026-08-07) — `POST /validation/validate` returns HTTP 200 with structured `{score, errors, warnings, suggestions}` (400 on malformed payload, 404 on unknown case); every rule is unit-tested with valid and invalid payloads; engine scoring/grading and the HTTP endpoints are verified end-to-end against real CaseFile/Adjudication records. `tests/test_validation.py` — **46/46 pass** (incl. `TestValidationUIIntegration` + `TestValidationUIEntryPoints`).

---

### Phase 13: Timeline Engine & Gantt Visualization — ✅ DONE (2026-08-06)

- **Goal & Rationale:** Automatically generate an interactive timeline and Gantt chart of case progression by extracting key milestones (complaint, inspection, sampling, lab dispatch, lab report, notice issuance, reply, petition, court order) from case documents.
- **Implemented:** `app/timeline/` — `engine.py` (`TimelineEntry` dataclass + `TimelineEngine.extract/refresh/validate_sequence/build_payload`), `routes.py` (view + JSON API + POST refresh), `templates/timeline/index.html` (vertical milestone timeline + horizontal Gantt with month axis, today marker, legend, sequence-warning callouts, and direct annexure/evidence download links). Blueprint registered in `app/__init__.py` (url_prefix `/timeline`). Milestone sources: CaseFile dates (inspection/sample-submission/DO-receipt/analyst-report/directive/replies), linked Sample (collection + dispatch), Adjudication (complaint/authorization/inspections/compliance + linked Inspections), Annexures and Evidence. **Persistence:** `timeline_event.case_id` is a NOT NULL FK to `case_files.id`, so case_file events are persisted (idempotent delete+insert on each API GET); adjudication timelines are computed on the fly and never stored.
- **Access (2026-08-06):** global **Timeline** nav item in `app/templates/base.html` opens a case-picker modal, reachable from every page incl. audit/annexure. The picker is a keyboard-navigable search dropdown (arrow keys + Enter, `<mark>` match highlighting, direct numeric-ID open, server-injected URL bases). Timeline entry points everywhere a case surfaces: both index pages' "Case Timelines" panel, document editor action bar, search results (case_file/adjudication), evidence cards, annexure rows, inspection list + detail (when adjudicated), audit log (CaseFile/Adjudication records), version-control history header, and sample list (batched sample→case map). Also fixed orphaned `app/audit/__init__.py` (never imported `routes.py`, so `/admin/audit-log` 404'd) and stale `edit_case_file`/`edit_adjudication` url_for names → `edit_case` in annexure/evidence templates.
- **Target Files to Edit/Create:**
    - `app/models/document.py` (add `TimelineEvent` SQLAlchemy model)
    - `migrations/versions/xxxx_add_timeline_event_table.py` (Alembic migration script)
    - `app/timeline/__init__.py` (blueprint init)
    - `app/timeline/engine.py` (event extraction logic)
    - `app/timeline/routes.py` (endpoints)
    - `app/timeline/templates/timeline/index.html` (interactive UI template)
    - `app/__init__.py` (register blueprint)
    - `tests/test_timeline.py` (test suite)
- **Detailed Implementation Plan:**
    1. **Database Model (`app/models/document.py`):**

        ```python
        class TimelineEvent(db.Model):
            __tablename__ = "timeline_event"
            id = db.Column(db.Integer, primary_key=True)
            case_id = db.Column(db.Integer, db.ForeignKey("case_file.id"), nullable=False, index=True)
            case_type = db.Column(db.String(32), default="case_file")
            event_type = db.Column(db.String(64), nullable=False) # e.g. 'inspection', 'sampling', 'lab_report'
            timestamp = db.Column(db.DateTime, nullable=False, index=True)
            document_ref = db.Column(db.String(256), nullable=True) # Annexure or document link
            description = db.Column(db.Text, nullable=True)
            created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
        ```

    2. **Migration Script (`migrations/versions/`):** Generate standard migration adding `timeline_event` table with indexes on `case_id` and `timestamp`.
    3. **Timeline Extraction Engine (`app/timeline/engine.py`):**
        - Class `TimelineEngine`:
            - `extract_events_from_case(case_id: int) -> list[TimelineEvent]`:
                - Reads `CaseFile`, associated `Inspection`, `Sample`, `Adjudication`, and `Annexure` records.
                - Parses date fields (`inspection_date`, `sampling_date`, `dispatch_date`, `lab_report_date`, `notice_date`).
                - Generates `TimelineEvent` records and detects chronologically invalid sequences (e.g. lab report preceding sampling date).
    4. **Blueprint & Routes (`app/timeline/routes.py`):**
        - Register `timeline_bp = Blueprint('timeline', __name__, url_prefix='/timeline')`.
        - Route `GET /timeline/case/<int:case_id>`: Renders `index.html` displaying case timeline.
        - Route `GET /timeline/api/case/<int:case_id>`: Returns JSON array of timeline events for frontend charting.
    5. **Frontend Template (`app/timeline/templates/timeline/index.html`):**
        - Render vertical milestone timeline and horizontal Gantt chart using HTML/CSS and vanilla JS.
        - Include direct links to source annexures/documents for each timeline event node.
- **Acceptance Criteria & Test Plan:** ✅ MET — case dates auto-populate `timeline_event` on API access; `/timeline/case/<id>` renders the interactive timeline (vertical nodes + Gantt bars); `tests/test_timeline.py` — **21/21 pass** (engine extraction, linked Sample/Annexure events, adjudication ephemeral, sequence-warning detection, idempotent persistence, API/view/refresh routes, 404s, None-date guards, annexure `document_url`, global nav picker, all UI entry points, sample-detail JSON link, `/cases` feed shape, editor + index-page links).

---

## Priority 2 — Core Features (Phases 13–20)

### Phase 15: Analytics Dashboard — ❌ NOT STARTED (verified 2026-08-07)

- **Goal & Rationale:** Provide operational analytics and executive reporting on pending/disposed cases, inspection compliance rates, sample testing pipeline status, legal section frequency, and geographic violation clusters.
- **Evaluation note (2026-08-07):** Confirmed **not started** — `glob app/analytics/**` returns nothing (no `app/analytics/` package), no `/analytics` routes registered, no `analytics` nav link in `app/templates/base.html`, and no `tests/test_analytics.py`. Greenfield build per the plan below; no existing code to rework.
- **Target Files to Edit/Create:**
    - `app/analytics/__init__.py` (blueprint package)
    - `app/analytics/routes.py` (aggregate SQL queries & API)
    - `app/analytics/templates/analytics/dashboard.html` (dashboard template with Chart.js & Leaflet.js)
    - `app/templates/base.html` (add navigation link)
    - `app/__init__.py` (register blueprint)
    - `tests/test_analytics.py` (test suite)
- **Detailed Implementation Plan:**
    1. **Aggregate Query Service (`app/analytics/routes.py`):**
        - Route `GET /analytics/`: Renders dashboard view.
        - Route `GET /analytics/api/metrics`:
            - Query 1 (Case Statuses): Count of `CaseFile` & `Adjudication` records grouped by status (`draft`, `pending`, `disposed`).
            - Query 2 (Inspection Compliance): Count of `Inspection` records grouped by `compliance_status`.
            - Query 3 (Sample Pipeline): Breakdown of `Sample` records across states (`collected`, `sent_to_lab`, `conforming`, `non_conforming`).
            - Query 4 (Legal Provisions): Aggregated count of FSSA 2006 sections cited across case files.
            - Query 5 (Geographic Map Data): Select `lat`, `lng`, `fbo_name`, `violation_details` from `FboIssue` / `Inspection` records with non-null coordinates.
    2. **Dashboard UI (`app/analytics/templates/analytics/dashboard.html`):**
        - Include Chart.js (CDN or local static asset).
        - Render Donut Chart for case statuses, Bar Chart for sample pipeline, Horizontal Bar Chart for legal sections.
        - Render interactive Leaflet.js map plotting FBO locations with custom marker popups showing violation details.
    3. **Base Layout Link (`app/templates/base.html`):** Add nav item linking to `url_for('analytics.dashboard')`.
- **Acceptance Criteria & Test Plan:**
    - Navigating to `/analytics/` displays charts and map with active DB metrics without SQL errors.
    - `pytest tests/test_analytics.py` asserts endpoint returns 200 and valid JSON data.

---

### Phase 18: Multi-User RBAC & Document Comments — ⚠️ PARTIAL (verified 2026-08-07)

- **Goal & Rationale:** Implement role-based access control (RBAC) to enforce administrative permission boundaries, alongside a document commenting system for multi-user collaboration and approval workflows.
- **Status (verified 2026-08-07): ~30% complete.** ✅ `Role`, `user_roles`, `Comment` models in `app/models/auth.py` (migration `add_role_user_role_comment_tables.py` + `fix_rbac_tables.py`); ✅ admin user-management UI at `/auth/users` (list / create / reset-password / toggle-admin / delete, guarded by `@admin_required` from `app/utils/auth.py`) — **but it manages the `is_admin` boolean only, with no Role-model assignment UI**. ❌ Not implemented: `app/decorators.py` + `@role_required(*roles)` (the only guard is `admin_required`, which checks `is_admin`, not roles), comment API endpoints in `app/document_viewer/routes.py` (zero matches), the comment sidebar in `document_viewer/editor.html`, and `tests/test_rbac.py` (does not exist). Note `User.roles` is deliberately `lazy="select"` and currently unused — nothing reads roles yet.
- **Target Files to Edit/Create:**
    - `app/models/auth.py` (add `Role`, `UserRole`, `Comment` models)
    - `migrations/versions/xxxx_add_rbac_and_comment_tables.py` (DB migration)
    - `app/decorators.py` (create `@role_required` decorator)
    - `app/auth/routes.py` (user role management UI for admins)
    - `app/document_viewer/routes.py` (comment API endpoints)
    - `app/document_viewer/templates/document_viewer/editor.html` (comment sidebar UI)
    - `tests/test_rbac.py` (security test suite)
- **Detailed Implementation Plan:**
    1. **Database Schema (`app/models/auth.py`):**

        ```python
        class Role(db.Model):
            __tablename__ = "role"
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(64), unique=True, nullable=False) # 'admin', 'inspector', 'adjudicator', 'viewer'
            description = db.Column(db.String(256))

        user_roles = db.Table(
            "user_roles",
            db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
            db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
        )

        class Comment(db.Model):
            __tablename__ = "comment"
            id = db.Column(db.Integer, primary_key=True)
            case_id = db.Column(db.Integer, nullable=False, index=True)
            case_type = db.Column(db.String(32), default="case_file")
            user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
            content = db.Column(db.Text, nullable=False)
            section_id = db.Column(db.String(128), nullable=True) # Anchored document section
            created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
        ```

    2. **Security Decorator (`app/decorators.py`):**

        ```python
        def role_required(*roles):
            def decorator(f):
                @wraps(f)
                def decorated_function(*args, **kwargs):
                    if not current_user.is_authenticated:
                        return redirect(url_for('auth.login'))
                    user_role_names = [r.name for r in current_user.roles]
                    if not any(role in user_role_names for role in roles) and not current_user.is_admin:
                        abort(403)
                    return f(*args, **kwargs)
                return decorated_function
            return decorator
        ```

    3. **Comment Endpoints (`app/document_viewer/routes.py`):**
        - `POST /document/comment`: Creates comment tied to case and section.
        - `GET /document/<case_type>/<int:case_id>/comments`: Returns list of comments for document sidebar.
    4. **Admin UI (`app/auth/routes.py`):** Add route `GET/POST /auth/users` for assigning roles to user accounts.
- **Acceptance Criteria & Test Plan:**
    - Non-admin user accessing an `@role_required('admin')` route receives HTTP 403.
    - Document comments can be posted, persisted in DB, and viewed in the editor sidebar.
    - `pytest tests/test_rbac.py` tests permissions and comment creation.

---

### Phase 16: Backup, Export & Case Import — ✅ DONE (2026-08-05)

- **Goal & Rationale:** Provide comprehensive data portability through full JSON/ZIP case exports, case restoration via JSON imports, and scheduled automated database backups via Celery beat.
- **Implementation Summary:** All three sub-features are fully implemented and verified with 14 passing tests (`tests/test_case_backup.py`).
- **Files Modified/Created:**
    - `app/case_file_generator/services.py` — `export_case_as_json()`, `export_case_as_zip()`, `import_case_from_json()` (supports both `case_file` and `adjudication` types)
    - `app/case_file_generator/routes.py` — 3 routes: `GET /api/cases/<id>/export.json`, `GET /api/cases/<id>/export.zip`, `POST /api/cases/import` (admin-required)
    - `app/utils/backup.py` — `create_daily_db_snapshot()` + `create_daily_db_snapshot_task` (Celery beat handler)
    - `celery_app.py` — `beat_schedule` entry `"daily-db-snapshot"` at midnight UTC
    - `app/settings/templates/settings/backup.html` — backup dashboard UI (download, restore, table counts)
    - `app/settings/routes.py` — backup, backup_download, backup_restore routes
    - `tests/test_case_backup.py` — 14 tests (TestExportCaseJson, TestExportCaseZip, TestImportCase, TestDailySnapshot) — all pass
- **Detailed Implementation:**
    1. **Full Case JSON Export (`app/case_file_generator/services.py:290-318`):** `export_case_as_json(case_id, case_type)` serializes case record, annexures (with relative file paths), evidence, and versions into a nested JSON dict.
    2. **Extended ZIP Bundler (`app/case_file_generator/services.py:326-380`):** `export_case_as_zip(case_id, case_type)` packages `case_export.json` manifest + compiled PDFs (via DocumentCaseManager) + raw annexure/evidence files. PDF/file failures are non-fatal (appended to `warnings` list).
    3. **Case Import Engine (`app/case_file_generator/services.py:388-493`):** `import_case_from_json(json_data)` validates JSON schema, creates new CaseFile/Adjudication via existing form processor, clones annexures/evidence/versions in a DB transaction. Raw files are NOT duplicated — only DB records.
    4. **Automated Celery Beat (`celery_app.py:92-103` + `app/utils/backup.py:299-328`):** `daily-db-snapshot` beat schedule calls `create_daily_db_snapshot()` which writes a dated `nsa_backup_<YYYYMMDD>.zip` to `instance/backups/`.
    5. **Settings UI (`app/settings/templates/settings/backup.html`):** Download button, restore form with CSRF token + size limit, table row counts display.
- **Acceptance Criteria & Test Plan:** ✅ All 14 tests pass (`pytest tests/test_case_backup.py`). Export → re-import creates an identical clone with new PKs and cloned related records.

---

### Phase 11: AI Assistant Integration

- **Status:** ✅ **Complete (2026-08-08)** — `app/ai_assistant/` package (blueprint + service + routes + Celery task + JS sidebar); `httpx`-based `AIAssistantService` (zero new dependencies); `POST /ai-assistant/assist`; editor sidebar with 4 action buttons; `tests/test_ai_assistant.py` **23/23 pass**; no regressions (validation 46/46, food_cell 15/15, timeline 21/21, knowledge graph 21/21 green).
- **Goal & Rationale:** Embed an AI-powered assistant into the document editor to provide automated summarization, legal terminology refinement, contradiction detection, missing annexure identification, and prayer drafting.
- **Codebase Evaluation (verified):**
    - **`httpx>=0.27.0`** is already a declared dependency in `pyproject.toml` — **no new dependency needed** for LLM API calls (OpenAI/OpenRouter both expose HTTP APIs). The `openai` package is NOT available.
    - **Blueprint pattern** — follow `app/food_cell/__init__.py` (Blueprint with `template_folder` + `static_folder`, routes imported after definition).
    - **Service layer pattern** — follow `app/food_cell/services.py`: lazy imports of optional deps, `current_app` for config access, `db.session` for persistence.
    - **Routes pattern** — follow `app/validation/routes.py`: import blueprint from `app.ai_assistant`, use `jsonify` + `request.get_json(silent=True)`.
    - **Editor integration** — `app/document_viewer/templates/document_viewer/editor.html` already exposes `window.CASE_ID`, `window.QuillEditor` (`getQuill()`, `getDelta()`, `getPreviewHtml()`), and the legal-validation drawer pattern (`#editor-val-drawer` + `ValidationDrawer.initRowButtons`). The AI sidebar mirrors this pattern — docked as an `<aside>` in the `split-view`, with `window.QuillEditor.getQuill().root.innerHTML` as the content source.
    - **Test pattern** — follow `tests/test_validation.py` (46 tests): `_setup_test_env()` creates app with in-memory SQLite + `db.create_all()`, seeds `User`/`FSO` via `POST /auth/login`.
    - **Celery pattern** — follow `app/food_cell/tasks.py`: lazy import `from celery_app import celery`, `if celery is not None: fn = celery.task(...)`.
    - **Config pattern** — follow `app/__init__.py`: read env vars into `app.config` at factory time.
- **Target Files to Edit/Create:**
    - `app/ai_assistant/__init__.py` (blueprint package + `ai_bp` Blueprint)
    - `app/ai_assistant/service.py` (`AIAssistantService` — httpx-based LLM client with token tracking)
    - `app/ai_assistant/routes.py` (`POST /ai/assist` endpoint)
    - `app/ai_assistant/tasks.py` (Celery task for async doc-level operations)
    - `app/static/js/ai_assistant.js` (editor sidebar JS — dockable, mirrors `validation_drawer.js` pattern)
    - `app/__init__.py` (register `ai_bp` at `/ai-assistant`, add env vars to config)
    - `.env.example` (add `AI_ASSISTANT_PROVIDER`, `AI_ASSISTANT_API_KEY`, `AI_ASSISTANT_BASE_URL`, `AI_ASSISTANT_MODEL`)
    - `tests/test_ai_assistant.py` (test suite with mocked HTTP + mocked LLM responses)
- **Detailed Implementation Plan:**
    1. **Provider Abstraction Service (`app/ai_assistant/service.py`):**
        - Class `AIAssistantService`:
            - Reads config from `current_app`: `AI_ASSISTANT_PROVIDER` ('openrouter'|'openai'|disabled), `AI_ASSISTANT_API_KEY`, `AI_ASSISTANT_BASE_URL` (optional override), `AI_ASSISTANT_MODEL` (default: `poolside/laguna-s-2.1:free` via OpenRouter).
            - `_request(prompt, max_tokens) -> tuple[str, int]`: single `httpx.Client.post()` to the provider's chat completions endpoint, parses `choices[0].message.content` + `usage.total_tokens` from JSON response. Retries with exponential backoff on 429/503 (3 attempts).
            - `is_enabled() -> bool`: returns `False` if API key missing or provider not set.
            - Action methods (each maps to a prompt template):
                - `summarize_text(text, max_tokens=500) -> str`
                - `refine_legal_language(text) -> str`
                - `detect_contradictions(text) -> list[str]`
                - `suggest_missing_annexures(text) -> list[str]`
                - `draft_prayers(facts, grounds) -> str`
            - Each method returns `(result, tokens_used)` via a `_track_usage(n)` helper that accumulates per-request token counts (satisfies S10c operational monitoring).
        - **LLM prompt templates** — stored as module-level constants (not separate files) following the 'fewest files possible' principle. Each is a focused prompt: system instruction + user content, with explicit JSON output format for structured actions (contradictions, annexures).
        - **No new dependencies** — `httpx` handles everything. Token usage tracked via `usage.total_tokens` from provider response.
    2. **Celery Task (`app/ai_assistant/tasks.py`):**
        - Lazy import pattern from `app/food_cell/tasks.py`: `try: from celery_app import celery; except ImportError: celery = None`.
        - `run_ai_action(action, content, context=None) -> dict`: wraps `AIAssistantService`, returns `{"result": str, "tokens_used": int}`. Registered as `celery.task` if celery available.
        - Add `"app.ai_assistant.tasks"` to `TASK_MODULES` in `celery_app.py`.
    3. **API Routes (`app/ai_assistant/routes.py`):**
        - Register `ai_bp = Blueprint('ai_assistant', __name__)`.
        - Route `POST /ai/assist`: Accepts `{"action": str, "content": str, "context": dict}`. Returns JSON `{"result": str, "tokens_used": int, "action": str}`. 400 on missing/invalid action. 503 if AI not configured.
        - Action whitelist: `summarize`, `refine_legal`, `detect_contradictions`, `suggest_annexures`, `draft_prayers`.
    4. **Frontend Sidebar (`app/static/js/ai_assistant.js`):**
        - Mirrors `validation_drawer.js` pattern: IIFE module, `ready()` helper, `esc()` escape function.
        - Exports `window.AIAssistant = { init: fn, dock: fn }` — `init()` binds action buttons in the editor, calls `POST /ai-assistant/assist` via `fetch`, renders results in a docked `<aside>` drawer.
        - Action buttons: 'Summarize', 'Improve Legal Phrasing', 'Find Contradictions', 'Suggest Annexures', 'Draft Prayer'.
        - Content source: `window.QuillEditor.getQuill().root.innerHTML` (current editor content).
    5. **Editor Integration (`app/document_viewer/templates/document_viewer/editor.html`):**
        - Add `<aside id="aiAssistantPane">` to the `split-view` (between TOC pane and editor pane, or as a collapsible floating panel).
        - Add AI action buttons to the `action-bar` (next to Validate button).
        - Include `<script src=".../ai_assistant.js">` + init call in `extra_js` block.
    6. **App Registration (`app/__init__.py`):**
        - Add `AI_ASSISTANT_PROVIDER`, `AI_ASSISTANT_API_KEY`, `AI_ASSISTANT_BASE_URL`, `AI_ASSISTANT_MODEL` to `app.config`.
        - Import + register: `from app.ai_assistant import ai_bp` / `app.register_blueprint(ai_bp, url_prefix="/ai-assistant")`.
    7. **Env vars (`.env.example`):** Add the 4 new variables with descriptive comments (no token values).
- **Acceptance Criteria & Test Plan:**
    - `AIAssistantService.is_enabled()` returns `False` when `AI_ASSISTANT_API_KEY` is unset (graceful degradation — app boots, routes return 503).
    - `POST /ai/assist` with `{"action": "summarize", "content": "..."}` returns 200 + `{"result": str, "tokens_used": int}` when configured, 503 when not.
    - Editor sidebar renders suggestions/detections from the AI service end-to-end.
    - `pytest tests/test_ai_assistant.py` — 10+ tests covering: service construction (enabled/disabled), each action method (mocked httpx), token tracking, route 200/400/503 paths, draft blueprint registration (skipped if unregistered).
    - No regressions: `pytest tests/test_validation.py` (46 tests), `pytest tests/test_food_cell_do_intimation.py` (15 tests), `pytest tests/ -k "editor or legal or document_viewer"` remain green.

### Phase 19: AI Case Intelligence

- **Goal & Rationale:** Synthesize Legal Validation Engine (Phase 12) outputs and AI LLM capabilities (Phase 11) to produce a composite "Case Readiness Score" (0–100), evidence strength index, and allegation-to-evidence matrix.
- **Target Files to Edit/Create:**
    - `app/case_intelligence/__init__.py` (blueprint package)
    - `app/case_intelligence/engine.py` (readiness scoring engine)
    - `app/case_intelligence/routes.py` (endpoints)
    - `app/legal_analysis/routes.py` (UI integration)
    - `tests/test_case_intelligence.py` (test suite)
- **Detailed Implementation Plan:**
    1. **Scoring Engine (`app/case_intelligence/engine.py`):**
        - Class `CaseIntelligenceEngine`:
            - `compute_readiness_score(case_id: int) -> dict`:
                - Fetches validation score from `ValidationEngine`.
                - Evaluates evidence strength (photo verification status, OCR confidence, lab report completeness).
                - Maps allegations to supporting evidence files; identifies unsupported allegations.
                - Returns readiness payload: `{score: int, grade: str, evidence_gaps: list, timeline_conflicts: list}`.
    2. **Routes (`app/case_intelligence/routes.py`):**
        - Route `GET /case_intelligence/<int:case_id>`: Returns intelligence payload for legal analysis view.
- **Acceptance Criteria & Test Plan:**
    - `/case_intelligence/<case_id>` returns a composite score and highlights missing evidence links.
    - `pytest tests/test_case_intelligence.py` tests score calculation with incomplete vs complete cases.

---

### Phase 14: Knowledge Graph Engine — ✅ DONE (2026-08-08)

- **Goal & Rationale:** Provide entity extraction and relationship mapping across cases, FBOs, inspectors, samples, lab reports, legal provisions, and evidence items, visualized via an interactive graph node graph.
- **Target Files to Edit/Create:**
    - `app/knowledge_graph/__init__.py`
    - `app/knowledge_graph/engine.py` (entity-relationship extractor)
    - `app/knowledge_graph/routes.py` (API endpoints)
    - `app/knowledge_graph/templates/knowledge_graph/view.html` (Cytoscape.js visualizer)
    - `app/knowledge_graph/neo4j_sync.py` (Neo4j sync adapter — env-gated, dormant)
    - `tests/test_knowledge_graph.py` (test suite)
- **Detailed Implementation Plan (implemented):**
    1. **Extraction Engine (`app/knowledge_graph/engine.py`):**
        - Class `KnowledgeGraphEngine` with `build_graph_for_case(case_id: int, case_type: str = "case_file") -> dict`:
            - Uses `CaseResolver` (D1) to resolve CaseFile vs Adjudication record by case_id + kind.
            - Extracts Nodes: Case, FBO, Inspector (FSO), Sample, Lab, LegalSection, Evidence, Ancillary (bills + annexures).
            - Extracts Edges: `INSPECTED_BY`, `SAMPLED_FROM`, `TESTED_AT`, `VIOLATED_SECTION`, `SUPPORTED_BY`, `REFERENCES`.
            - Also parses sections from `applicable_sections` string + adjudication flag fields + `problem` text via `_extract_sections()`.
            - Returns Cytoscape.js-compatible JSON: `{nodes: [{data: {id, label, type, color, shape, icon, ...}}], edges: [{data: {source, target, type, color}}]}`.
            - Persists to `Entity`/`Relationship` tables for case_file only (idempotent: delete-then-replace). Adjudication graphs are ephemeral (no persistence).
    2. **Routes & Visualization (`routes.py`, `templates/knowledge_graph/view.html`):**
        - Route `GET /knowledge-graph/case/<int:case_id>?kind=case_file`: Renders Cytoscape.js interactive node-edge graph view with legend, edge list, and node info sidebar.
        - Route `GET /knowledge-graph/api/case/<int:case_id>?kind=case_file`: Returns JSON payload with Cytoscape elements + case metadata (`case_number`, `case_type`, `node_count`, `edge_count`).
        - 404 on unknown case_id / case_type mismatch.
    3. **Neo4j Sync Adapter (`neo4j_sync.py`, dormant by default):**
        - `Neo4jSync` class with `sync_graph(case_id, case_type, nodes, edges)` — runs a single Cypher `UNWIND $nodes AS n MERGE (e:Entity {id: n.id}) …` transaction against a Neo4j Aura database.
        - Gated by `ENABLE_NEO4J_SYNC` env var (defaults to `false`); requires `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.
        - Called after `build_graph_for_case()` when enabled; uses `neo4j` Python driver (`pip install neo4j`).
- **Acceptance Criteria & Test Plan:** ✅ MET — Graph API returns correct node/edge structure representing all case entities and relationships (8 node types, 6 edge types). Persistence is idempotent (re-run replaces rows, not appends). Adjudication graphs are ephemeral. `pytest tests/test_knowledge_graph.py` — **21/21 pass** (TestGraphExtraction: 9, TestPersistence: 3, TestRoutes: 6, TestIntegration: 2).

#### Neo4j Integration (discussion, 2026-08-09)

A preliminary knowledge graph was extracted from the 24-document FSSAI corpus (`corpus_eval_result.json` → `knowledge_graph.json`): **88 nodes** (24 docs + 57 sections + 3 canonical authorities + 4 jurisdictions) and **199 edges** (document→section, document→authority, document→jurisdiction, section_cooccurrence).

**Current state**: neo4j driver installed in venv (6.2.0). `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` env vars set in `.env` and `.env.example`. See Phase 14 Neo4j sync below for APOC dynamic labels, constraints, indexes, and QStash async sync — all implemented and verified against the live Aura instance. RAG uses Qdrant as its primary retrieval store; Neo4j serves as an optional secondary graph store for case-file entity/relationship traversal.

**Decision**: Neo4j integration is **not yet wired into the RAG retrieval pipeline** — Qdrant payloads already carry `document_type`, `authority`, `section_number`, `jurisdiction`, `citations`, `references`, and `entities` enabling filtered retrieval without a graph DB. The `knowledge_graph.json` artifact is a **corpus-level analysis** for authority normalization (6 raw variants → 3 canonical) and section semantic descriptions — not the runtime Phase 14 engine (which extracts entities from individual case-file documents).

**Phase 14 Neo4j sync** (case-file knowledge graph, ✅ DONE 2026-08-07):

- `app/services/neo4j_graph.py` — `Neo4jGraphService` with `push_to_neo4j()`, `query_neo4j()`, `build_cypher_payload()`, `setup_constraints_and_indexes()`, `neo4j_configured()`
- **APOC dynamic labels**: `apoc.create.node([n.label], {...})` creates nodes with real Neo4j labels (Case, FBO, Section, etc.); fallback to `CREATE (:Entity {...})` if APOC unavailable
- **Constraints** (9 uniqueness on `local_id` per label) + **indexes** (3 property indexes on `entity_type`, `name`, `relationship_type`)
- **QStash async**: `sync_kg_to_neo4j` task registered in `TASK_REGISTRY` + `TASK_MODULES`; `POST /knowledge-graph/api/sync-neo4j` (+ `/<case_id>`) route with async/sync fallback
- Env vars in `.env`/`.env.example`: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
- Verified end-to-end against live Aura instance: schema setup, APOC push, dynamic labels, relationships, constraints
- Tests: `tests/test_neo4j_kg_sync.py` — **15/15 pass** (4 test classes covering config detection, real connection, task sync, route async/sync, payload builder, constraint/index setup, APOC flag)

**RAG Neo4j future direction** (if graph traversal RAG is needed):

1. `pip install neo4j` + add `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` to `.env.example`
2. `scripts/load_kg_to_neo4j.py` (~30 lines) converting `knowledge_graph.json` → Cypher `MERGE` statements
3. Add graph traversal to `retrieval/hybrid_retriever.py` (e.g., citation-chain following for hallucination detection)
4. **Hybrid approach** (recommended): Keep Qdrant for dense + sparse vector search; use Neo4j as secondary store for structured graph traversal queries. Sync from Qdrant chunk payloads to Neo4j nodes/edges on ingestion.

---

### Phase 20: Plugin Architecture

- **Goal & Rationale:** Decouple core services (OCR processing, AI processing, Rule suggestion, PDF generation) behind formal provider interfaces to enable dynamic plugin registration and third-party extensions.
- **Target Files to Edit/Create:**
    - `app/plugins/__init__.py`
    - `app/plugins/base.py` (abstract base interfaces)
    - `app/plugins/registry.py` (plugin registry manager)
    - `app/ocr_pipeline/ocr_engine.py` (refactor to `OCRProvider`)
    - `app/utils/suggester.py` (refactor to `RuleProvider`)
    - `app/pdf_assembly/engine.py` (refactor to `PDFProvider`)
    - `tests/test_plugins.py` (test suite)
- **Detailed Implementation Plan:**
    1. **Plugin Base Interfaces (`app/plugins/base.py`):**

        ```python
        class OCRProvider(ABC):
            @abstractmethod
            def extract_text(self, file_path: Path) -> dict: ...

        class AIProvider(ABC):
            @abstractmethod
            def generate(self, prompt: str) -> str: ...

        class RuleProvider(ABC):
            @abstractmethod
            def evaluate_rules(self, data: dict) -> list: ...

        class PDFProvider(ABC):
            @abstractmethod
            def render_pdf(self, html_content: str) -> bytes: ...
        ```

    2. **Plugin Registry (`app/plugins/registry.py`):**
        - Singleton `PluginRegistry` supporting `register(interface, name, cls)` and `get(interface, name)`.
    3. **Engine Refactoring:**
        - Wrap existing WeasyPrint, OCR, and Suggester implementations into registered plugin classes.
- **Acceptance Criteria & Test Plan:**
    - Modules retrieve active providers through `PluginRegistry.get(...)` without hardcoded imports.
    - `pytest tests/test_plugins.py` verifies dynamic plugin registration and execution.

---

## Priority 3 — Pipeline (Extraction → Storage → Autopopulation)

### Phase A: Foundation (DB Schema + Services) — ✅ DONE (2026-08-06)

- **Goal & Rationale:** Build the fundamental data models, celery tasks, and core services for Vision-LLM + zonal OCR processing and multi-sample PDF splitting.
- **Target Files to Edit/Create:**
    - `app/models/ocr.py` (new model file)
    - `migrations/versions/xxxx_add_ocr_pipeline_tables.py` (migration script)
    - `app/services/ocr_extraction.py` (OCR processing service)
    - `app/services/page_splitter.py` (PDF bundle splitter)
    - `app/ocr_pipeline/tasks.py` (Celery background tasks)
- **Detailed Implementation Plan:**
    1. **Database Schema (`app/models/ocr.py`):**
        - Models: `OCRDocument` (raw extracted JSON, status, content hash), `LabTestParameter` (standard vs observed values), `OCRCorrection` (field corrections log), `FieldAuthority` (source authority weights), `ConflictLog` (conflicting field values).
        - Extend `Sample` model: Add fields `nature_of_food`, `batch_no`, `mfd`, `exp`, `manufacturer_details`.
    2. **Services (`ocr_extraction.py`, `page_splitter.py`):**
        - Implement `process_document_ocr(file_path: Path) -> dict`.
        - Implement `split_pdf_bundle(file_path: Path) -> list[Path]`.
    3. **Celery Tasks (`tasks.py`):** Implement `process_ocr_document_async` task.
- **Implemented (2026-08-06):** `process_document_ocr()` (SHA-256 hash, page count, OCR + regex/NER field extraction, lab-test parameter triples), `split_pdf_bundle()` (PyMuPDF page splitting; single-page passthrough; missing-file → `[]`), and the `process_ocr_document_async` Celery task (registered via `celery_app.py` `TASK_MODULES`; persists `OCRDocument` + `LabTestParameter` rows, links `sample_id`, returns `""` gracefully on missing files).
- **Acceptance Criteria & Test Plan:** ✅ MET — migration `add_ocr_pipeline_models.py` executes cleanly; the Celery task writes `OCRDocument` + `LabTestParameter` rows (verified by `tests/test_ocr_extraction.py` — **14/14 pass**, including 3 new task-persistence tests in `TestOcrTaskPersistence`).

---

### Phase B: Review Workflow

- **Goal & Rationale:** Provide an editable UI review workflow for extracted OCR fields, auto-creating `OCRCorrection` records on manual edits and writing field discrepancies to `ConflictLog`.
- **Target Files to Edit/Create:**
    - `app/ocr_extraction/__init__.py`
    - `app/ocr_extraction/routes.py`
    - `app/ocr_extraction/service.py`
    - `app/conflict_resolution/routes.py`
    - `app/conflict_resolution/templates/conflict_resolution/queue.html`
- **Detailed Implementation Plan:** Build interactive review form, track manual diffs into `OCRCorrection`, and surface conflicting values in the conflict resolution queue template.
- **Acceptance Criteria & Test Plan:** Editing an extracted value writes an entry to `OCRCorrection` and updates target case fields.

---

### Phase C: Autopopulation

- **Goal & Rationale:** Map verified OCR data directly into DO Letters, Bill Generators, Case Files, Adjudications, and Notices, auto-drafting `FboIssue` records for non-conforming lab reports.
- **Target Files to Edit/Create:**
    - `app/autopopulation/service.py`
    - `app/autopopulation/mappings.py`
    - `app/case_file_generator/routes.py`
    - `app/bill_generator/routes.py`
    - `app/adjudication/routes.py`
- **Detailed Implementation Plan:** Construct unified field mapping dictionary (`mappings.py`) and trigger automatic form population across generator blueprints.
- **Acceptance Criteria & Test Plan:** Triggering autopopulation pre-fills case forms without manual re-entry.

---

### Phase D: Feedback Loop

- **Goal & Rationale:** Track field correction rates and automatically inject corrected OCR examples into Vision-LLM prompts for continuous extraction improvement.
- **Target Files to Edit/Create:**
    - `app/feedback_dashboard/routes.py`
    - `app/feedback_dashboard/templates/feedback_dashboard/index.html`
    - `app/ocr_pipeline/tasks.py`
- **Detailed Implementation Plan:** Calculate per-field accuracy metrics and run periodic Celery task `refresh_few_shot_examples`.
- **Acceptance Criteria & Test Plan:** Feedback dashboard displays field error rates and prompt example count.

---

### Phase E: Operational Modes

- **Goal & Rationale:** Support both historical PDF bulk upload backfilling and real-time upload processing upon sample creation.
- **Target Files to Edit/Create:** `app/ocr_extraction/routes.py`
- **Detailed Implementation Plan:** Add `POST /ocr/bulk-upload` endpoint handling multi-file zip batches in background worker.
- **Acceptance Criteria & Test Plan:** Bulk upload processes archive of PDFs and registers individual `OCRDocument` rows.

---

## Priority 4 — Hardening & Debt

### Security Hardening

- [x] **S2: Enforce CSP Header** — ✅ DONE. `content_security_policy_report_only=False` confirmed in `app/__init__.py`.
- [x] **S9a: Concurrency Guard (StaleDataError Handling)** — ✅ DONE. Models have `version_id` column + `__mapper_args__ = {"version_id_col": version_id}` on `Inspection` (`app/models/inspection.py:24-28`), `Sample` (`app/models/billing.py:52-56`), `Bill` (`app/models/billing.py:15-19`), and `CaseFile` (`app/models/document.py:17-21`). Routes catch `StaleDataError` → HTTP 409: `case_file_generator/routes.py:461-463`, `adjudication/routes.py:470-472`, `bill_generator/routes.py:165-167`, `sample/routes.py:317-320`, `inspection_routes.py:313-316` (DELETE). **✅ Fully fixed (2026-08-06):** the inspection-PUT handler now returns `jsonify({...}), 409` as a tuple. `tests/test_concurrency_inspection.py`: **4/4 pass**.
    - **Files:** `app/inspection/routes/inspection_routes.py`, `app/sample/routes.py`, `app/models/inspection.py`, `app/models/billing.py`, `tests/test_concurrency_inspection.py`
- [x] **S10a & S10b: CI Dependency Auditing** — ✅ DONE. `.github/dependabot.yml` and `.github/workflows/pip-audit.yml` both exist.
- [x] **S10c: Database Backup & Health Monitoring** — ✅ DONE (health endpoint exists; `GET /health` returns DB status).

---

### Performance Quick Wins (1–2 Days)

- [x] **SQLAlchemy Connection Pooling:** Add explicit pool options (`pool_size=10`, `max_overflow=20`, `pool_recycle=1800`, `pool_pre_ping=True`) in `app/__init__.py:200-210`. ✅ DONE.
- [x] **FSO Data Caching:** `@lru_cache(maxsize=1)` on `load_fso_names()` in `app/utils/fso_data.py:27`. ✅ DONE.
- [x] **Jinja2 Template Cache:** `FileSystemBytecodeCache` in `app/__init__.py:285-293`. ✅ DONE.
- [x] **Response Compression:** `Flask-Compress` initialized via `compress.init_app(app)` at `app/__init__.py:283`, `Compress` imported in `app/extensions.py:2`. ✅ DONE.
- [x] **Health Endpoint:** `GET /health` route in `app/health/routes.py:11` returning DB status + memory usage. ✅ DONE.
- [x] **Eager Loading Optimization:** ✅ **DONE (2026-08-06).** Added `load_only` column trimming to `DocumentCaseManager._list_cases_query()` — the JSON `/cases` endpoints were hydrating all ~45 columns per row; they now fetch only the 5 summary columns. Set `lazy="selectin"` on `Bill.samples` + the `bills` backref (`app/models/billing.py`) so any loop over bill↔sample access issues a single query. Added `.distinct()` to the evidence tag-cloud query (`app/evidence/routes.py`) so the per-request scan scales with distinct tags, not total evidence count. Audit-log list was already eager (`RecordAudit.user` `lazy="joined"`); inspection/sample lists already `.join(FSO, ...)`.

---

## Deepening Tasks (Architectural Refactoring) — ✅ ALL DONE

> Source: `REFACTORING_PLAN.md`. Goal: Increase Module Depth in 5 shallow areas.
> **All five (D1–D5) are fully implemented.** See AGENTS.md §8 for the implementation order and current module-depth metrics.

### D1: Extract CaseResolver — ✅ DONE

- **Files:** `app/shared/case_resolver.py` (implemented)
    - `app/document_viewer/routes.py`
    - `app/version_control/routes.py`
    - `app/evidence/routes.py`
    - `app/search/indexer.py`
    - `app/annexure/routes.py`
    - `tests/test_case_resolver.py` (new test suite)
- **Detailed Implementation Plan:**
    1. Create `app/shared/case_resolver.py`:

        ```python
        @dataclass
        class ResolvedCase:
            case_id: int
            adjudication_id: int | None
            case_type: str  # 'case_file' or 'adjudication'
            case_number: str
            record: Any

        class CaseResolver:
            @staticmethod
            def resolve(case_id: int, kind: str | None = None) -> ResolvedCase | None:
                # Unifies resolution across CaseFile and Adjudication models
        ```

    2. Replace duplicate resolution functions in `document_viewer`, `version_control`, `evidence`, `search`, and `annexure`.
- **Acceptance Criteria & Test Plan:** All 5 modules utilize `CaseResolver.resolve()`. `pytest tests/test_case_resolver.py` passes.

---

### D2: Extract DocumentSaveCoordinator — ✅ DONE

- **File:** `app/services/document_lifecycle.py` (implemented)
    - `app/document_viewer/routes.py`
    - `tests/test_document_lifecycle.py` (new test suite)
- **Detailed Implementation Plan:**
    1. Create `DocumentSaveCoordinator` class encapsulating HTML cleanup, snapshot versioning via `VersionService`, audit logging, and payload validation.
    2. Replace 5 private inline helper functions in `app/document_viewer/routes.py`.
- **Acceptance Criteria & Test Plan:** Saving documents delegates cleanly to `DocumentSaveCoordinator`. Unit tests pass.

---

### D3: Complete PDFAssemblyEngine Consolidation — ✅ DONE

- **File:** `app/pdf_assembly/engine.py` (implemented)
    - `app/utils/pdf_utils.py` (backward-compatible re-exports)
    - `app/adjudication/routes.py`
    - `app/case_file_generator/tasks.py`
    - `app/document_viewer/renderer.py`
    - `app/document_viewer/routes.py`
    - `tests/test_pdf_assembly_engine.py`
- **Detailed Implementation Plan:**
    1. Consolidate PDF generation and bookmarking logic from `pdf_utils.py` and `pdf_assembly/__init__.py` into `app/pdf_assembly/engine.py`.
    2. Re-export legacy functions in `app/utils/pdf_utils.py` to maintain backward compatibility.
- **Acceptance Criteria & Test Plan:** Callers render PDFs via `PDFAssemblyEngine`. Merged test suite passes.

---

### D4: Extract InspectionPhotoService — ✅ DONE

- **File:** `app/inspection/photo_service.py` (implemented)
    - `app/inspection/routes/photo_routes.py`
    - `app/inspection/routes/__init__.py`
    - `tests/test_inspection_photo_service.py` (new test suite)
- **Detailed Implementation Plan:**
    1. Extract EXIF parsing, PIL image validation, secure storage, OCR dispatch, and geo verification from `photo_routes.py` into `InspectionPhotoService`.
    2. Reduce `photo_routes.py` to a thin HTTP adapter layer.
- **Acceptance Criteria & Test Plan:** `photo_routes.py` contains only HTTP request handling. `tests/test_inspection_photo_service.py` passes.

---

### D5: Extract DocumentCaseManager — ✅ DONE

- **File:** `app/shared/document_case_manager.py` (implemented)
    - `app/case_file_generator/routes.py`
    - `app/adjudication/routes.py`
    - `tests/test_document_case_manager.py`
- **Detailed Implementation Plan:**
    1. Create parameterized `DocumentCaseManager` class handling common CRUD, document generation, TOC generation, and annexure renumbering for both `CaseFile` and `Adjudication`.
    2. Refactor `case_file_generator/routes.py` and `adjudication/routes.py` to inherit from / delegate to `DocumentCaseManager`.
- **Acceptance Criteria & Test Plan:** Over 1,200 lines of duplicated route code eliminated. Parametrized tests pass cleanly.

---

### Test Environment Issues (Discovered During 2026-08-06 PR Verification)

> During the verification of committing all 14 open Dependabot PRs (commit `a746104`), the full test suite (832 tests, 22-min runtime) was executed. **783 tests passed**. The 28 failures + 21 errors are all environment-specific — **not** caused by the dependency changes (the updated package versions were already installed before the commit). These items document the gaps that must be addressed for a fully green test run in this environment.

> **Status (2026-08-07):** ENV-1 and ENV-4 are **RESOLVED** — both were product-code defects (unregistered blueprints + never-committed Phase 13 UI + orphaned audit routes), not environment gaps. `test_timeline.py` 21/21 and `test_food_cell_do_intimation.py` 15/15 are green. ENV-2/3/5/6/7/8 remain open (SQLite-vs-PG markers, Redis/Celery config, cv2 dependency, dependabot rebase, Python 3.12).

- [x] **ENV-1: Timeline Route Registration Bug — ✅ RESOLVED (2026-08-07).** `test_timeline.py` — **21/21 pass**. **Original (incorrect) root-cause claim:** an uncommitted `health_bp` change in `app/__init__.py` interfered with blueprint initialization. **Verified reality:** the health integration was complete and correct; the real root cause was that `timeline_bp` was **never registered** in `create_app()` (confirmed via `git log -S 'timeline_bp'` — zero history), so all `/timeline/*` routes 404'd. Secondary gaps found: (a) the Phase 13 UI entry points (global case-picker modal in `base.html`, "Case Timelines" panels, editor/search/entry-point buttons, sample-detail JSON `case_id`/`timeline_url`) were **never committed** despite task.md claiming 21/21; (b) `app/audit/__init__.py` never imported `routes.py`, so `/admin/audit-log` 404'd; (c) stale `edit_case_file`/`edit_adjudication` `url_for` names in annexure/evidence templates caused `BuildError`. **Fix applied (2026-08-07):** registered `timeline_bp`; built the global picker modal + all UI entry points; added `from app.audit import routes`; fixed the stale url_for names; added `case_id`/`timeline_url` to `GET /sample/<id>`; `DocumentCaseManager.index()` now passes recent cases for the panels. Regression suites green (annexure 22, document_viewer 51+, search 56, route-collisions 2, step1-5, validation, cross-ref, toc).

- [ ] **ENV-2: SQLite vs PostgreSQL Incompatibility — Concurrency Tests** — `test_concurrency_inspection.py` (4 failures). Tests assert HTTP 409 on concurrent modification, but get HTTP 500. Root cause: `StaleDataError` is raised by PostgreSQL advisory locks / row-level locking, but **SQLite does not raise `StaleDataError`** on concurrent writes — it silently overwrites or returns no error. The S9a guard code is correct (returns `jsonify({...}), 409` tuple), but the underlying DB doesn't trigger the exception. Fix: add `@pytest.mark.skipif(not _is_postgres(), reason="requires PostgreSQL advisory locks")` markers, or configure the test environment to use PostgreSQL.

- [ ] **ENV-3: SQLite vs PostgreSQL — Backup/Export Tests** — `test_case_backup.py` (14 errors at setup). All tests fail at fixture setup because they require PostgreSQL-specific features (JSON export serialization, zip archive operations, Celery beat schedule configuration). Fix: same `skipif` markers as ENV-2, or run with PostgreSQL in CI.

- [x] **ENV-4: Missing OCR Template — ✅ RESOLVED (2026-08-07).** `test_food_cell_do_intimation.py` — **15/15 pass**. **Original (incorrect) root-cause claim:** the template `food_cell/do_intimation.html` was missing. **Verified reality:** the template **exists** on disk; the `TemplateNotFound` was caused by `food_cell_bp` being **never registered** in `create_app()` — an unregistered blueprint's `templates/` folder never enters Jinja's search path. **Fix applied (2026-08-07):** `app.register_blueprint(food_cell_bp, url_prefix="/food-cell")` in `app/__init__.py`. No template was created (none was needed).

- [ ] **ENV-5: Missing Redis/Celery for Food Cell Sync** — `test_food_cell_do_intimation.py` (7 errors in `TestSyncForwarding`, `TestDownloadEndpoint`, `TestStatusEndpoint`, etc.). The post-save Celery task `send_do_intimation.delay()` requires a running Redis broker. Fix: configure `REDIS_URL` in the test environment, or mock Celery task dispatch with `celery_app.conf.task_always_eager = True`.

- [ ] **ENV-6: Missing Optional Dependency — cv2/OpenCV** — `test_ocr_pipeline.py` (7 failures). `ModuleNotFoundError: No module named 'cv2'` — the OCR image preprocessing pipeline (`app/ocr_pipeline/preprocessing.py`) requires OpenCV for grayscale conversion, denoising, adaptive thresholding, and contrast enhancement. Fix: install `opencv-python` in the test/CI environment, or add `opencv-python` as an optional dependency in `pyproject.toml` under `[project.optional-dependencies.ocr]`.

- [ ] **ENV-7: Dependabot Branch Staleness** — All 14 dependabot PR branches are based on an old main commit (`89d7535`), far behind the current main (`0b5827b`). This causes `git diff main..branch` to show massive diffs (650+ files) because the branches only contain the version bump, but the base is stale. Fix: configure `.github/dependabot.yml` to use `target-branch: main` with automatic rebasing, or rebase branches manually before review.

- [ ] **ENV-8: Python Version Mismatch** — Environment runs Python 3.11.15, but `pyproject.toml` declares `requires-python = ">=3.12"`. Some tests may behave differently on 3.11 vs 3.12. Fix: use Python 3.12+ in the test environment.

### ENV-9: Upstash QStash Webhook Signing Key Warning — ✅ RESOLVED IN PRODUCTION (2026-08-07)

> **Warning observed:** `WARNING in routes: QStash signing keys not configured; rejecting webhook` (runtime log from `app/tasks_webhook/routes.py:48`). Investigated 2026-08-06/07 via `scout` agent context-gathering.

**Symptom:** When QStash (Upstash) delivers a webhook to `POST <PUBLIC_BASE_URL>/tasks/run/<task_name>`, the handler `run_task()` checks `os.environ.get("QSTASH_CURRENT_SIGNING_KEY")` and `os.environ.get("QSTASH_NEXT_SIGNING_KEY")`. If either is missing, it logs the warning and returns HTTP 503. QStash then retries up to 3 times (configured at publish) before marking the message as failed.

**Root cause:** Configuration gap — not a logic bug. The signing keys were declared as `sync: false` in `render.yaml` (manual provisioning in the Render Dashboard) and empty in `.env.example`. If they are not pasted into the Render Dashboard for the live production service, every QStash delivery 503s. Locally, `load_dotenv()` (in `create_app()` at `app/__init__.py:111`) loads the real keys from `.env`, so the warning did NOT fire in this dev environment — confirmed by runtime check.

**Env var requirements (4 total):**

| Var                          | Purpose                            | Checked by                        |
| ---------------------------- | ---------------------------------- | --------------------------------- |
| `QSTASH_TOKEN`               | Publisher auth (QStash API client) | `qstash_configured()` + `/health` |
| `QSTASH_CURRENT_SIGNING_KEY` | Webhook verification (current)     | `run_task()` receiver             |
| `QSTASH_NEXT_SIGNING_KEY`    | Webhook verification (rotation)    | `run_task()` receiver             |
| `PUBLIC_BASE_URL`            | Base URL → builds webhook URL      | `qstash_configured()` + `/health` |

**Provisioning per environment:**

- **Production (Render):** ✅ DONE — all 4 env vars pasted into the Render Dashboard for the `food-adjudication-portal` web service. `GET /health` should now report `qstash: "configured"` and QStash deliveries will verify successfully.
- Local dev (`.env`, NOT committed): all 4 present with real values.
- `.env.example` (repo template): all 4 **empty** (by design — never commit real keys).
- `render.yaml` (web + worker services): all 4 declared `sync: false` → manually provisioned in Render Dashboard.

**Verification steps:**

1. Confirm `GET /health` returns `"qstash": "configured"` in production after redeploy.
2. Trigger a bill/case-file PDF generation from the UI and poll `GET /tasks/status/<message_id>` — should transition `pending → running → completed`.
3. Check the QStash console for successful delivery logs (no 503s).

**Secondary issues identified (code-level, low priority):**

- **ENV-9a: Inconsistent env-var checks** — ✅ DONE (2026-08-07). Aligned `run_task()`'s env-var check with `qstash_configured()` so the webhook receiver and `/health` report consistently. Previously the receiver checked only the 2 signing keys; now it uses `qstash_configured()` (all 4 vars) → returns 503 when any QStash var is missing, matching `/health`. Also narrowed `record` type in `task_status()` with `assert record is not None` for static-analysis clarity. `pytest tests/test_qstash_webhook.py` — **12/12 pass**.
- **ENV-9b: Clock tolerance = 0** — ✅ DONE (2026-08-07). `Receiver.verify()` defaults to `clock_tolerance=0`. Any clock skew between QStash's delivery nodes and the production host causes valid webhooks to 401 on `exp`/`nbf` claims. Fixed by passing `clock_tolerance=5` to `Receiver.verify()` in `app/tasks_webhook/routes.py`.
- **ENV-9c: `url` binding omitted** — `run_task` calls `receiver.verify(signature, body)` without `url=`, so the JWT `sub`/destination-URL claim is NOT validated. This is a **deliberate documented tradeoff** (see `app/tasks_webhook/routes.py:54-58`): prevents false 401s behind `ProxyFix`/custom domains/trailing-slash drift. HMAC + `exp`/`nbf` checks are the actual security boundary. No action needed unless stricter URL-binding is required.
- **ENV-9d: 4 failing tests + DLQ gap** — ✅ DONE (2026-08-07). `tests/test_qstash_webhook.py` had 4 pre-existing test failures (302 redirects instead of expected 400/403/404): `TestTaskStatus::test_unknown_message_id_returns_404`, `TestDownloadTaskFile::test_missing_path_returns_400`, `test_path_traversal_blocked`, `test_nonexistent_file_returns_404`. Root cause: `GET /tasks/status/<id>` and `GET /tasks/download` are NOT in `public_endpoints` (only `tasks_webhook.run_task` is — `app/__init__.py:286-287`), so the `require_login` gate redirects unauthenticated requests to `/auth/login` → 302. These endpoints are polled by the authenticated frontend, so they should remain login-gated. Fix: added `auth_client` fixture (logs in via `POST /auth/login` + seeded `User`, same pattern as `test_auth_change_password.py`) and updated the 4 tests to use it. Also added the **DLQ gap fix** — QStash's `failure_callback` parameter is now passed to `publish_json()` in `publish_task()`, pointing to the new `POST /tasks/failed/<task_name>` route which verifies the same Upstash-Signature, updates Redis status to `"failed"` with the error message, and logs an error for operator alerting. Previously, a permanently-failed message left Redis stuck at `"pending"` forever with no signal. `pytest tests/test_qstash_webhook.py` — **16/16 pass** (was 8/12; added 4 `TestDeliveryFailed` tests + 4 `auth_client`-based tests).

**Files:** `app/tasks_webhook/routes.py:44-49` (warning site), `app/utils/qstash_client.py:106-113` (`qstash_configured()`), `app/__init__.py:111` (`load_dotenv`), `app/__init__.py:286-287` (public_endpoints), `app/health/routes.py:52-54` (health check), `render.yaml:55-64` (env var declarations), `.env.example` (empty values).

### ENV-10: Render free-tier RAG inference — Modal hosting + Qdrant-side BM25 — ⚠️ CODE DONE, DEPLOY PENDING (2026-08-16)

> **Problem:** Render free tier (512 MB RAM / 0.1 CPU) cannot load any local torch model — `all-mpnet-base-v2` (~420 MB fp32) alone exceeds the budget, and the CE reranker (~90 MB) pushes it over. **Decision:** run **zero local models** in production: dense embeddings + CE reranking on **Modal** (free $30/mo credits, serverless, scales to zero), BM25 sparse computed **in-cluster by Qdrant** (`Qdrant/bm25` — verified live 2026-08-16, free on the free tier). The HF Serverless Inference API (`api-inference.huggingface.co`) was **decommissioned** (410/404 since late 2025 — replaced by Inference Providers which only serve an allowlisted catalog; this custom CE is not in it), so the old `mode="serverless"` path and `scripts/test_hf_inference.py` are **dead ends** — do not debug them.

**Code state — ALL IMPLEMENTED & TESTED (2026-08-16):**

- `modal_deploy/app.py` — Modal app hosting `POST /rerank` (TEI-compatible: `{"query", "texts"}` → `[{"index", "score"}]`, backed by `sumanksaha/Foodmultidomain`) + `POST /embed` (`{"texts"}` → `{"vectors": [[...]]}`, backed by `all-mpnet-base-v2`, no normalization — matches the 768-dim index) + `GET /healthz`. Models downloaded at **image build time** (`.run_function(_download_models, secrets=[hf_secret])`), so containers start warm. Requires a workspace Secret named `hf-token` (Hugging Face template, `HF_TOKEN` = read token with gate accepted). Deploy: `modal deploy app.py` from `modal_deploy/`. README: `modal_deploy/README.md`.
- `app/rag/retrieval/remote_embedder.py` — `RemoteEmbedClient` (encoder-seam HTTP client, lazy local fallback) — mirrors `remote_reranker.py`.
- `app/rag/retrieval/dense_retriever.py` — `_get_remote_embedder()` / `embed_query` branch behind `RAG_EMBED_ENDPOINT` (covers both the dense-only search path and the server-side hybrid fusion path).
- `app/rag/qdrant_client.py` — `BM25_TEXT_MODEL = "Qdrant/bm25"` + `search_sparse_text()` + `hybrid_search_text()` (Document-query prefetch + RRF; no local fastembed at query time).
- `app/rag/retrieval/sparse_retriever.py` — `server_bm25` constructor flag → `search_sparse_text` path.
- `app/rag/retrieval/hybrid_retriever.py` — server-side fusion uses `hybrid_search_text(dense_vector, query)` when `sparse.server_bm25` is on.
- `app/rag/tasks.py` — `_qdrant_bm25_enabled()` + `SparseRetriever(..., server_bm25=_qdrant_bm25_enabled())`.
- `app/__init__.py` — config: `RAG_EMBED_ENDPOINT`, `RAG_EMBED_TOKEN`, `RAG_EMBED_TIMEOUT`, `RAG_EMBED_REMOTE_FALLBACK`, `RAG_QDRANT_BM25`.
- Tests: `tests/test_remote_embedder.py` (17), `tests/test_qdrant_bm25.py` (13), conftest autouse `_rag_remote_inference_env` isolation. All green; live cluster probe returns §50/§51/§58 for the penalty query.

**Step 1 — Deploy models to Modal (from a networked machine):**

```bash
pip install modal
modal setup                 # browser auth — no secrets typed
cd modal_deploy
modal deploy app.py         # first build ~minutes (torch + both models baked in)
```

- Modal account: sign up at modal.com, verify phone, add a card to unlock the **$30/month free credits**, then set a **monthly spend limit of $30** (Settings) so it can never exceed the credit.
- **Secret:** Modal → Secrets → Hugging Face template → name it **`hf-token`** → `HF_TOKEN` = the read token (account must have accepted the `sumanksaha/Foodmultidomain` gate conditions on the model page).
- Deploy prints three URLs like `https://<workspace>--nsa-legal-inference-{rerank,embed,healthz}.modal.run` — record the `rerank` and `embed` ones.
- Verify (cold start 10–30 s on first call):

```bash
curl -X POST https://<workspace>--nsa-legal-inference-embed.modal.run -H "Content-Type: application/json" \
  -d '{"texts": ["penalty for selling substandard food"]}'            # → {"vectors": [[768 floats]]}
curl -X POST https://<workspace>--nsa-legal-inference-rerank.modal.run -H "Content-Type: application/json" \
  -d '{"query": "penalty for selling substandard food", "texts": ["Section 50: General penalty for unsafe food"]}'  # → [{"index": 0, "score": ~4.2}]
```

**Step 2 — Set env vars on Render (web service + Celery worker; Dashboard → Environment, or add to `render.yaml` with `sync: false`):**

| Var | Value to enter | Why |
| --- | --- | --- |
| `RAG_EMBED_ENDPOINT` | `https://<workspace>--nsa-legal-inference-embed.modal.run` | dense queries embed over HTTP — no local torch |
| `RAG_EMBED_TOKEN` | *(empty)* | Modal endpoint is public; Space/endpoint auth uses its own secret |
| `RAG_EMBED_TIMEOUT` | `5` | per-request timeout (s) |
| `RAG_EMBED_REMOTE_FALLBACK` | **`false`** | ⚠️ required — `true` would lazily build local torch on failure and OOM 512 MB; `false` degrades to sparse-only |
| `RAG_RERANKER_ENDPOINT` | `https://<workspace>--nsa-legal-inference-rerank.modal.run` | CE head scores over HTTP |
| `RAG_RERANKER_MODE` | `tei` | TEI `/rerank` contract (the `serverless` mode targets the decommissioned API — do not use) |
| `RAG_RERANKER_TOKEN` | *(empty)* | — |
| `RAG_RERANKER_REMOTE_FALLBACK` | **`false`** | ⚠️ required — `true` would build local CE on failure and OOM; `false` degrades to sec_act features-only |
| `RAG_QDRANT_BM25` | **`true`** | Qdrant computes BM25 in-cluster — removes the last local model (fastembed) |
| `RAG_ENSEMBLE_RERANK` | `true` | sec_act features local (pure Python) + remote CE head |

**Step 3 — Verify in production:**

1. Redeploy both Render services, then run a `/rag/query` (or `GET /rag/health`) — logs should show the CE head + dense embedding scoring over HTTP, no torch import errors, no OOM kills.
2. Confirm graceful degradation: stop the Modal app (or use a wrong URL) → retrieval still returns results (features-only / sparse-only), never crashes.
3. Watch Render memory in the dashboard — should stay well under the 512 MB free-tier ceiling.

**Rollback:** set `RAG_EMBED_ENDPOINT` / `RAG_RERANKER_ENDPOINT` empty and `RAG_QDRANT_BM25=false` → returns to local-torch behavior (works only if Render is upgraded off the free tier). `RAG_EMBED_REMOTE_FALLBACK`/`RAG_RERANKER_REMOTE_FALLBACK` default `true` for dev machines; Render must keep them `false`.

**Cost guardrail:** free tier = $30/mo Modal credits; ~1 s embed + ~2 s rerank ≈ $0.00006/query ≈ 500 K queries/month. Qdrant BM25 is free (deterministic, in-cluster).

**Acceptance criteria:** `modal deploy` succeeds and both curl checks above return correct shapes; all five RAG env vars set on web + worker; `/rag/query` returns grounded results in production with Render memory < 512 MB and no torch in the process; full offline suite (incl. `tests/test_remote_embedder.py` 17 + `tests/test_qdrant_bm25.py` 13) green.

**Files:** `modal_deploy/` (new), `app/rag/retrieval/remote_embedder.py` (new), `app/rag/retrieval/dense_retriever.py`, `app/rag/qdrant_client.py`, `app/rag/retrieval/sparse_retriever.py`, `app/rag/retrieval/hybrid_retriever.py`, `app/rag/tasks.py`, `app/__init__.py`, `.env` (real URLs now set), `.env.example`, `tests/test_remote_embedder.py`, `tests/test_qdrant_bm25.py`, `tests/conftest.py`.

> **DEPLOYED ✅ (2026-08-16):** `modal deploy app.py` succeeded from the dev sandbox (Modal CLI 1.5.4 — note the SDK renames: `container_idle_timeout`→`scaledown_window`, `web_endpoint`→`fastapi_endpoint`, `allow_concurrent_inputs`→`@modal.concurrent` on the class, `@app.cls()` outermost). **Live URLs: `https://sumanksaha--rerank.modal.run`, `https://sumanksaha--embed.modal.run`, `https://sumanksaha--healthz.modal.run`.** Verified: `/embed` returns 768-dim vectors; `/rerank` ranks Section 50 #1 for the penalty query at −0.82 (matches the local checkpoint's parity reference −0.821). `hf-token` secret created via `modal secret create hf-token HF_TOKEN=<read token>`. `.env` now points at the real URLs. **Remaining: paste the 8 env vars into the Render Dashboard (web + worker) — Step 2 table above — then verify `/rag/query` in production.**

### ENV-11: LangGraph agent pipeline (M3+M4) — ✅ DONE (2026-08-16)

> **Task:** Implement the LangGraph agent layer from `docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md` Part C — a self-correcting RAG pipeline with a conditional groundedness retry loop, behind an opt-in flag.

**Delivered (all tests green):**

- `app/rag/agent/state.py` — `RAGState` TypedDict (query, query_type, chunks, retry_count, audit_trail, groundedness, response, log_id, expanded_query, max_retries) + `initial_state()`; all chunks stored as JSON-serializable dicts (M5 checkpointing-ready).
- `app/rag/agent/nodes.py` — thin adapters over the existing pipeline: `classify_node` (QueryClassifier, falls back to `general`), `retrieve_node` (`run_retrieval_pipeline` — already uses remote CE + Qdrant-side BM25), `evidence_node` (behind `ENABLE_EVIDENCE_SELECTOR`), `generate_node` (`run_generation_pipeline` with in-state chunks), `verify_node`, `expand_query_node` (reuses `GroundedLLMClient`, stub-LLM testable, keeps original query on failure), `finalize_node` (merges `pipeline: "agent"` + retry/audit metadata).
- `app/rag/agent/graph.py` — `StateGraph`: classify → retrieve → [evidence] → generate → verify → conditional → expand_query → retrieve loop / finalize → END. Guard: `groundedness < 0.7` and `retry_count < max_retries` (default 2). Compiled once at import; `langgraph` imported only here (lazy — legacy pipeline untouched).
- `app/rag/agent/routes.py` — `POST /api/rag/query/agent` on `rag_bp` (registered via `app/rag/__init__.py`); 400 validation / 503 RAG-disabled / 503 langgraph-missing; **flag off → delegates to the legacy `query()` route** (zero behaviour change until flip); flag on → runs the graph.
- Config: `RAG_USE_AGENT_PIPELINE` (default `false`) in `app/__init__.py` + `.env.example`; `langgraph>=1.0.0` added to `pyproject.toml` (lazy import).
- Tests: `tests/test_rag_agent_state.py` (5) + `test_rag_agent_nodes.py` (17) + `test_rag_agent_graph.py` (12) + `test_rag_agent_routes.py` (7) = **41 new tests, all passing** (stub-LLM, pipeline entry points monkeypatched — no Qdrant/network/torch). Regression: `test_rag_routes.py` + `test_rag_tasks.py` + `test_rag_e2e.py` + `test_route_collisions.py` 33/33 green.

**Flip plan (rollout §8):** A/B `pipeline` (`legacy`/`agent`) on the frozen 150-question benchmark before setting `RAG_USE_AGENT_PIPELINE=true` in prod. M5 (checkpointing + HITL `interrupt()`) deferred — pin latest patched `langgraph-checkpoint-postgres` first.

### Developer Environment Notes

> Tooling limitations encountered during the PR verification task and workarounds used:

- **`gh` CLI not installed**: Fell back to `https://api.github.com/repos/.../pulls` endpoint with `curl.exe -s -H "Accept: application/vnd.github.v3.diff"`.
- **GitHub API rate limit (60/hour unauthenticated)**: Fetched all 14 PR diffs in a single parallel batch. No `GITHUB_TOKEN`/`GH_TOKEN` env var was available.
- **PowerShell `&&`/`||` not supported**: Used `;` separators and `if ($?) {}` constructs instead.
- **`curl` aliased to `Invoke-WebRequest`**: Used `curl.exe` for explicit `curl` binary.
- **`tail`/`head` not available**: Used `Select-Object -First N` / `Select-Object -Last N` instead.
- **30-second shell command timeout**: Used `Start-Process -WindowStyle Hidden` with output redirected to files for long-running test suites.
- **CRLF line endings causing git binary detection**: Used `git diff --text` to force text diffs. Consider adding `.gitattributes` with `* text=auto` to normalize.

## Priority 5 — Cloudinary Testing & Hardening

- **Target Files to Edit/Create:**
    - `tests/test_storage_cloudinary.py` (new test file)
    - `app/utils/storage.py` (retry logic)
    - `app/health/routes.py` (health check)
- **Detailed Implementation Plan:**
    1. Add unit tests for `extract_cloudinary_public_id` and mock upload/destroy operations.
    2. Add `@retry` decorators via `tenacity` to Cloudinary network calls in `app/utils/storage.py`.
    3. Implement `GET /health/cloudinary` health check endpoint.
    4. Support parsing single `CLOUDINARY_URL` environment variable string.
- **Acceptance Criteria & Test Plan:** Cloudinary network failures retry gracefully; `pytest tests/test_storage_cloudinary.py` passes.

---

## Priority 7 — Multi-Target Sheets Redundancy (Airtable + MS Excel) — ✅ COMPLETE (2026-08-07)

- **Goal & Rationale:** Eliminate Google Sheets as a single point of failure for data backup by adding Airtable and Microsoft Excel Online as parallel real-time sync targets, with R2 CSV exports of each service for redundant restore when any (or all) services are unavailable. **Note:** MS Excel Online sync is implemented but **dormant** — `ENABLE_EXCEL_SYNC=false` in `.env.example` and `render.yaml` (no Microsoft 365 / Azure AD credentials available). Airtable sync is **active** (`ENABLE_AIRTABLE_SYNC=true`).
- **Dependencies to Add:** `pyairtable>=1.0.0`, `msal>=1.0.0` (verify `requests` already present)

**Completion Summary (2026-08-07):**

- `app/utils/sync.py` — 12 new functions/variables for the restore chain: `restore_from_airtable_csv()`, `restore_from_excel_csv()`, `restore_from_sheets_csv()`, `restore_if_empty()` (orchestrates Airtable → Excel → Sheets), `trigger_backup()` (delegates to `backup_coordinator.run_backup()`), `_restore_from_records()` (dispatches CSV records by module, strips Airtable metadata), `_restore_module()` (maps CSV rows to SQLAlchemy models with type coercion), `_build_column_map()`, `_parse_csv_value()` (Integer/Float/Boolean/Date/DateTime/BigInteger/String coercion), `_is_empty_sqlite_db()` (fixed to use `db.metadata.tables` instead of `inspector.get_table_names()` to exclude `alembic_version`), plus module-level maps: `_AIRTABLE_TABLE_MAP`, `_WORKSHEET_MAP`, `_SHEETS_RESTORE_MAP` (6 modules each). Added `logging` + `logger`.
- `app/__init__.py` — Fixed doubled indentation on all Priority 7 config lines (were 8 spaces, now 4). Config flags and QStash schedule registration (02:00 UTC, gated behind `ENABLE_BACKUP_SCHEDULE`) confirmed correct.
- `app/settings/routes.py` — Restored the original `backup_restore` route (was broken: decorator + `def` line were consumed during an earlier edit). Added `backup_redundant_to_r2` POST route and `backup_redundant_to_r2_status` GET route. Compiles cleanly.
- `tests/test_priority7_redundancy.py` — 43 tests, all passing. Covers CSV parsing, backup coordinator (all/partial/all-fail with isolation verification), restore chain edge cases, `_is_empty_sqlite_db`, settings routes, QStash schedule registration, config flags, and the standalone backup script.

**Test Results:**

```
43 passed in 13–16 seconds
No regressions: test_route_collisions + test_storage = 53 passed, test_food_cell = passing
```

**Key implementation findings:**

1. `_is_empty_sqlite_db` bug: Using `inspector.get_table_names()` returns ALL SQLite tables including `alembic_version`, causing false "DB is not empty" results. Fixed to iterate `db.metadata.tables` instead.
2. `backup_coordinator.run_backup()` uses lazy imports — tests must patch at the source module (`app.services.sheets_sync.export_sheets_to_r2`), not at the coordinator.
3. QStash `publish_recurring` returns `{"mode": "disabled"}` when `QSTASH_TOKEN` env var is absent — tests verify graceful degradation.
4. Module-scoped fixtures reduced test runtime from ~200s to ~13s because `create_app()` is expensive (~17s cold).
5. Temp fix scripts (`_fix_*.py`, `_write_*.py`, `_append_*.py`) cleaned up after implementation.

**Note:** All planned subsections (A–F below) have been implemented. The originally planned separate test files (`test_airtable_sync.py`, `test_excel_sync.py`, `test_restore_redundant.py`, `test_airtable_base_rotation.py`) were consolidated into the single `tests/test_priority7_redundancy.py` (43 tests) for better fixture sharing and reduced `create_app()` overhead (module-scoped fixtures cut runtime from ~200s to ~13s).

### A. Airtable Sync Service

- **Goal & Rationale:** Push records to Airtable in parallel with Google Sheets. Handle Airtable's 1,200 records-per-base free-tier limit via automatic base rotation. Export all bases to R2 CSV for backup/restore.
- **Target Files to Edit/Create:**
    - `pyproject.toml` (add `pyairtable` dependency)
    - `.env.example` (add `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID`)
    - `app/services/airtable_sync.py` (new file – ~350 lines)
    - `app/models/auth.py` (add `AirtableBaseMap` model)
    - `migrations/versions/xxxx_add_airtable_base_map_table.py` (Alembic migration)
    - `app/__init__.py` (init base tracking table on startup)
    - Extend route handlers: `app/case_file_generator/routes.py`, `app/adjudication/routes.py`, `app/inspection/routes/inspection_routes.py`, `app/sample/routes.py`, `app/bill_generator/routes.py` (add `sync_to_airtable()` calls)
    - `tests/test_airtable_sync.py` (new test file, 20 tests)
- **Detailed Implementation Plan:**
    1. **Dependencies:** Add `pyairtable>=1.0.0` to `pyproject.toml` under `[project.dependencies]`.
    2. **Environment Variables:** Add to `.env.example`:

        ```
        AIRTABLE_API_KEY=keyXXXXXXXXXXXXXX
        AIRTABLE_BASE_ID=appXXXXXXXXXXXXX
        # NOTE: Additional bases auto-created when primary hits 1,200-record limit
        ```

    3. **Database Schema (`app/models/auth.py` + migration):**

        ```python
        class AirtableBaseMap(db.Model):
            __tablename__ = "airtable_base_map"
            id = db.Column(db.Integer, primary_key=True)
            record_id = db.Column(db.Integer, nullable=False)
            module = db.Column(db.String(64), nullable=False)
            airtable_record_id = db.Column(db.String(256))
            airtable_base_id = db.Column(db.String(256))
            airtable_table_name = db.Column(db.String(256))
            created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
        ```

    4. **Sync Service (`app/services/airtable_sync.py`):**
        - `get_airtable_client()` — lazy `pyairtable.Api()` init from `AIRTABLE_API_KEY`.
        - `AIRTABLE_TABLE_MAP` — maps module names to Airtable table names (mirrors `WORKSHEET_MAP`).
        - `AIRTABLE_FIELD_MAP` — maps column names to Airtable field labels (mirrors `SHEET_COLUMNS`).
        - `sync_to_airtable(module, row_dict, db_record_id)` — finds base with capacity via `_get_or_create_active_base()`, inserts record, tracks mapping.
        - `_get_or_create_active_base(module)` — iterates bases, checks record count via Airtable metadata API, returns first with capacity (<1,100 records) or creates new.
        - `_create_airtable_base(module, batch_num)` — creates base via `POST https://api.airtable.com/v0/meta/bases` with schema from `SHEET_COLUMNS`.
        - `_track_airtable_sync(db_record_id, module, airtable_record_id, base_id)` — inserts into `AirtableBaseMap`.
        - `export_airtable_all_bases_to_r2()` — iterates all bases, downloads all records, combines into one CSV, uploads to `nsa_backups/airtable_csv/`.
    5. **Route handler integration:** Add `sync_to_airtable("module", row_dict, record.id)` after existing `sync_to_sheets()` calls.
- **Acceptance Criteria & Test Plan:** `sync_to_airtable()` pushes records to Airtable on create/update. At 1,100 records, a new base is auto-created. Mapping tracked in `AirtableBaseMap`. `export_airtable_all_bases_to_r2()` writes combined CSV. `pytest tests/test_airtable_sync.py` — 20 tests pass.

---

### B. Microsoft Excel Online Sync Service

- **Goal & Rationale:** Push records to Excel Online (via Microsoft Graph API) in parallel with Google Sheets and Airtable. Export worksheets to R2 CSV for backup/restore.
- **Target Files to Edit/Create:**
    - `pyproject.toml` (add `msal>=1.0.0`; verify `requests` present)
    - `.env.example` (add `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_DRIVE_ID`, `MS_SPREADSHEET_ID`)
    - `app/services/excel_sync.py` (new file – ~200 lines)
    - Extend route handlers: `app/case_file_generator/routes.py`, `app/adjudication/routes.py`, `app/inspection/routes/inspection_routes.py`, `app/sample/routes.py`, `app/bill_generator/routes.py` (add `sync_to_excel()` calls)
    - `tests/test_excel_sync.py` (new test file, 20 tests)
- **Detailed Implementation Plan:**
    1. **Dependencies:** Add `msal>=1.0.0` to `pyproject.toml`; verify `requests` is present.
    2. **Environment Variables:**

        ```
        MS_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        MS_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        MS_CLIENT_SECRET=your-client-secret
        MS_DRIVE_ID=04D8D8D8D8D8D8D8D8D8D8D8D8D8D8
        MS_SPREADDHEET_ID=04D8D8D8D8D8D8D8D8D8D8D8D8D8D8
        ```

    3. **Authentication (`app/services/excel_sync.py`):**
        - `get_excel_token()` — uses `msal.ConfidentialClientApplication` with client credentials flow.
        - `get_excel_graph_session()` — `requests.Session` with Bearer token header.
        - Returns `None` if any env var is missing.
    4. **Sync Function:**
        - `sync_to_excel(worksheet_name, row_dict)` — appends a row via Graph API:
          `POST https://graph.microsoft.com/v1.0/me/drive/items/{spreadsheet_id}/workbook/worksheets('{name}')/rows`
        - Reuses `WORKSHEET_MAP` from `sheets_sync.py`.
        - Returns `True` on success, `False` on any failure.
    5. **CSV Export for R2 Backup:**
        - `export_excel_to_r2()` — reads worksheet data via `GET .../usedRange/$value`, builds CSV, uploads to `nsa_backups/excel_csv/`.
    6. **Route handler integration:** Add `sync_to_excel("WorksheetName", row_dict)` after existing `sync_to_sheets()` calls.
- **Acceptance Criteria & Test Plan:** `sync_to_excel()` appends rows to Excel worksheets via MS Graph API. `export_excel_to_r2()` writes CSV exports. `pytest tests/test_excel_sync.py` — 20 tests pass.

---

### C. R2 Backup Script (QStash-triggered)

- **Goal & Rationale:** Daily export of all three services (Sheets, Airtable, Excel) to R2 as CSV. Triggered by QStash webhook (no Celery worker needed).
- **Target Files to Edit/Create:**
    - `scripts/backup_redundant_sheets.py` (new file – ~50 lines)
    - `app/settings/routes.py` (extend with admin-only API endpoint)
    - `.github/workflows/backup-redundant.yml` (optional fallback schedule)
- **Detailed Implementation Plan:**
    1. **Backup Script (`scripts/backup_redundant_sheets.py`):**
        - `run_backup()` — loads app context, calls three export functions sequentially:

            ```python
            export_sheets_to_r2()
            export_airtable_all_bases_to_r2()
            export_excel_to_r2()
            ```

        - Returns dict `{"sheets": bool, "airtable": bool, "excel": bool}`.
    2. **QStash Webhook Integration:**
        - Add `POST /admin/backup-redundant-to-r2` route in `app/settings/routes.py` (admin-only).
        - Schedule daily via QStash from app startup in `app/__init__.py`.
    3. **GitHub Actions Fallback (secondary):** Optional schedule at 1 AM UTC.

---

### D. Restore Chain Extension

- **Target Files to Edit:** `app/utils/sync.py` (extend), `app/__init__.py` (extend startup recovery hook)
- **Details:**
    - `_list_r2_csv_backups(prefix)` — find latest CSV in R2.
    - `_download_r2_csv(key)` — download from R2.
    - `_csv_to_records(csv_content)` — parse CSV.
    - `_parse_csv_value(value, column_name, model)` — type conversion.
    - `restore_from_airtable_csv()` — download combined CSV from R2 → SQLite.
    - `restore_from_excel_csv()` — download CSV from R2 → SQLite.
    - Extend `app/__init__.py` startup recovery: after R2 JSON restore attempt, try Sheets CSV → Airtable CSV → Excel CSV → live API.
- **Test:** `tests/test_restore_redundant.py` (15 tests).

---

### E. Route Handler Integration

- **Pattern for each `create_*`/`update_*` handler:**

    ```python
    # After existing sync_to_sheets() call:
    sync_to_airtable("module", row_dict, record.id)  # NEW
    sync_to_excel("WorksheetName", row_dict)  # NEW
    ```

- **Files to extend:** `app/case_file_generator/routes.py`, `app/adjudication/routes.py`, `app/inspection/routes/inspection_routes.py`, `app/sample/routes.py`, `app/bill_generator/routes.py`

---

### F. Testing Strategy

**New test files:**

| File                                   | Tests                                                                      | Coverage                                                                                                    |
| -------------------------------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `tests/test_airtable_sync.py`          | 20                                                                         | `sync_to_airtable()`, base rotation, `AirtableBaseMap` tracking, CSV export, limit detection                |
| `tests/test_excel_sync.py`             | 20 (planned — not yet created)                                             | `sync_to_excel()`, `get_excel_token()`, CSV export, type serialization (dormant until M365 creds available) |
| `tests/test_restore_redundant.py`      | 15                                                                         | Restore functions, priority chain, CSV parsing, `_is_empty_sqlite_db`                                       |
| `tests/test_airtable_base_rotation.py` | 10                                                                         | Multi-base creation, record routing, base capacity checks                                                   |
| `tests/test_sheets_backup.py`          | Already exists (14 tests) — extend to verify integration with new services |

**Test approach:** All cloud API calls (Airtable, MS Graph, R2) are mocked using `unittest.mock.patch`. No real credentials required for CI.

---

### G. Rollout Strategy

1. **Phase 1 (Week 1):** Core sync services + route integration behind feature flags (`ENABLE_AIRTABLE_SYNC=true`, `ENABLE_EXCEL_SYNC=false` — Excel dormant pending Microsoft 365 credentials).
2. **Phase 2 (Week 2):** Backup script + QStash scheduling + restore chain. Test restore in staging.
3. **Phase 3 (Week 3):** Enable by default + monitoring dashboard for sync success rates + alerts for base rotation events. **Excel Online** remains dormant until Azure AD app registration + `Files.ReadWrite.All` admin consent is configured.

---

## Priority 6 — Infrastructure & Future Levels

- [x] **PostgreSQL Migration:** Execute production database schema migration.

    **Alembic Migration Snippet** (new revision `add_rbac_and_comment_tables`):

    ```python
    """add_rbac_and_comment_tables

    Revision ID: a1b2c3d4e5f6
    Revises: previous_rev_id
    Create Date: 2026-08-05 12:00:00

    """
    from alembic import op
    import sqlalchemy as sa

    def upgrade():
        # roles table
        op.create_table(
            "role",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("description", sa.String(256)),
        )
        # association table user_roles
        op.create_table(
            "user_roles",
            sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), primary_key=True),
            sa.Column("role_id", sa.Integer, sa.ForeignKey("role.id"), primary_key=True),
        )
        # comments table
        op.create_table(
            "comment",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("case_id", sa.Integer, nullable=False, index=True),
            sa.Column("case_type", sa.String(32), server_default="case_file"),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column("section_id", sa.String(128)),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )

    def downgrade():
        op.drop_table("comment")
        op.drop_table("user_roles")
        op.drop_table("role")
    ```

    **CI Workflow Modification** (add PostgreSQL service to GitHub Actions):

    ```yaml
    # .github/workflows/ci.yml
    services:
        postgres:
            image: postgres:15
            env:
                POSTGRES_USER: test_user
                POSTGRES_PASSWORD: test_pass
                POSTGRES_DB: test_db
            ports: ["5432:5432"]
            options: >-
                --health-cmd "pg_isready -U $POSTGRES_USER"
                --health-interval 10s
                --health-timeout 5s
                --health-retries 5
    env:
        DATABASE_URL: postgresql://test_user:test_pass@localhost:5432/test_db
    ```

- [x] **Celery Worker Setup:** Deploy persistent Celery worker process.
- [x] **Dockerization:** Maintain multi-stage `Dockerfile` and `docker-compose.yml` (Flask + Celery + Redis + Postgres).
- [x] **OpenAPI Spec:** Generate Swagger documentation via `flasgger` / `apispec`.
- [x] **Structured Logging:** Replace standard logger with `structlog` for JSON-formatted logs.

---

_End of Task List & Detailed Implementation Plan_
