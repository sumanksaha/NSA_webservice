# Refactoring Plan — Architectural Deepening

> **Generated:** 2026-08-04  
> **Source:** Deepen Architecture analysis of NSA Webservice v0.8.0  
> **Scope:** 5 deepening candidates, ranked by Module Depth score (lowest = highest priority)

---

## Summary

| # | Candidate | Module Depth | Files Affected | Risk |
| --- | ----------- | ------------- | ---------------- | ------ |
| 1 | Case/Adjudication route duplication | 2 | 2 route files | Medium |
| 2 | Cross-module case resolution | 1 | 5+ files | Low |
| 3 | Document viewer inlined concerns | 2 | 1 route file | Low |
| 4 | Inspection routes mechanical split | 1 | 5 files | Medium |
| 5 | PDF utils grab-bag | 3 | 2 files | Medium |

---

## 1. Case / Adjudication Route Duplication → `DocumentCaseManager`

### Problem

`app/case_file_generator/routes.py` (697 lines) and `app/adjudication/routes.py` (820 lines) are near-mirrors. Both implement: index, list_cases, get_case, get_case_by_number, editor, xref_report, toc_report, renumber_annexures, regenerate, lookup_fssai, lookup_ce, generate. They differ only in model (`CaseFile` vs `Adjudication`), template directory, and a few adjudication-specific fields.

### Deepened Module: `app/shared/document_case_manager.py`

**Interface** (small, stable):

```python
class DocumentCaseManager:
    def __init__(self, model, template_dir, bp_name, case_type, sections_fn=None): ...
    def register_routes(self, bp): ...
    def get_case(self, case_id) -> model | None
    def get_case_by_number(self, case_number) -> model | None
    def list_cases(self, filters, page, per_page) -> Pagination
    def render_editor(self, case_id) -> str  # HTML
    def generate_documents(self, case_id, form_data) -> PDFResult
    def regenerate(self, case_id) -> PDFResult
    def xref_report(self, case_id) -> str  # HTML
    def toc_report(self, case_id) -> str  # HTML
    def renumber_annexures(self, case_id, new_letters: dict) -> None
```

**What sits behind the seam:**

- Common route registration (decorators + handlers generated from config)
- Form validation + `process_form_data` dispatch (case_type-specific via injected `sections_fn`)
- Template rendering with model-specific context
- Cross-reference + TOC post-processing delegation (`pdf_utils.post_process_pdf_html`)
- Adjunction lookup + permission-letter generation
- Annexure renumbering + enclosures list

**Adapters:**

- `case_file_generator/routes.py` → thin: creates `DocumentCaseManager(CaseFile, "case_file_generator", "case_file", sections_fn=get_applicable_sections)` + 1 extra route (`lookup_sample`)
- `adjudication/routes.py` → thin: creates `DocumentCaseManager(Adjudication, "adjudication", "adjudication")` + 2 extra routes (`lookup_ce_route`, `suggest_sections_route`)

**Tests:**

- Replace `test_step1.py`–`test_step5_integration.py` (testing both modules separately) with a single parametrized `test_document_case_manager.py` that runs the same assertions against both `case_file` and `adjudication` configs.

### Risk

Medium — two large files collapse to ~150 lines each. Must verify all 20 routes still resolve (use `test_route_collisions.py`).

---

## 2. Cross-Module Case Resolution → `CaseResolver`

### Problem

Three independent implementations of "resolve whether an ID is a CaseFile or Adjudication":

- `document_viewer/routes.py::_resolve_case()` → `(record, case_type, label)` — 8 lines
- `version_control/routes.py::_resolve_target()` → `(case_id, adjudication_id)` — 14 lines, with `kind` param
- Inline lookups in `evidence/routes.py`, `search/indexer.py`, `annexure/routes.py` — scattered `db.session.get()` calls

### Deepened Module: `app/shared/case_resolver.py`

**Interface:**

```python
@dataclass
class ResolvedCase:
    case_id: int | None
    adjudication_id: int | None
    case_type: str       # "case_file" | "adjudication"
    case_number: str
    record: CaseFile | Adjudication | None

class CaseResolver:
    def resolve(self, case_id_or_adjudication_id: int, kind: str | None = None) -> ResolvedCase | None
```

**What sits behind the seam:**

- The disambiguation algorithm (try CaseFile first, then Adjudication; respect `kind` hint)
- Single DB hit per table (not per field)
- Case number extraction from the resolved record

**Adapters:**

