# Deepening Opportunities — NSA Webservice

> **Status:** Independent analysis of module depth across the codebase.
> Identifies shallow modules (interface ≈ implementation complexity) and
> proposes deepening refactors — turning them into deep modules (small
> interface, substantial hidden behaviour).
>
> **Generated:** 2026-08-12
> **Glossary (Ousterhout):** module = a named collection of related operations;
> interface = the set of operations a module exposes; seam = a point where
> the implementation can be substituted; leverage = how broadly a concept
> is used. A **shallow module** has an interface that is nearly as complex
> as its implementation; a **deep module** hides substantial complexity behind
> a small interface.
> **Domain context:** Flask 2.x + SQLAlchemy 2.x + PostgreSQL/SQLite +
> WeasyPrint + Celery/Redis + QStash (see `pyproject.toml` for deps).

---

## 1. Method

1. **Churn ranking:** `git log --since=90.days --name-only` → top files by
   commit frequency. Most-churned: `app/__init__.py` (37), `app/models.py`
   (26), `app/adjudication/routes.py` (21), `app/case_file_generator/routes.py`
   (20), `pyproject.toml` (17), `app/inspection/routes.py` (17).
2. **Code reading:** Walked every module in `app/`, `legal_paragraph_detection_engine/`,
   `kg/`, `evaluation/`, plus all 1,757 tests to find where understanding one
   concept required bouncing between many files.
3. **Deletion test:** For each candidate, asked "what complexity reappears
   across N callers if this module is deleted?" — the forcing-function check.
4. **Depth scoring:** 1 (shallow) → 5 (deep), Ousterhout scale, adjusted
   for leverage (see §2).

---

## 2. Module Depth Scorecard

| #   | Module                    | Files                                                                                                   | LOC    | Depth | Target | Done | Forcing Function                                           |
| --- | ------------------------- | ------------------------------------------------------------------------------------------------------- | ------ | ----- | ------ | ---------------------------------------------------------- |
| 1   | **Triple-Sync Services**  | `sheets_sync.py`, `airtable_sync.py`, `excel_sync.py`, `backup_coordinator.py`, `food_cell/services.py` | ~1,400 | **1** | 4 | [x] | Sync-to-3-targets pattern in 7 call sites                  |
| 2   | **DocumentCaseManager**   | `document_case_manager.py`                                                                              | 736     | **2** | 3 | [x] | 5 callback params push complexity to callers               |
| 3   | **QdrantIndexer**         | `qdrant_indexer.py`                                                                                     | 524    | **2** | 4      | Hooks + ingest + retry + collection mgmt merged            |
| 4   | **ValidationEngine**      | `validation/engine.py`                                                                                  | 262    | **2** | 4      | `_build_case_data` hides 50 lines of ORM+serialization     |
| 5   | **FoodCell Services**     | `food_cell/services.py`                                                                                 | 229     | **2** | 4 | [x] | 7 private helpers + triple-sync duplication                |
| 6   | **PDFAssemblyEngine**     | `pdf_assembly/engine.py`                                                                                | 785    | **3** | 4 | [x] | HTML f-strings extracted to Jinja2 templates               |
| 7   | **LegalParagraphEngine**  | `legal_paragraph_detection_engine/src/`                                                                 | ~5,050 | 3     | 4      | Multi-pass pipeline; each stage visible in interface       |
| 8   | **RAG IngestionPipeline** | `rag/ingestion.py`                                                                                      | 526    | 3     | 4      | 10 injected dependencies; caller must know all             |

**Depth scale:** 1 = shallow (interface ≈ implementation), 5 = deep (small
interface, large hidden behaviour). Prioritized by **shallowest first** —
depth ≤ 2 gets the strongest recommendation.

---

## 3. Deepening Candidates

### Candidate 1: Triple-Sync Services — ⭐ Highest Priority

**Files:**

- `app/services/sheets_sync.py` (345 LOC)
- `app/services/airtable_sync.py` (422 LOC)
- `app/services/excel_sync.py` (329 LOC)
- `app/services/backup_coordinator.py` (75 LOC)
- `app/food_cell/services.py` (278 LOC, `_sync_intimation()` only)
- `app/utils/sync.py` (415+ LOC, 12 restore functions)
- **Call sites:** `app/sample/routes.py`, `app/adjudication/routes.py`,
  `app/bill_generator/routes.py`, `app/case_file_generator/routes.py`,
  `app/inspection/routes/inspection_routes.py`,
  `app/shared/document_case_manager.py`,
  `app/food_cell/services.py`

