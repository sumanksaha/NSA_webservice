# Shrink Implementation Plan — Ponytail Audit Findings (Second Pass)

## Status (2026-09-13)

| # | Target | Verdict | Notes |
|---|--------|---------|-------|
| 1 | `case_query_service` inline | ✅ Done (pre-existing) | Manager uses `db.session.get` directly; module kept as read-only utility per plan |
| 2 | Verification simplify | ✅ Done | `_guarded()` helper extracted in `verification_service.py`; public `verify_photo_location` kept; `test_inspection_photo_service` + characterization pass |
| 3 | `word_converter` functional | ✅ Superseded | The `CaseFileWordConverter` the plan targeted no longer exists (DOCX moved to ADR-001 adoc pipeline); remaining `ImprovementNoticeWordConverter` is a different, fully-tested module — not refactored |
| 4 | Adjudication RBAC | ✅ Done | `_rbac_docx_gate` + `_rbac_scope_for_case` + dead `_rbac_scope_for_form` + inline `copy_letter` gate → single `_rbac_check()` |
| 5 | `to_dict` one-liners | ✅ Done (pre-existing) | Both converters iterate `model.__table__.columns` |
| 6 | `parse_date` inline | ❌ Kept deliberately | Shared ISO/timezone/time-suffix normalization used by 5+ call sites; inlining would duplicate logic |
| 7 | Shared RBAC merge | ✅ Done (pre-existing) | `app/shared/rbac.py` is the single source of truth |
| 8 | `sync_orchestrator` Sheets-only | ✅ Done (part) | Stale `test_sync_orchestrator.py` rewritten to Sheets-only contract; stale triple-target comments fixed. `airtable_sync`/`excel_sync`/`pyairtable` KEPT — live `backup_coordinator` targets, not dead code |
| 9 | `context_derivers` inline | ✅ Done (part) | Dead wrappers with zero importers deleted (`derive_case_file_context`, `derive_adjudication_context`, `derive_applicable_sections_from_form_data`, ~140 lines + unused `case_keys` imports). Live multi-consumer rules kept — inlining them would duplicate legal strings |
| 10 | `case_keys` prune | ✅ Done (part) | Dead maps/helpers/TypedDicts deleted (484 → 52 lines, 13 live keys kept). Live constants NOT replaced with literals — typo risk in legal-document keys |

## 1. `app/shared/case_query_service.py` → Inline into `DocumentCaseManager`

**Goal:** Eliminate the indirection layer between DocumentCaseManager and direct model lookups.

**What stays:** CaseQueryService kept as a utility for callers that only need read‑only operations without constructing DocumentCaseManager.

**Changes in `document_case_manager.py`:**

- Keep `CaseQueryService` import and instance; rename internal `_query_service` to `query_service` for clarity.
- Update `get_case`, `get_case_by_number`, `list_cases` to call `self.query_service.get_case`, etc.
- Use `self.case_summary` (already delegates to `query_service.case_summary`).

**Result:** Eliminates a proxy layer while preserving the explicit purpose of CaseQueryService for narrow lookups.

## 2. `app/inspection/verification/` → Simplify Two‑Handler Flow

**Current:** `verification_service.py` and `verification_engine.py` together model "PASS/FLAG" status logic over photos.

**Simplify:**

- Collapse `verification_service` into a `VerificationHelper` class with a single `verify_photos` method.
- Move the logic from `verification_engine` into helper methods (e.g., `compute_status`, `build_verification_context`).
- Inline the standard three‑step process: extract, assess, record.
- Remove dead imports like `tenacity` if used only for retries.

**Result:** Cuts ~150 lines to ~40 lines while preserving the external API (export `verify_photos`).

## 3. `app/food_cell/word_converter.py` → Replace Fat Class with Functional Layout

**Current:** 220‑line `CaseFileWordConverter` with delegate methods (`_add_heading`, `_add_paragraph`).

**Simplify:**

- Replace `class CaseFileWordConverter` with a set of utility functions (`add_heading`, `add_paragraph`, `add_table_row`, `build_petition`, `build_permission_letter`).
- Each `build_*` takes `ctx` dict and returns `bytes` (as before).
- No instance state; plain functions (no `self`).
- Remove `_add_*` delegates → inline docx operations.
- Keep `WordConverterUtils` static if needed, or delete.

**Result:** Reduces code to ~40 lines, removes class boilerplate.

## 4. Adjudication RBAC Consolidation (`app/adjudication/routes.py`)

