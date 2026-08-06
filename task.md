# Task List & Detailed Implementation Plan — NSA Webservice

> **Status:** ✅ Deepening Tasks D1–D5, S6a–d, S7, S2, S10a–c, Priority 6 infra, S9a (concurrency guard — fully fixed), Phase 16 (backup/export/import), Phase A (OCR pipeline foundation), Phase 13 (timeline engine + Gantt UI + global case-picker + entry points), **Phase 21 (Food Cell DO Intimation)**, and **7/7** Performance Quick Wins all implemented & verified. Phases 11–12, 14–15, 17–20 pending.

> **Purpose:** Consolidated, actionable, highly detailed implementation plan and TODO list for AI agents and developers. Organized by priority with checkboxes, explicit file targets, data schemas, function signatures, routes, acceptance criteria, and testing strategies.
> **Sources & Alignment:** `SECURITY_TODO.md`, `ALL_TODO_MERGED.md`, `ROADMAP_ALIGNMENT_REPORT.md`, `ENGINEERING_ASSESSMENT.md`, `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md`, `REFACTORING_PLAN.md`.

---

## Completed Milestones

> Items finished and verified are tracked here so agents can trust implementation status at a glance.

- [x] **Phase 4 — Annexure Replace** (`POST /annexure/<id>/replace` + UI button + 8 tests). Replaces the stored file on an existing annexure in place: re-extracts hash/page-count/OCR/size/MIME, keeps the annexure id + letter so document references stay valid, rejects content-hash duplicates of _other_ annexures (self re-upload allowed), deletes the old file after commit, and audit-logs `ANNEXURE_REPLACED`. Files: `app/annexure/routes.py`, `app/annexure/templates/annexure/index.html`, `tests/test_annexure.py`.
- [x] **PDF Engine Repairs**. Consolidated WeasyPrint import guards (`import_weasyprint()`), header/footer template injection (`_get_default_header_template`, `_get_default_footer_template`, `_apply_headers_footers`), fixed PDF bookmarking and footer insertion (`rfind` instead of `endswith`), detached direct WeasyPrint imports from `tasks.py`. Files: `app/pdf_assembly/__init__.py`, `app/utils/pdf_utils.py`, `app/case_file_generator/tasks.py`.
- [x] **Technical Debt Cleanup**. Removed singleton pattern from `app/services/legal_engine.py`, split `app/models.py` into `app/models/` package (`auth.py`, `document.py`, `inspection.py`, `issue.py`, `billing.py`, `config.py`), modularized `app/inspection/routes.py` (1077 lines) into 4 submodules, updated `datetime.utcnow()` to `datetime.now(timezone.utc)` across 11 occurrences, migrated `Model.query.get()` to `db.session.get()`, updated `db.get_engine()` to `db.engines['default']` in migrations, untracked ~70MB of CSV data files from repository index.
- [x] **Phase 10 — Fuzzy Search Integration & Rapidfuzz Dependency** (`fuzzy_search_fallback()` + `fuzzy` API param + UI toggle + 56 tests). Added `rapidfuzz>=3.0.0` and `numpy>=1.26.0` as declared dependencies in `pyproject.toml`. Implemented `fuzzy_search_fallback()` in `app/search/indexer.py` using `fuzz.token_set_ratio` + `fuzz.partial_ratio` scoring with threshold filtering (default 65.0) and `<mark>`-wrapped snippet highlighting. Updated `search()` to auto-fall-back to fuzzy when FTS5/LIKE yields zero results or when `fuzzy=True`. Updated `app/search/routes.py` to read `fuzzy` query param and return effective `fuzzy` flag + match `score` keys in JSON. Added styled toggle switch (`#fuzzyToggle`) in `app/search/templates/search/index.html`. Verified `app/document_cleaner/normalizers.py` imports `rapidfuzz` cleanly. `pytest tests/test_search.py` passes 56/56 (TestFuzzySearch: 19, TestSearchAPI: 9, TestSearchPage: 2, plus existing FTS5/indexing tests). Lint clean (`ruff check`). Files: `pyproject.toml`, `app/search/indexer.py`, `app/search/routes.py`, `app/search/templates/search/index.html`, `tests/test_search.py`.
- [x] **S7: Scraper TLS Security Fix**. Verified and enforced TLS certificate checking for KMC trade license lookup in `app/utils/lookup.py`. Removed `check_hostname = False` and `verify_mode = ssl.CERT_NONE`. Maintained cipher string `DEFAULT@SECLEVEL=1`. Files: `app/utils/lookup.py`.
- [x] **Priority 6 — Infrastructure & Future Levels** (PostgreSQL Migration + CI + Docker + OpenAPI + Structured Logging). Four Alembic migrations authored & verified clean against head (`add_ocr_pipeline_models`, `add_timeline_event_table`, `add_role_user_role_comment_tables` [Rev `a1b2c3d4e5f6`], `add_entity_relationship_tables` → head); models model==migration-parity confirmed via autogen (zero drift for new objects). SQLAlchemy connection pooling in `create_app`; CI `test-postgres` job in `validation.yml`. `.gitignore` fix (`models/`→`/models/`) so `app/models/` ships; `migrations/env.py` `include_object` hook suppressing destructive FTS5 virtual-table auto-drops. Multi-stage `Dockerfile` + `docker-compose.yml` + `.dockerignore` (`docker compose config` validates). `flasgger` Swagger UI at `/apidocs/`; `structlog` structured logging via `app/utils/logging.py::setup_logging` (JSON prod / console dev + stdlib bridge); `GET /health` endpoint. Verified: app boots, `ruff` clean on changed files, 86 targeted tests pass. (Celery worker was already deployed via `render.yaml` — no change needed.)
- [x] **S9a Concurrency Guard — full fix (2026-08-06).** The one-line inspection-PUT bug (`409` passed *inside* `jsonify()` → HTTP 200) is fixed: `app/inspection/routes/inspection_routes.py` now returns `jsonify({...}), 409`. `tests/test_concurrency_inspection.py` — 4/4 pass.
- [x] **Eager Loading Optimization — Perf Quick Win #5 (2026-08-06).** `load_only` column trimming on `DocumentCaseManager._list_cases_query()` (wide-table `/cases` JSON endpoints), `lazy="selectin"` on `Bill.samples` + `bills` backref, `distinct()` on the evidence tag-cloud query. All 7 Performance Quick Wins now complete.
- [x] **Phase A — OCR Pipeline Foundation (2026-08-06).** `app/services/ocr_extraction.py`, `app/services/page_splitter.py`, `app/ocr_pipeline/tasks.py` implemented and polished (clean `db.session` import replacing a `__import__` hack; single `to_flat_dict()` call). `tests/test_ocr_extraction.py` — 14/14 pass.
- [x] **Phase 13 — Timeline Engine & Gantt UI (2026-08-06).** New `app/timeline/` blueprint: `TimelineEngine` extracts milestones from CaseFile/Adjudication/Inspection/Sample/Annexure/Evidence dates, persists case_file events to `timeline_event` (idempotent), validates chronological sequences, and serves a vertical-timeline + Gantt page with document links. **Access:** global nav case-picker (keyboard-navigable search dropdown with `<mark>` highlighting + server-injected URL bases) in `base.html`, "Case Timelines" panels on both index pages, document-editor button, Timeline buttons in search results / evidence / annexure / inspection (list + detail, when adjudicated) / audit log (CaseFile/Adjudication rows) / version-control history / sample list (batched sample→case map) + `case_id`/`timeline_url` on the sample detail JSON. Also wired the orphaned `app/audit` routes (audit log viewer was unreachable) and fixed stale `edit_case_file`/`edit_adjudication` url_for names → `edit_case`. `tests/test_timeline.py` — **21/21 pass**; route-collision + app-boot regression green.
- [x] **Phase 21 — Food Cell DO Intimation (2026-08-06).** New `app/food_cell/` blueprint (`/food-cell`): DO Intimation PDF download / HTML view / regenerate / status routes; `DoIntimation` model + `food_cell_forwarded` on `Sample` (`add_food_cell_do_intimation` migration); HTML→PDF via WeasyPrint with stub fallback; Celery `send_do_intimation` task wired post-save in `app/sample/routes.py::create_sample()`; best-effort sync to Sheets + Airtable + Excel. `tests/test_food_cell_do_intimation.py` — **15/15 pass**.