**Problem:**
The "sync one row to Google Sheets + Airtable + Excel" pattern is
**copy-pasted across 7 call sites** with near-identical try/except blocks:

```python
# Pattern repeated 7× (sample/routes.py:212-232):
success = sync_to_sheets("sample_repo", row_dict)
if success:
    sample.synced_at = datetime.now(UTC)
    db.session.commit()
try:
    from app.services.airtable_sync import sync_to_airtable
    sync_to_airtable("sample_repo", row_dict, sample.id)
except Exception as e:
    current_app.logger.warning(f"Sample: Airtable sync failed: {e}")
try:
    from app.services.excel_sync import sync_to_excel
    sync_to_excel("sample_repo", row_dict)
except Exception as e:
    current_app.logger.warning(f"Sample: Excel sync failed: {e}")
```

The three sync services each maintain their **own copy** of:

- `WORKSHEET_MAP` / `AIRTABLE_TABLE_MAP` / Excel's equivalents (3× duplication)
- `SHEET_COLUMNS` / `AIRTABLE_FIELD_MAP` / Excel's equivalents (3× duplication)
- Thread-local client caching (3× pattern)
- `_escape_formula` formula-injection prevention (3×)
- Export-to-R2 functions (3×)

`backup_coordinator.py` wraps the same 3-target pattern in a separate
try/except per target, unaware that the sync services share interfaces.

`food_cell/services.py::_sync_intimation()` reinvents the same triple-sync
loop inline (7 more lines of try/except).

**Deletion test:** Delete `backup_coordinator.py` → the try/except-per-target
pattern reappears in 3 export paths. Delete the triple-sync from `sample/routes.py`
→ it reappears in 6 other route files. **The module is earning its keep.**

**Solution:** Extract a `SyncTarget` protocol and `SyncOrchestrator`:

```python
# app/services/sync_orchestrator.py (NEW)
class SyncResult(TypedDict):
    sheets: bool
    airtable: bool
    excel: bool

class SyncTarget(Protocol):
    """Protocol satisfied by sheets_sync, airtable_sync, excel_sync."""
    module_key: str
    worksheet_map: dict[str, str]
    column_map: dict[str, list[str]]
    def sync_row(self, module_key: str, row: dict, entity_id: int | None = None) -> bool: ...
    def export_to_r2(self) -> str | None: ...

class SyncOrchestrator:
    """Single entry point: sync one row to all enabled targets.

    Replaces 7× duplicated try/except blocks with a single call:
        result = orchestrator.sync_row("sample_repo", row_dict, sample.id)
    """
    def sync_row(self, module_key: str, row: dict, entity_id: int | None = None) -> SyncResult: ...
    def backup_all(self) -> dict[str, bool]: ...
```

The three sync services become thin adapters implementing `SyncTarget`.
`food_cell/services.py::_sync_intimation()` → 3 lines calling
`self._orchestrator.sync_row(...)`. `backup_coordinator.py` → delegates to
`orchestrator.backup_all()`.

**Benefits:**

- **Locality:** The triple-sync logic lives in one place. Changing how errors
  are logged or how failures are tolerated requires editing 1 file, not 7.
- **Leverage:** 7 call sites simplify to 1-line calls. New targets (e.g.,
  a Supabase bridge in Phase 17) plug in as one more adapter.
- **Tests:** 7 sets of try/except are replaced by `test_sync_orchestrator.py`
  with mock adapters. The existing per-target sync tests continue to test
  the adapters directly.

**Estimated effort:** 3–4 days (including migrating all 7 call sites)
**Tests affected:** 43 backup/redundancy tests + 15 food_cell tests + all sync
call-site tests

**Dependency category:** In-process (pure computation, no I/O at the orchestrator
level). Testable with mock adapters. No new seam needed — the orchestrator
is tested with in-memory fakes.

---

### Candidate 2: QdrantIndexer — Mixed Concerns

**Files:** `app/rag/qdrant_indexer.py` (524 LOC)

**Problem:**
`QdrantIndexer` is a **four-in-one** module doing:

1. **Chunk → embed → upsert** (the core ingestion facade)
2. **Single-retry upsert** (`_upsert_with_retry`)
3. **Sparse embedding** (`_embed_sparse` — collection-capability check + lazy fastembed)
4. **After-flush hook dispatch** (`_on_after_flush`, `register_qdrant_hooks`,
   `register_chunk_model`, `register_document_model`, `_chunk_payload`,
   `_get_indexer`, `set_default_indexer` — 20+ lines of hook management)

The `sync_payloads()` method (45 lines) does: validate vector size → rebuild
chunks → embed dense → embed sparse → construct points → upsert with retry —
all in one method. The after_flush hook logic is **not a separate concern**
— it's tangled into the same class, and the hook registration functions
are module-level globals (`_REGISTERED_MODELS`, `_registered_hooks`,
`_default_indexer`).

**Deletion test:** Delete the hook functions → the
`after_flush` listener registration and dispatch logic reappears in a new
module. Delete `_upsert_with_retry` → retry logic reappears at each upsert
call site (currently just one, but grows as the indexer gains more paths).

**Solution:** Split into two modules:

- `app/rag/qdrant_indexer.py` — `QdrantIndexer` (chunk → embed → upsert, 4
  methods: `index_document`, `sync_chunks`, `sync_payloads`, `remove_*`).
  The retry logic and sparse embedding become private internals.
- `app/rag/index_sync_hook.py` — `QdrantHookManager` encapsulating
  `_REGISTERED_MODELS`, `_on_after_flush`, `register_qdrant_hooks()`,
  `register_chunk_model()`, `register_document_model()`.

**Benefits:**

- **Locality:** Ingest logic changes (e.g., new batch strategy) don't touch
  the hook registration. Hook registration changes don't risk the embed-upsert
  path.
- **Leverage:** The `QdrantHookManager` can be tested with a fake indexer.
  The `QdrantIndexer` can be tested without SQLAlchemy session hooks.
- **Test surface shrinks:** Current `test_qdrant_indexer.py` (16 tests) tests
  both ingest and hook logic. After the split, hook tests go to
  `test_index_sync_hook.py`.

**Estimated effort:** 2–3 days
**Tests affected:** 16 indexer tests + 6 hook-related tests

**Dependency category:** In-process. Hook management is pure logic
(registry pattern). Ingest can be tested with injected fakes (already has
mock-injection pattern).

---

### Candidate 3: ValidationEngine — Data Assembly Hidden Behind a Thin Interface

**Files:** `app/validation/engine.py` (262 LOC)

**Problem:**
`ValidationEngine.validate_case()` is a thin 10-line orchestrator, but
`_build_case_data()` (55 lines) — the method that assembles the
`case_data` dict consumed by all 7 rules — hides enormous complexity:

- Branch on `case_type == "case_file"` vs `"adjudication"` (imports different
  `case_file_to_dict` / `adjudication_to_dict` from route modules)
- 4 ORM queries (`Annexure.query`, `Evidence.query`, `Sample.get`, `suggest_sections`)
- 2 template renders (`render_case_file_document`, `render_adjudication_document`)
- 2 serialization helpers (`_serialize_annexure`, `_serialize_evidence`) that
  each enumerate 10+ fields

The interface (`validate_case(case_id, case_type)`) hides ~120 lines of
ORM + serialization + rendering. This is **actually deep** in Ousterhout's
sense — but the problem is that `_build_case_data` is **private** and
untestable in isolation. When a validation rule needs a new field, the test
writer can't construct just the `case_data` dict — they need a real CaseFile
in the DB.

**Deletion test:** Delete `ValidationEngine` → the rule evaluation loop,
scoring, and aggregation logic reappear across test code and rule consumers.

**Solution:** Extract `CaseDataAssembler` as a public class with a clear
interface, so tests can inject a pre-built `case_data` dict directly:

```python
class CaseDataAssembler:
    """Assemble the plain-dict payload consumed by validation rules.

    Separated so tests can construct case_data directly without a DB,
    matching the 'plain dict' pattern from rules.py docstring.
    """
    def assemble(self, resolved: ResolvedCase) -> dict[str, Any]: ...
    def serialize_annexure(self, a) -> dict: ...
    def serialize_evidence(self, e) -> dict: ...
    def render_documents(self, resolved) -> tuple[str, str]: ...
```

`ValidationEngine.__init__` takes an optional `assembler: CaseDataAssembler`
(default builds the real one). Rules tests can now do:

```python
engine = ValidationEngine(assembler=FakeAssembler({"fields": {...}}))
result = engine.validate_case(case_id=1, case_type="case_file")
```

**Benefits:**

- **Locality:** Data-gathering logic (ORM, templates, serialization) is in one
  place, separable from rule evaluation logic.
- **Testability:** The 46 validation tests can inject `case_data` dicts
  without touching the ORM or templates.
- **Leverage:** `CaseDataAssembler` becomes reusable — the RAG verification
  pipeline (`evidence_verifier.py`) also needs annexure/evidence
  serialization, and the timeline engine needs date extraction from the same
  records.

**Estimated effort:** 2 days
**Tests affected:** 46 validation tests (can be refactored to use the
assembled dict directly; existing DB-backed tests still work through default)

**Dependency category:** Local-substitutable — uses SQLite in tests (already
has `test_postgres` CI job), `CaseDataAssembler` is tested in isolation with
in-memory dicts.

---

### Candidate 4: FoodCell Services — Orchestration of Private Helpers

**Files:** `app/food_cell/services.py` (278 LOC)

**Problem:**
`generate_and_forward_do_intimation()` (35 lines) is an orchestration of
7 private helpers:

```
_resolver_sample → _next_do_reference_no → _render_html → _render_pdf
→ _store_intimation → _sync_intimation → (_build_sync_row)
```

All 7 are module-level functions (not methods), so they're visible in `from`
imports but not part of any interface. The function does too much: reference
generation, template rendering, PDF generation, file I/O, DB persistence, AND
triple-sync — 5 distinct domains in one call path.

Additionally, `_next_do_reference_no()` uses the `CodeSequence` pattern
which is also used by `generate_sample_code` in `app/sample/sample_utils.py`
— the sequence-generation logic is duplicated.

**Deletion test:** Delete `generate_and_forward_do_intimation` → the
DO-intimation generation logic (render → PDF → store → sync) reappears in
the food_cell routes and Celery tasks.

**Solution:** Extract two concerns:

1. `DODocumentRenderer` — `_render_html`, `_render_pdf`, `_store_intimation`
    - `CodeSequence`-based reference generation (reused by sample code gen).
      Interface: `render(sample) -> (html, pdf_path)`, `generate_reference() -> str`.
2. Reuse `SyncOrchestrator` from Candidate 1 — replaces the inline
   `_sync_intimation` triple-sync.

**Benefits:**

- **Locality:** PDF rendering, storage, and sync are separable concerns.
- **Leverage:** `DODocumentRenderer` is reusable for the `regenerate`
  endpoint. `CodeSequence` generation is centralized for both DO refs and
  sample codes.
- **Testability:** The 15 food_cell tests can mock the renderer and
  orchestrator separately.

**Estimated effort:** 2–3 days
**Tests affected:** 15 food_cell tests

**Dependency category:** Local-substitutable (file I/O can use `tmp_path`
fixture, sync uses the `SyncOrchestrator` with mock adapters).

---

### Candidate 5: DocumentCaseManager — Callback Explosion

**Files:** `app/shared/document_case_manager.py` (719 LOC)

**Problem:**
D5 from AGENTS.md was "completed" but the interface still leaks complexity.
The constructor takes **5 callback parameters**:

```python
DocumentCaseManager(
    model_to_dict_fn: ModelToDictFn,
    process_form_fn: ProcessFormFn,
    prepare_context_fn: PrepareContextFn,
    validate_form_fn: ValidateFormFn,
    render_context_fn: RenderContextFn,  # may not exist
)
```

Callers must understand and provide all 5 — the interface is nearly as complex
as the implementation. The class exposes 10+ public methods
(`register_routes`, `get_case`, `list_cases`, `render_editor`,
`xref_report`, `toc_report`, `renumber_annexures`, `regenerate`,
`generate_case`) — some callers only need 2 of them (e.g., food_cell only
calls `get_case` + `generate`).

**Deletion test:** Delete `DocumentCaseManager` → the near-duplicate route
logic between `case_file_generator/routes.py` (697 lines) and
`adjudication/routes.py` (820 lines) reappears. **Earning its keep.**