- `document_viewer/routes.py` — replace `_resolve_case` with `CaseResolver().resolve(case_id)`
- `version_control/routes.py` — replace `_resolve_target` + `_kind_param` with `CaseResolver().resolve(id, kind)`
- `evidence/routes.py`, `search/indexer.py`, `annexure/routes.py` — replace inline `db.session.get` calls

**Tests:**

- `tests/test_case_resolver.py` — ID collision across tables, missing records, `kind` hints, case_number extraction

### Risk

Low — pure extraction + refactor. No behaviour change, just consolidation.

---

## 3. Document Viewer Inlined Concerns → `DocumentSaveCoordinator`

### Problem

`app/document_viewer/routes.py` has 5 private helpers that couple the route layer to `VersionService`, `log_audit`, and `save_saved_document`:

- `_resolve_case()` — duplicates Candidate 2's logic
- `_save_document_content()` — delegates to `document_storage.save_saved_document`
- `_log_audit()` — wraps `services.audit.log_audit`
- `_snapshot_version()` — calls `VersionService.create_version` / `create_version_if_changed` with try/except + case-type branching
- `_actor()` — wraps `current_user`

### Deepened Module: `app/services/document_lifecycle.py`

**Interface:**

```python
@dataclass
class SaveResult:
    timestamp: str
    version_number: int | None
    content_hash: str | None
    success: bool

class DocumentSaveCoordinator:
    def __init__(self, case_resolver: CaseResolver | None = None): ...
    def save(self, case_id: int, case_type: str, doc_type: str,
             html_content: str, delta_content: dict | None = None,
             force_snapshot: bool = False) -> SaveResult
```

**What sits behind the seam:**

- Persistence (`save_saved_document`)
- Version snapshotting (`VersionService` — both force and dedup paths)
- Audit logging (with best-effort error swallowing)
- User ID resolution from Flask-Login
- Case-type → VersionService kwarg mapping

**Adapters:**

- `document_viewer/routes.py` — `autosave_document` and `save_document` call `coordinator.save()` instead of 4 private helpers

**Tests:**

- `tests/test_document_lifecycle.py` — mock `VersionService` + `save_saved_document`, assert `SaveResult` fields, verify audit called, verify versioning policy (force vs dedup)

### Risk

Low — internal refactor, no API change.

---

## 4. Inspection Routes Mechanical Split → `InspectionPhotoService`

### Problem

`app/inspection/routes/photo_routes.py` (428 lines) mixes four concerns: EXIF GPS extraction (`_extract_exif_gps`, 80 lines), image validation (PIL verify + size check), file storage (uuid naming, temp directory), OCR dispatch (conditional import + `run_ocr_extraction`), and route definitions. The route file `_pick()` helper (form field fallback to EXIF) is business logic hidden in a route handler.

### Deepened Module: `app/inspection/photo_service.py`

**Interface:**

```python
@dataclass
class PhotoUploadResult:
    photo_id: str
    filepath: str
    raw_lat: float
    raw_lng: float
    accuracy: float
    verification: dict
    stamped: bool

class InspectionPhotoService:
    def upload_evidence(self, inspection_id, file_obj,
                        lat=None, lng=None, accuracy=None,
                        captured_at=None) -> PhotoUploadResult
    def upload_adjudication_photo(self, adjudication_id, file_obj) -> PhotoUploadResult
    def delete(self, photo_id: str) -> bool
    def list_for_inspection(self, inspection_id: int) -> list[PhotoInfo]
    def list_adjudication(self, adjudication_id: int) -> list[PhotoInfo]
```

**What sits behind the seam:**

- EXIF GPS extraction + conversion to degrees
- Image validation (extension, size, PIL verify)
- Secure file naming + temp directory management
- Evidence record creation + DB commit + rollback
- Geo verification dispatch (`verify_photo_location`)
- Image stamping (`process_and_stamp_image`)
- OCR dispatch (conditional `run_ocr_extraction` call)
- Audit logging for photo events

**Adapters:**

- `app/inspection/routes/photo_routes.py` → thin: parse request, call service, return JSON

**Tests:**

- Mock `upload_photo`, `process_and_stamp_image`, `verify_photo_location`
- Test EXIF fallback to form values (`_pick` logic now in service)
- Test file validation (bad extension, oversized, non-image)
- Test OCR dispatch conditional

### Risk

Medium — extracting 428 lines into a service. Must preserve the `Evidence` record creation flow exactly.

---

## 5. PDF Utils Grab-Bag → `PDFAssemblyEngine`

### Problem