### 📌 Suggested Next 3 Steps (2026-08-06)

> Highest future impact, smallest effort — in this order:

1. **Phase 12 — Legal Validation Engine** (`app/validation/`: `engine.py`, `rules.py`, `routes.py`). Self-contained, rule-based, zero external deps; plugs into the existing `app/legal_analysis` workbench UI. Reuses `app/utils/suggester.py` + `fss_sections.md`. Deliverable: `ValidationResult`/`BaseRule`/`ValidationEngine` + `POST /validation/validate` + `tests/test_validation.py`.
2. **Phase 15 — Analytics Dashboard** (`app/analytics/`). Aggregate SQL over `CaseFile`/`Adjudication`/`Inspection`/`Sample`/`FboIssue` + Chart.js/Leaflet dashboard. Natural consumer of the new `selectin`/`load_only` query patterns. Deliverable: `GET /analytics/api/metrics` + `tests/test_analytics.py`.
3. **Phase 18 — Multi-User RBAC & Comments** (`@role_required` decorator + comment API/UI + user-role admin). `Role`/`user_roles`/`Comment` models + migration already done; only the decorator, comment endpoints, and admin UI remain. Deliverable: `app/decorators.py` + comment routes + `tests/test_rbac.py`.

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

### Phase 12: Legal Validation Engine