**Solution:** Not a full rewrite — split the **route registration** (which
needs all 5 callbacks) from the **query/render operations** (which need 1–2).
Extract `CaseQueryService` (get_case, list_cases, get_case_by_number)
as a separate module with a 3-method interface, and keep
`DocumentCaseManager` focused on the CRUD + generation flow.

**Benefits:**

- **Locality:** Simple lookups don't need the 5-callback constructor.
- **Leverage:** `CaseQueryService` is usable from food_cell, timeline,
  validation, and sync paths — all of which just need "what case is this ID?"
- **Testability:** Query tests don't need form-processing callbacks.

**Estimated effort:** 2 days
**Tests affected:** Minimal — new `test_case_query_service.py`; existing
document_case_manager tests continue through the manager.

**Dependency category:** In-process. `CaseQueryService` wraps SQLAlchemy
queries (tested with SQLite, already available in CI).

---

### Candidate 6: PDFAssemblyEngine — Large Implementation, Adequate Interface

**Files:** `app/pdf_assembly/engine.py` (1,041 LOC)

**Assessment:**
D3 from AGENTS.md was "completed" but the module remains large (1,041 lines).
However, the **interface is small and stable**: `generate_from_html()`,
`post_process()`, `embed_photos()`, `assemble()`, `assemble_complete_case_pdf()`.
The private helpers (`_generate_main_document_pdfs`, `_generate_index_page_pdf`,
`_generate_annexure_pdfs`, etc.) are genuinely internal — they don't leak to
callers.

**Module Depth:** 3 (balanced). The interface hides the HTML post-processing
complexity, but `assemble_complete_case_pdf` is 35 lines that delegates to 4
private methods — it's doing orchestration, not hiding it.

**Verdict:** **Not a priority** for deepening. The module is already
adequately deep for its primary use case (HTML → PDF). The size is from
the HTML template generation (`_create_index_page_html`, `_create_annexure_page_html`,
`_create_evidence_photo_page` — 300+ lines of f-string HTML), which is
genuinely implementation detail.

If anything, the HTML template strings could be **extracted to Jinja2
templates** (the project already uses Jinja2), reducing the Python module
from 1,041 → ~700 LOC. This is a **deletion** opportunity, not a deepening
one.

---

## 4. Prioritized Backlog
| Priority | Candidate                       | Est. Effort | Est. Gain                                    | Risk   | Status |
| -------- | ------------------------------- | ----------- | -------------------------------------------- | ------ | ------ |
| P0       | Triple-Sync Services            | 3–4 days    | 7 call sites → 1-line calls, −145 LOC       | Low    | DONE   |
| P0b      | PDFAssemblyEngine template ext. | 1 day       | 1041→785 LOC, HTML in Jinja2 templates      | Low    | DONE   |
| P3       | FoodCell services               | 2–3 days    | Reusable renderer, 278→229 LOC              | Low    | DONE   |
| P4       | DocumentCaseManager query split | 2 days      | CaseQueryService, standalone lookups         | Low    | DONE   |
| P1       | QdrantIndexer split             | 2–3 days    | Test isolation, cleaner hooks               | Low    | PENDING|
| P2       | ValidationEngine assembler      | 2 days      | 46 tests become DB-free                      | Low    | PENDING|

---

## 5. Import-Boundary Check

```bash
# No import-boundaries.json exists in this repo; the project uses Python
# imports (not shell sourcing). The boundary concern is:
# - app/services/ → must not depend on app/rag/ (separation of sync vs. RAG)
# - app/shared/  → can be imported by any module (canonical contract)
# - app/rag/     → can depend on app/shared/, app/services/
# - app/food_cell/ → can depend on app/services/, app/utils/
```

The proposed `SyncOrchestrator` in `app/services/` respects this boundary —
it depends on the three sync adapters (same package) and exposes a protocol
that callers in any package can use. No cross-package violations.

---

## 6. Relationship to Rust Refactoring Plan

Two of the five deepening candidates (Triple-Sync Services, QdrantIndexer)
are also targets in `docs/RUST_REFACTORING_EVALUATION.md` — the sync
orchestrator is pure Python (in-process, no native extensions needed), while
the QdrantIndexer's ingest path benefits from Rust embedding + batch upsert.
The two plans are **complementary**: deepening reduces interface complexity
and coupling; Rust reduces per-operation CPU cost. Do deepening first (it
reshapes the interfaces the Rust modules will need to satisfy), then port
the internals to Rust.