`app/utils/pdf_utils.py` (238 lines) mixes: (a) WeasyPrint import guard + HTML→PDF, (b) bookmark CSS injection, (c) post-processing orchestration (delegates to CrossReference + TOC engines), (d) image embedding (HTTP fetch + base64 + local file read). Meanwhile `app/pdf_assembly/__init__.py` exists but is empty — the real PDF logic never moved there.

### Deepened Module: `app/pdf_assembly/engine.py`

**Interface:**

```python
class PDFAssemblyEngine:
    def assemble(self, html_content: str, case_id: int | None = None,
                 adjudication_id: int | None = None,
                 photo_urls: list[str] | None = None) -> tuple[bytes | None, str | None]
    def post_process(self, html_content: str, case_id: int | None = None,
                     adjudication_id: int | None = None) -> str
    def embed_photos(self, photo_urls: list[str]) -> list[dict]
    def generate_from_html(self, html_content: str) -> tuple[bytes | None, str | None]
```

**What sits behind the seam:**

- WeasyPrint import guard + HTML→PDF conversion
- Phase 6+7 post-processing pipeline (CrossReference + TOC + bookmarks)
- WeasyPrint bookmark CSS injection
- PDF hyperlink annotation (`_apply_hyperlinks` from `pdf_assembly/__init__.py`)
- Photo embedding (HTTP fetch, base64, local file read, direct-URL mode)
- Header/footer/page-number injection
- QR code generation
- Signature placeholders

**Backwards-compatible shims** in `app/utils/pdf_utils.py`:

```python
from app.pdf_assembly.engine import PDFAssemblyEngine
_engine = PDFAssemblyEngine()

def generate_pdf_from_html(html): return _engine.generate_from_html(html)
def post_process_pdf_html(html, **kw): return _engine.post_process(html, **kw)
def embed_photos_as_base64(urls): return _engine.embed_photos(urls)
def renumber_html_lists(html): return _engine.post_process(html)  # delegates to CrossReference
```

**Adapters:**

- `adjudication/routes.py`, `case_file_generator/tasks.py`, `document_viewer/renderer.py`, `document_viewer/routes.py` — import from `app.pdf_assembly import PDFAssemblyEngine` (or keep using `pdf_utils` shims)

**Tests:**

- Consolidate `test_phase8_pdf_assembly.py` + `test_pdf_photo_embedding.py` + `test_toc_generator.py` behind the engine interface
- Mock WeasyPrint for post-processing tests
- Test photo embedding: remote URL, local path, direct-URL mode, failure cases

### Risk

Medium — consolidating 5 callers. Must preserve all existing function signatures in `pdf_utils.py` for backward compat.

---

## Implementation Order

| Step | Candidate | Module to create | Files to modify | Effort |
| ------ | ----------- | ----------------- | ----------------- | -------- |
| 1 | #2 (CaseResolver) | `app/shared/case_resolver.py` | 5+ route files | 1 day |
| 2 | #3 (DocumentSaveCoordinator) | `app/services/document_lifecycle.py` | `document_viewer/routes.py` | 1 day |
| 3 | #5 (PDFAssemblyEngine) | `app/pdf_assembly/engine.py` | `pdf_utils.py`, 5 callers | 2 days |
| 4 | #4 (InspectionPhotoService) | `app/inspection/photo_service.py` | `photo_routes.py` | 2 days |
| 5 | #1 (DocumentCaseManager) | `app/shared/document_case_manager.py` | `case_file_generator/`, `adjudication/` | 3 days |

---

## 7. Deepening Roadmap

> See `REFACTORING_PLAN.md` for the full design of each deepening candidate. This table summarizes the implementation order and Module Depth targets.

| Step | Candidate                        | New Module                                | Target Depth | Files to Touch          | Effort |
| ---- | -------------------------------- | ----------------------------------------- | ------------ | ----------------------- | ------ |
| 1    | Cross-module case resolution   | `app/shared/case_resolver.py`             | 1 → 4        | 5+ route files          | 1 day  |
| 2    | Document viewer inlined concerns | `app/services/document_lifecycle.py`      | 2 → 4        | `document_viewer/routes.py` | 1 day  |
| 3    | PDF utils grab-bag              | `app/pdf_assembly/engine.py`             | 3 → 4        | `pdf_utils.py` + 5 callers | 2 days |
| 4    | Inspection routes mechanical split | `app/inspection/photo_service.py`        | 1 → 4        | `photo_routes.py`       | 2 days |
| 5    | Case/Adjudication route duplication | `app/shared/document_case_manager.py`  | 2 → 4        | 2 route files           | 3 days |

---

*End of refactoring plan*