- **Goal & Rationale:** Build an automated rule-based validation engine that analyzes case documents and adjudications for legal completeness, mandatory section presence, statutory reference accuracy (FSSA 2006), signature placeholders, date sequence consistency, and evidence duplication.
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
- **Acceptance Criteria & Test Plan:**
    - `POST /validation/validate` returns HTTP 200 with structured JSON `{score, errors, warnings, suggestions}`.
    - `tests/test_validation.py` tests all individual rules with valid and invalid case payloads.

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
- **Acceptance Criteria & Test Plan:** ✅ MET — case dates auto-populate `timeline_event` on API access; `/timeline/case/<id>` renders the interactive timeline (vertical nodes + Gantt bars); `tests/test_timeline.py` — **14/14 pass** (engine extraction, linked Sample/Annexure events, adjudication ephemeral, sequence-warning detection, idempotent persistence, API/view/refresh routes, 404s, None-date guards, annexure `document_url`).

---

## Priority 2 — Core Features (Phases 13–20)

### Phase 15: Analytics Dashboard

- **Goal & Rationale:** Provide operational analytics and executive reporting on pending/disposed cases, inspection compliance rates, sample testing pipeline status, legal section frequency, and geographic violation clusters.
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

### Phase 18: Multi-User RBAC & Document Comments

- **Goal & Rationale:** Implement role-based access control (RBAC) to enforce administrative permission boundaries, alongside a document commenting system for multi-user collaboration and approval workflows.
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

- **Goal & Rationale:** Embed an AI-powered assistant into the document editor to provide automated summarization, legal terminology refinement, contradiction detection, missing annexure identification, and prayer drafting.
- **Target Files to Edit/Create:**
    - `pyproject.toml` (add `openai` or `httpx` dependencies)
    - `app/ai_assistant/__init__.py` (blueprint package)
    - `app/ai_assistant/service.py` (LLM provider service)
    - `app/ai_assistant/routes.py` (AI API endpoints)
    - `app/ai_assistant/templates/ai_assistant/sidebar.html` (editor UI panel)
    - `app/__init__.py` (register blueprint)
    - `tests/test_ai_assistant.py` (test suite with mocks)
- **Detailed Implementation Plan:**
    1. **Provider Abstraction Service (`app/ai_assistant/service.py`):**
        - Class `AIAssistantService`:
            - Supports OpenRouter or OpenAI API based on `AI_ASSISTANT_PROVIDER` ('openrouter'|'openai') and `AI_ASSISTANT_API_KEY`.
            - Implements token usage tracking to satisfy S10c operational monitoring.
            - Helper methods:
                - `summarize_text(text: str) -> str`
                - `refine_legal_language(text: str) -> str`
                - `detect_contradictions(sections: dict) -> list[str]`
                - `suggest_missing_annexures(sections: dict) -> list[str]`
                - `draft_prayers(facts: str, grounds: str) -> str`
    2. **API Routes (`app/ai_assistant/routes.py`):**
        - Register `ai_bp = Blueprint('ai', __name__, url_prefix='/ai')`.
        - Route `POST /ai/assist`: Accepts `{"action": str, "content": str, "context": dict}`. Returns JSON response `{"result": str, "tokens_used": int}`.
    3. **UI Sidebar (`app/ai_assistant/templates/ai_assistant/sidebar.html`):**
        - Dockable floating sidebar in document editor with action buttons ("Summarize", "Improve Legal Phrasing", "Find Contradictions", "Suggest Annexures").
