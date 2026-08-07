# Airtable Sync Completion Plan — Priority 7 (Multi-Target Sheets Redundancy)

> **audit-code self-review.** Run before requesting a second review.
> **Audit date:** 2026-08-07 · **Auditor:** this agent
> **Scope:** `app/services/airtable_sync.py`, `app/services/excel_sync.py` (missing), route integration, backup/restore chain, tests, deps.

---

## 1. Audit-Code Self-Review (applied to existing + missing code)

### Supply Chain & Security

| Item                                          | Status         | Notes                                                                                                                                                                           |
| --------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pyairtable>=1.0.0` declared                  | ✅             | In `pyproject.toml`                                                                                                                                                             |
| `httpx>=0.27.0` declared (base-rotation REST) | ✅             | In `pyproject.toml`                                                                                                                                                             |
| `msal>=1.0.0` declared (Excel OAuth)          | ❌ **MISSING** | Must add to `pyproject.toml`                                                                                                                                                    |
| Secrets in diff                               | ⚠️             | `.env.example` contains **real-looking** Airtable credentials (`AIRTABLE_API_KEY=pat3H1z2B90c84Cer…`, `AIRTABLE_BASE_ID=apprLB9PQ4g6OxQT8`). Replace with obvious placeholders. |
| OWASP: formula injection                      | ✅             | `_escape_formula` in both `sheets_sync.py` and `airtable_sync.py`                                                                                                               |
| Config-driven feature flag                    | ⚠️             | `ENABLE_AIRTABLE_SYNC` declared in `.env.example` but **never checked** by code — sync runs whenever API key is present. Wire guard in `sync_to_airtable`.                      |

### Code Quality (audit-code heuristics on `airtable_sync.py`)

| Heuristic                               | Status  | Detail                                                                                           |
| --------------------------------------- | ------- | ------------------------------------------------------------------------------------------------ |
| G34 Stepdown / single abstraction level | ✅      | Client/base/export each at own level                                                             |
| F1 ≤ 3 args / G25 named constants       | ✅ / ⚠️ | `BASE_ROTATION_THRESHOLD = 1100` ✅; magic `page_size=100` ⚠️ (extract to `_PAGE_SIZE = 100`)    |
| G5 DRY                                  | ✅      | Thread-local pattern mirrors `sheets_sync.py` by design                                          |
| C5 commented-out code                   | ✅      | None                                                                                             |
| Law of Demeter                          | ✅      | `_track_airtable_sync` does one DB write                                                         |
| File length < 300 lines                 | ⚠️      | `airtable_sync.py` ≈ 380 lines — extract CSV-export to `AirtableExportMixin` if it grows further |

### Test Coverage

| Function                               | Test                             | Status         |
| -------------------------------------- | -------------------------------- | -------------- |
| `sync_to_airtable`                     | `test_airtable_sync.py`          | ❌ **MISSING** |
| `_base_near_capacity` / `_rotate_base` | `test_airtable_base_rotation.py` | ❌ **MISSING** |
| `_track_airtable_sync`                 | `test_restore_redundant.py`      | ❌ **MISSING** |
| `export_airtable_all_bases_to_r2`      | same                             | ❌ **MISSING** |

**Existing food_cell tests pass (15/15) ONLY because `_patch_sync_fns()` stubs all three sync targets** — they do not exercise real Airtable/Excel code paths.

---

## 2. Current State (what already exists and works)

✅ `app/services/airtable_sync.py` — complete, lazy-import, base-rotation, tracking, R2 export, `is_configured()`
✅ `AirtableBaseMap` model (`app/models/auth.py`) with `created_at` + 3 indexes
✅ Migration `add_airtable_base_map_table.py` (chain head = `food_cell_do_intimation`)
✅ `pyairtable` + `httpx` in `pyproject.toml`
✅ `.env.example` has `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID`, `ENABLE_AIRTABLE_SYNC`
✅ Food Cell post-save trigger calls `sync_to_airtable` (via `_load_sync_fns` in `app/food_cell/services.py`)
✅ Food cell tests 15/15 pass (with mocks)

---

## 3. Missing Pieces (the real gaps)

### GAP 1 — Excel sync module is missing ❌ (BLOCKING)

`app/food_cell/services.py` imports `sync_to_excel` from `app/services/excel_sync` (line 57). The module **does not exist** → `ImportError` → `_sync_to_excel = None` → Excel sync **silently never fires** in production. `sync_to_airtable` IS called but only for food_cell module.

**Fix:** Create `app/services/excel_sync.py`:

- `get_excel_token()` — `msal.ConfidentialClientApplication` client-credentials flow
- `get_excel_graph_session()` — `requests.Session` with Bearer header (returns `None` if any env var missing)
- `sync_to_excel(worksheet_name, row_dict)` → Graph API `POST /drives/{drive}/items/{spreadsheet}/workbook/worksheets('{name}')/rows`
- `export_excel_to_r2()` → `GET .../usedRange/$value`, CSV to R2 `nsa_backups/excel_csv/`
- Reuses `WORKSHEET_MAP` from `sheets_sync.py`

**Deps:** add `msal>=1.0.0` to `pyproject.toml`; add `requests>=2.31.0` as a direct dep (msal depends on it; it is available transitively but not declared — declare explicitly).

### GAP 2 — `AIRTABLE_TABLE_MAP` / `AIRTABLE_FIELD_MAP` incomplete ❌

Currently only `food_cell_do_intimations` is mapped. The 5 route-integration modules have no Airtable table/field mapping.

**Fix:** Extend both dicts in `airtable_sync.py` (mirror `SHEET_COLUMNS` / `WORKSHEET_MAP` from `sheets_sync.py`):

- `sample` → table `Sample_CaseFile`, fields = `SHEET_COLUMNS["sample"]`
- `non_sample` → table `NonSample_Adjudication`, fields = `SHEET_COLUMNS["non_sample"]`
- `billing` → table `Billing`, fields = `SHEET_COLUMNS["billing"]`
- `sample_repo` → table `Sample_Repository`, fields = `SHEET_COLUMNS["sample_repo"]`
- `inspection_log` → table `Inspection_Log`, fields = `SHEET_COLUMNS["inspection_log"]`

### GAP 3 — Route-level `sync_to_airtable()` NOT wired ❌

Only `sync_to_sheets()` is called in 5 route files. `sync_to_airtable()` is never called there.

**Fix:** Add `sync_to_airtable(module, row_dict, record.id)` after each existing `sync_to_sheets(module, row_dict)` call:

| File                                         | Line     | Module key         |
| -------------------------------------------- | -------- | ------------------ |
| `app/case_file_generator/routes.py`          | 466      | `"sample"`         |
| `app/adjudication/routes.py`                 | 507      | `"non_sample"`     |
| `app/inspection/routes/inspection_routes.py` | 182, 291 | `"inspection_log"` |
| `app/sample/routes.py`                       | 212, 336 | `"sample_repo"`    |
| `app/bill_generator/routes.py`               | 177      | `"billing"`        |

Pattern (mirrors sheets):

```python
success = sync_to_sheets("sample", row_dict)
# Best-effort parallel Airtable sync
try:
    sync_to_airtable("sample", row_dict, record.id)