**Current:** Three nearly identical helpers:

- `_rbac_docx_gate` (called by `download_docx`)
- `_rbac_scope_for_case` (called by `regenerate_adjudication_documents`)
- `_rbac_scope_for_form` (called by `preview_adjudication_route`)

**Simplify:**

- Extract one `_rbac_check(case_id, user)` that returns `(adj, error_response)`.
- In `download_docx`: call `_rbac_check`, then pass `adj`.
- In `regenerate_adjudication_documents`: call `_rbac_check`, then use `adj`.
- In `preview_adjudication_route`: skip case lookup, just apply RBAC on form data via `_rbac_check_for_form`.
- Rename helpers, drop duplicates.

**Result:** Cuts ~15 lines per file (3 helpers → 1).

## 5. `adjudication_to_dict` / `case_file_to_dict` → One‑Liner with SQLAlchemy Model Helpers

**Current:** Manual field‑by‑field extraction (40+ lines each).

**Simplify:**

- Use `self.model.__table__.columns` and iterate, building dicts.
- Or use `db.session.get(self.model, case.id)` then `case.__dict__` with filter.
- Keep `to_dict` for JSON serialization; maintain the same keys.
- Implementation in `DocumentCaseManager` or helper method in each route file.

**Result:** Cuts ~35 lines per converter → ~10 lines.

## 6. `app/utils/filters.py` → Consolidate `parse_date` and `to_words`

**Current:** `parse_date` tries `datetime.strptime` and returns None on error.
**Simplify:**

- Inline in callers: `try: datetime.strptime(value, "%Y-%m-%d") except: None`.
- `to_words`: keep as a single utility; it’s already concise.

**Result:** Removes 5‑line wrapper for `parse_date`.

## 7. Shared RBAC Merge (`case_file_generator/routes.py` & `adjudication/routes.py`)

**Current:** `_case_visible_to_current_user` duplicated.

**Simplify:**

- Move to `app/shared/rbac.py` as a single function.
- Export from `app.shared.rbac` and import in both route files.
- Remove the duplicate definitions.

**Result:** Single source of truth for case visibility checks.

## 8. `app/services/sync_orchestrator.py` → Inline Google Sheets Only

**Current:** `sync_row` dispatches on `"sample"` or `"non_sample"` strings.

**Simplify:**

- Remove Airtable + Excel branches (unused; audit says zero callers).
- Keep Google Sheets via `gspread`.
- Eliminate `sync_row`; inline call in `_sync_to_sheets`.
- Remove `pyairtable` and `excel_sync` imports.

**Result:** Cuts ~100 lines, drops 2 dependencies.

## 9. `app/shared/context_derivers.py` → Inline Simple Derivations

**Current:** `derive_case_track`, `derive_violations`, etc., produce simple dict transforms.

**Simplify:**

- Extract functions but move logic directly into `DocumentCaseManager.prepare_context_fn` (or helper).
- Delete the abstract wrappers.

**Result:** Removes 2‑file indirection.

## 10. `app/shared/case_keys.py` → Remove Unused Constants

**Current:** `DERIVED_APPLICABLE_SECTIONS` etc. used only for dict keys.

**Simplify:**

- Remove constants, use literal strings directly in the code.
- Update any imports that reference the constants.

**Result:** Cleaner import graph; fewer unused symbols.

---

**Net Impact (Estimated):**

- **~600 lines** removed (400 from `DocumentCaseManager`, ~100 from verification, ~80 from converters, ~20 from RBAC, etc.)
- **~7 dependencies** removed or pruned (tenacity, chardet, tqqdm, psycopg3 duplicate, num2words, pyairtable, excel_sync)
- **Fewer files** deleted (feedback_dashboard, case_intelligence, food_cell/email_sender, food_cell/services, ai_assistant/tasks, cleanup_rag_logs script)
- **Simplified mental model:** One RBAC helper, one document management abstraction, direct DB mappings.

**Next Steps:**

1. Apply `DocumentCaseManager` inlining.
2. Refactor verification module into a concise helper.
3. Rewrite `WordConverter` as functions.
4. Consolidate adjudication RBAC helpers.
5. Streamline dict extraction.
6. Merge `parse_date` and RBAC.
7. Drop unused sync targets.
8. Inline context derivers.
9. Prune `case_keys` constants.

All changes follow **ponytail principle**: keep public API stable, drop dead indirection, use stdlib or direct calls where hand‑rolled wrappers exist.
