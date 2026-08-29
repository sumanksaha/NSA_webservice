## Data Flow Architecture (NSA Webservice)

### ✅ Primary Database (PostgreSQL/Supabase)

- **Source of truth**: All UI forms (CaseFile, Adjudication, Inspection, Sample, etc.) save directly to PostgreSQL first via `db.session.commit()`
- **Sync path**: After commit, `sync_row("table_name", ...)` is called synchronously (e.g., in `sample/routes.py`, `inspection/routes.py`, etc.)
- **Verification**: `synced_at` timestamp field is set to current UTC time only after successful write to external store

### ✅ Google Sheets

- **Sync mechanism**: `sync_to_sheets()` in `app/utils/sync.py` (called synchronously after DB commit)
- **Path**: Queries for records where `synced_at IS NULL` → appends rows to Google Sheets tabs (case_files, adjudications, bills, etc.)
- **Failure handling**: Returns 500 error if sync fails; DB transaction rolls back
- **Verification**: `synced_at` is set only after successful Sheets write

### ✅ Airtable

- **Sync mechanism**: `sync_to_airtable()` in `app/services/airtable_sync.py` (called synchronously after DB commit)
- **Path**: Uses pyairtable API to write records to Airtable tables (mapped from sheets_sync)
- **Feature flag**: Controlled by `ENABLE_AIRTABLE_SYNC` (currently false by default)
- **Verification**: `synced_at` is set only after successful Airtable write

### ✅ Excel

- **Sync mechanism**: `sync_to_excel()` in `app/services/excel_sync.py` (called synchronously after DB commit)
- **Path**: Uses Microsoft Graph API (msal) to write to Excel files
- **Feature flag**: Controlled by `ENABLE_EXCEL_SYNC` (currently false by default)
- **Verification**: `synced_at` is set only after successful Excel write

### ✅ Supabase (Separate from Sheets/Airtable)

- **Sync mechanism**: `SupabaseSyncService` in `app/sync/supabase_sync.py`
- **Path**: Independent pipeline that also uses `synced_at` field to find unsynced records
- **Verification**: `synced_at` is set only after successful Supabase write

### 🔑 Key Verification Points

1. **DB → Sheets**: `sample/routes.py` calls `sync_row("sample_repo", ...)` which triggers `sync_to_sheets()`
2. **DB → Airtable**: `sync_orchestrator.py` calls `sync_to_airtable()` after DB commit
3. **DB → Excel**: `sync_orchestrator.py` calls `sync_to_excel()` if enabled
4. **All paths use `synced_at`**: This field is the single source of truth for sync status
5. **No reverse verification**: There is no mechanism to verify that data in Sheets/Airtable/Excel actually contains the row after writing - only that the write attempt succeeded

### 📊 Data Flow Summary

```
UI Form → DB Commit → sync_row() → [Sheets (sync_to_sheets)] → [Airtable (sync_to_airtable)] → [Excel (sync_to_excel)] → [Supabase (SupabaseSyncService)]
                                                                                   ↑
                                                                                   └── All use synced_at field to track status
```

### ✅ Answer to Question

**Yes, UI data actually saves to all three stores (Supabase/PostgreSQL, Google Sheets, Airtable) when:**

- The `ENABLE_AIRTABLE_SYNC` flag is enabled (currently false)
- The `ENABLE_EXCEL_SYNC` flag is enabled (currently false)
- The daily backup schedule is active (ENABLE_BACKUP_SCHEDULE = true)

**However:** There is no guarantee that the data actually reached the external store due to:

- Network failures during sync
- API rate limits
- Authentication failures
- Schema mismatches

The system only verifies that the write attempt succeeded (sets `synced_at`), not that the data actually landed in the external system.