except Exception as e:
    current_app.logger.warning(f"Airtable sync failed [{module}]: {e}")
```

### GAP 4 — Feature flag `ENABLE_AIRTABLE_SYNC` not enforced ❌

Declared in `.env.example` but `airtable_sync.py` never reads it.

**Fix:** Guard at the top of `sync_to_airtable`:

```python
if not current_app.config.get("ENABLE_AIRTABLE_SYNC", False):
    return False
```

### GAP 5 — Backup script + settings route + **Sheets CSV export missing** ❌

`scripts/backup_redundant_sheets.py` and `POST /admin/backup-redundant-to-r2` route do not exist. **Additionally**, `export_sheets_to_r2()` does **NOT** exist in `sheets_sync.py` (verified: only `sync_to_sheets` is defined there) — so the backup script needs a Sheets CSV exporter too.

**Fix:**

- Add `export_sheets_to_r2()` to `app/services/sheets_sync.py` (downloads all worksheets → combined CSV → R2 `nsa_backups/sheets_csv/`) — mirrors `export_airtable_all_bases_to_r2()` pattern
- Create `scripts/backup_redundant_sheets.py` (~50 lines): `run_backup()` calls `export_sheets_to_r2()` + `export_airtable_all_bases_to_r2()` + `export_excel_to_r2()`, returns `{"sheets": bool, "airtable": bool, "excel": bool}`
- `app/settings/routes.py`: add `POST /backup-redundant-to-r2` route (admin-only, QStash webhook)

### GAP 6 — Restore chain extension missing ❌

`app/utils/sync.py` has no `restore_from_airtable_csv()` / `restore_from_excel_csv()`.

**Fix:** Add to `app/utils/sync.py`:

- `_list_r2_csv_backups(prefix)` — list latest CSV under `nsa_backups/{prefix}_csv/`
- `_download_r2_csv(key)` — `boto3`/`s3` get_object
- `restore_from_airtable_csv()` — download → parse → insert
- `restore_from_excel_csv()` — download → parse → insert

### GAP 7 — Tests missing ❌

No test files exist for Priority 7.

**Fix:** Create (all mocked, no real credentials):

- `tests/test_airtable_sync.py` (20 tests) — `sync_to_airtable`, field mapping, formula-injection escape, tracking
- `tests/test_excel_sync.py` (20 tests) — `sync_to_excel`, token caching, CSV export
- `tests/test_restore_redundant.py` (15 tests) — restore chain priority, CSV parsing, empty-DB detection
- `tests/test_airtable_base_rotation.py` (10 tests) — capacity detection, `_rotate_base`, multi-base routing

---

## 4. Implementation Order (lazy — smallest viable increments)

| Order | Task                                                                                                                                       | Effort | Risk   | Dependency |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------ | ------ | ---------- |
| 1     | Add `msal>=1.0.0` to `pyproject.toml`; fix `.env.example` placeholders                                                                     | 5 min  | Low    | —          |
| 2     | Create `app/services/excel_sync.py` (mirrors airtable_sync.py structure)                                                                   | 2h     | Medium | msal dep   |
| 3     | Extend `AIRTABLE_TABLE_MAP` / `AIRTABLE_FIELD_MAP` with 5 modules                                                                          | 1h     | Low    | —          |
| 4     | Wire `ENABLE_AIRTABLE_SYNC` guard + route-level `sync_to_airtable` calls (5 files)                                                         | 1h     | Low    | #3         |
| 5     | Add `export_sheets_to_r2()` to `sheets_sync.py` + create `scripts/backup_redundant_sheets.py` + `POST /admin/backup-redundant-to-r2` route | 1h     | Low    | #2         |
| 6     | Add `restore_from_airtable_csv` / `restore_from_excel_csv` to `app/utils/sync.py`                                                          | 2h     | Medium | —          |
| 7     | Create 4 test files (55 tests total)                                                                                                       | 3h     | Low    | #2–#6      |
| 8     | End-to-end: create sample → verify airtable_base_map row exists (mocked)                                                                   | 30 min | Low    | #4         |

**Parallelization:** #2 and #4 are independent after #1. #3 can run in parallel with #2. #5 depends on #2. #6 is independent. #7 depends on all above.

---

## 5. Acceptance Criteria

- [ ] `python -c "from app.services.airtable_sync import sync_to_airtable"` imports without error
- [ ] `python -c "from app.services.excel_sync import sync_to_excel"` imports without error
- [ ] `sync_to_airtable("sample", {...}, 1)` returns `False` when `ENABLE_AIRTABLE_SYNC=false`
- [ ] `sync_to_airtable("sample", {...}, 1)` inserts into `AirtableBaseMap` when mocked client succeeds
- [ ] `_rotate_base()` creates a new base via REST API when threshold exceeded
- [ ] `export_airtable_all_bases_to_r2()` writes combined CSV to R2 (mocked)
- [ ] `POST /admin/backup-redundant-to-r2` returns `{"sheets":.., "airtable":.., "excel":..}`
- [ ] `restore_from_airtable_csv()` + `restore_from_excel_csv()` parse R2 CSVs
- [ ] `pytest tests/test_airtable_sync.py tests/test_excel_sync.py tests/test_restore_redundant.py tests/test_airtable_base_rotation.py` all pass
- [ ] `ruff check .` clean on all new/modified files
- [ ] `.env.example` Airtable key is an obvious placeholder, not real-looking

---

## 6. Deviations / Rationalizations (audit-code "Red Flags" — name them)

1. **Feature flag as `config.get` boolean, not a proper toggle service** — acceptable: the existing codebase (e.g. `DISABLE_PDF_GENERATION`) uses the same `app.config.get("X", False)` pattern. A dedicated toggle service would be over-engineering (YAGNI).
2. **Thread-local client caching** (`_thread_local`) mirrors `sheets_sync.py` exactly — duplication of pattern, but consistency with existing code > premature abstraction. No action.
3. **`export_*` functions are ~60 lines each** — exceeds 300-line file ideal if in same module. Mitigation: file is already under 400; if backup logic grows, extract to `app/services/backup_export.py`. Not needed yet.
4. **Airtable REST base-rotation uses `httpx` while record sync uses `pyairtable.Api`** — two HTTP clients for one service. Justified: `pyairtable` doesn't expose base-creation (meta API); REST is the only path. `httpx` is already declared. Acceptable.