- **Acceptance Criteria & Test Plan:**
    - Editor sidebar allows sending text snippets to AI service and renders returned suggestions.
    - `pytest tests/test_ai_assistant.py` verifies service functions using mocked API responses.

---

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

### Phase 14: Knowledge Graph Engine

- **Goal & Rationale:** Provide entity extraction and relationship mapping across cases, FBOs, inspectors, samples, lab reports, legal provisions, and evidence items, visualized via an interactive graph node graph.
- **Target Files to Edit/Create:**
    - `app/knowledge_graph/__init__.py`
    - `app/knowledge_graph/models.py` (SQL light graph representation or schema)
    - `app/knowledge_graph/engine.py` (entity-relationship extractor)
    - `app/knowledge_graph/routes.py` (API endpoints)
    - `app/knowledge_graph/templates/knowledge_graph/view.html` (Cytoscape.js visualizer)
    - `tests/test_knowledge_graph.py` (test suite)
- **Detailed Implementation Plan:**
    1. **Extraction Engine (`app/knowledge_graph/engine.py`):**
        - Class `KnowledgeGraphEngine`:
            - `build_graph_for_case(case_id: int) -> dict`:
                - Extracts Nodes: `CaseNode`, `FBONode`, `InspectorNode`, `SampleNode`, `LabNode`, `SectionNode`, `EvidenceNode`.
                - Extracts Edges: `INSPECTED_BY`, `SAMPLED_FROM`, `TESTED_AT`, `VIOLATED_SECTION`, `SUPPORTED_BY`.
                - Formats output as Cytoscape.js compatible JSON: `{nodes: [{data: {id, label, type}}], edges: [{data: {source, target, label}}]}`.
    2. **Routes & Visualization (`routes.py`, `templates/knowledge_graph/view.html`):**
        - Route `GET /knowledge_graph/case/<int:case_id>`: Renders Cytoscape.js interactive node-edge graph view.
- **Acceptance Criteria & Test Plan:**
    - Graph API returns correct node/edge structure representing all case entities and relationships.
    - `pytest tests/test_knowledge_graph.py` verifies entity extraction logic.

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

## Priority 7 — Multi-Target Sheets Redundancy (Airtable + MS Excel)

- **Goal & Rationale:** Eliminate Google Sheets as a single point of failure for data backup by adding Airtable and Microsoft Excel Online as parallel real-time sync targets, with R2 CSV exports of each service for redundant restore when any (or all) services are unavailable.
- **Dependencies to Add:** `pyairtable>=1.0.0`, `msal>=1.0.0` (verify `requests` already present)

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

| File | Tests | Coverage |
|---|---|---|
| `tests/test_airtable_sync.py` | 20 | `sync_to_airtable()`, base rotation, `AirtableBaseMap` tracking, CSV export, limit detection |
| `tests/test_excel_sync.py` | 20 | `sync_to_excel()`, `get_excel_token()`, CSV export, type serialization |
| `tests/test_restore_redundant.py` | 15 | Restore functions, priority chain, CSV parsing, `_is_empty_sqlite_db` |
| `tests/test_airtable_base_rotation.py` | 10 | Multi-base creation, record routing, base capacity checks |
| `tests/test_sheets_backup.py` | Already exists (14 tests) — extend to verify integration with new services |

**Test approach:** All cloud API calls (Airtable, MS Graph, R2) are mocked using `unittest.mock.patch`. No real credentials required for CI.

---

### G. Rollout Strategy

1. **Phase 1 (Week 1):** Core sync services + route integration behind feature flags (`ENABLE_AIRTABLE_SYNC`, `ENABLE_EXCEL_SYNC` default: false).
2. **Phase 2 (Week 2):** Backup script + QStash scheduling + restore chain. Test restore in staging.
3. **Phase 3 (Week 3):** Enable by default + monitoring dashboard for sync success rates + alerts for base rotation events.

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
