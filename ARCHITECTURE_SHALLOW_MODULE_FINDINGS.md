## Review — Shallow Module Deepening (AGENTS.md §8 + task.md D1–D9)

**Evidence sources:** AGENTS.md §8 (lines 280–340), task.md Deepening §D1–D9 (lines 882–1171), code at `app/shared/case_resolver.py`, `app/services/document_lifecycle.py`, `app/pdf_assembly/engine.py`, `app/inspection/photo_service.py`, `app/shared/document_case_manager.py`, `app/utils/sync.py`, `app/utils/lookup.py`, `app/annexure/routes.py`, `app/evidence/routes.py`, plus live pytest runs (31 passed, 0 failed).

### Completed deepening (depth increased — verified)

- **D1 CaseResolver** (`app/shared/case_resolver.py`: 109L) — interface `resolve(case_id, kind) -> ResolvedCase | None`; 7 tests pass (`test_case_resolver.py`). **Locality:** high (replaces inline lookups in 5 blueprints). **Depth:** 1→4.
- **D2 DocumentSaveCoordinator** (`app/services/document_lifecycle.py`: 210L) — encapsulates 5 private helpers (`_resolve_case`, `_save_document_content`, `_log_audit`, `_snapshot_version`, `_actor`) into `save()`; 12 tests pass (`test_document_lifecycle.py`). **Depth:** 2→4.
- **D3 PDFAssemblyEngine** (`app/pdf_assembly/engine.py`: 785L) — consolidated from `pdf_utils.py` grab-bag; backward-compat shims preserved; interface `assemble/post_process/embed_photos/generate_from_html`. **Depth:** 3→4. ⚠️ **No dedicated `test_pdf_assembly_engine.py` found** — validation relies on integration only.
- **D4 InspectionPhotoService** (`app/inspection/photo_service.py`: 503L) — extracted EXIF/validation/storage/OCR/geo from `photo_routes.py`; 28 tests pass (`test_inspection_photo_service.py`). **Depth:** 1→4.
- **D5 DocumentCaseManager** (`app/shared/document_case_manager.py`: 772L) — parameterized `(model, template_dir, bp, case_type, sections_fn)`; eliminates ~1,500L duplication across case_file_generator + adjudication routes. **Depth:** 2→4. ⚠️ **No dedicated `test_document_case_manager.py` found**.

### Open shallow modules (need deepening — evidence from code + task.md)

- **D6 `app/utils/sync.py`** (465L) — **highest leverage**: 3 byte-identical maps (`_AIRTABLE_TABLE_MAP = _WORKSHEET_MAP = _SHEETS_RESTORE_MAP = _RESTORE_MODULE_MAP`, line 333); 3 copy-pasted restore pipelines (`restore_from_airtable_csv`/excel/sheets); dead duplicate `sync_to_sheets()` (line 52) with zero production importers (`grep` confirmed); `_build_column_map` returns `{}` unconditionally (no-op hook). **Seam:** no justified adapter split — all dead/duplicate. **Recommendation:** delete dead code → unify to 1 `BACKUP_MODULE_TO_TABLE` + `BackupRestorer` adapter class (see task.md Phase A–E).
- **D7 audit callers** (`annexure/routes.py:53`, `evidence/routes.py:105`, `document_lifecycle.py:181`) — same 9-line `_log_audit` wrapper (hardcodes `entity_type="annexure"`/`"evidence"`/derived) duplicated 3x; `actor` source inconsistent (`current_user.username` vs `form_data.get(...)` vs local `_actor()`). **Locality:** very high (all callers in 3 files). **Seam:** deep core (`log_audit` in `app/services/audit.py`) is already deep; shallow layer is caller wrapper. **Recommendation:** `AuditLogger` factory (`audit_logger(entity_type).log(...)`) per task.md.
- **D8 `app/utils/lookup.py`** (152L) — `lookup_fssai()` partially resolved (Postgres migration done, 2026-08-25); `lookup_ce` (line 82) is 50-line god-function mixing rate-limit (fcntl file lock + timestamp), SSL context (`ssl.create_default_context` + cipher override), cookie-warming `httpx.Client`, regex JSON repair (`re.sub(...)` for unquoted keys), and response shaping. **Inconsistent contracts:** FSSAI returns `(dict|None, str|None)`; CE raises or returns `None`; 6 call sites across 5 blueprints handle differently. **Depth:** 1→4 requires `LookupResult` dataclass + `RateLimiter` adapter + injectable HTTP adapter + pure `repair_kmc_json()`. **Leverage:** high once interface is fixed; blast radius requires INTERFACE-DESIGN.md (3 parallel sub-agents per task.md).
- **D9 `verification_service.py`** — NOT a deepening candidate (task.md §Deepening Strategy Notes). **Seam:** 3 sub-modules (`geo`, `ip`, `distance`) each have justified adapter split (production HTTP vs test mock) — merging would destroy independent testability. **Recommendation:** optional 1-line `_degrade()` helper only.

### Vocabulary-aligned assessment
- **Depth:** D1–D5 reached target (3→4 or 2→4). D6 (1→4) and D7 (1→3) high-payoff, low-risk. D8 (1→4) medium-risk due to contract change.
- **Locality:** D6/D7 are highest (all duplicates in 1–3 files). D8 is spread (6 call sites / 5 blueprints).
- **Leverage:** D6 > D7 > D8 — D6 deletes dead code (zero production impact confirmed by grep); D7 removes 3 identical wrappers; D8 requires design before build.
- **Seam / Adapter:** D4, D9 have justified seams (photo service = EXIF/validation/storage/OCR/geo split; verification = 3 external services). D6 has none — just delete. D7 should be a factory seam (`AuditLogger`). D8 should be 3 adapter seams (`RateLimiter`, HTTP adapter, JSON repair pure function).

### Residual risks
- No dedicated test file for PDFAssemblyEngine or DocumentCaseManager (integration-only coverage).
- `app/utils/sync.py` has zero production importers — safe to delete, but requires migration of `test_priority7_redundancy.py` (currently patches 7 private internals).
- `lookup_ce` fixes need `INTERFACE-DESIGN.md` before implementation (contract change across 6 call sites).
- Nothing blocked; D1–D5 verified green (31 tests passed).