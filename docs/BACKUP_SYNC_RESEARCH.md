# NSA Webservice Application Data Saving & Backup Systems Research

## Overview
This document investigates how the NSA Webservice application saves data to various storage systems:
1. **Primary Storage** - PostgreSQL database (local/Supabase)
2. **Secondary Sync Targets** - Google Sheets, Airtable, Excel Online
3. **Backup Targets** - Cloudflare R2 (via multiple sync paths)
4. **Conflict Resolution & Sync Mechanisms**

## Primary Data Storage: PostgreSQL

### Application Database Schema
The application uses PostgreSQL as its primary datastore with SQLAlchemy ORM. Core models participating in sync (from `app/sync/supabase_sync.py`):
- CaseFile (sample-based violations)
- Adjudication (non-sample adjudications)
- Bill (billing)
- Sample (sample tracking)
- Inspection (inspection records)

## Supabase Sync Service (Phase 17)
- **Source**: `app/sync/supabase_sync.py`
- **Lazy Import Pattern**: Supabase client is lazy-imported for graceful degradation
- **Conflict Resolution**: Uses `SyncState` table with `sync_version` integers for optimistic concurrency
- **Push/Pull Mechanics**:
  - Push: Compares local `sync_version` with Supabase version; mismatches queued as conflicts
  - Pull: Same check in opposite direction
  - Conflicts persisted to `sync_conflicts` table for UI resolution
- **Opt-in System**: Gated by `ENABLE_SUPABASE_SYNC=false` (default)

## Google Sheets Sync
- **Source**: `app/services/sheets_sync.py`
- **Worksheet Mapping**:
  - NonSample_Adjudication → Adjudication
  - Sample_CaseFile → CaseFile
  - Billing → Bill
  - Sample_Repository → Sample
  - Inspection_Log → Inspection
  - FoodCellDOIntimations → FoodCellDOIntimations
- **Sync Mechanics**: Append-only; `_escape_formula()` prevents formula injection
- **Authentication**: gspread with `GOOGLE_CREDENTIALS_JSON`

## Airtable Sync
- **Source**: `app/services/airtable_sync.py`
- **Base Rotation**: Auto-creates new bases when nearing 1,200 record free-tier limit
- **Thread-local Client**: Caches `pyairtable.Api` instances
- **Lazy Import**: Graceful degradation if `pyairtable` not installed
- **Gated by**: `ENABLE_AIRTABLE_SYNC=false` (default dormant)

## Excel Online Sync
- **Source**: `app/services/excel_sync.py`
- **Authentication**: OAuth 2.0 client credentials with `msal.ConfidentialClientApplication`
- **Data Access**: Graph API `usedRange` endpoint, tab-delimited text
- **Dormant Status**: `ENABLE_EXCEL_SYNC=false` (awaiting Azure AD credentials)

## Backup & Redundancy (Priority 7)
- **Source**: `app/services/backup_coordinator.py`
- **Backup Targets** (exports to Cloudflare R2):
  1. **Sheets** → `nsa_backups/sheets_csv/`
  2. **Airtable** → `nsa_backups/airtable_csv/`
  3. **Excel** → `nsa_backups/excel_csv/`
  4. **Full Archive** → `nsa_backups/full_archives/` (complete DB dump + instance files)

- **Scheduled Execution**: Daily via QStash webhook (`backup_redundant_sheets`)
- **Isolated Targets**: Each runs in independent try/catch
- **Retention Policy**: `BACKUP_ARCHIVE_RETENTION` (newest N kept)
- **Health Endpoint**: `/health/backups` (503 if stale>26h)

## Data Flow

### Normal Application Flow
1. User creates/updates data via Flask web interface
2. Data saved to primary PostgreSQL via SQLAlchemy
3. If Supabase sync enabled → data queued for push/pull
4. If Sheets/Airtable/Excel enabled → data appended to targets

### Backup Flow (QStash Scheduled @ 02:00 UTC)
1. For each target: export data → upload CSV to R2
2. Full archive: DB dump + instance files → ZIP → upload to R2

## Environment Variables

| Variable | Purpose | Status |
|----------|---------|--------|
| `DATABASE_URL` | Primary PostgreSQL | Required |
| `SUPABASE_URL` / `SUPABASE_API_KEY` | Supabase sync | Active |
| `SPREADSHEET_ID` / `GOOGLE_CREDENTIALS_JSON` | Sheets sync | Active |
| `AIRTABLE_API_KEY` / `AIRTABLE_BASE_ID` | Airtable sync | Active (when enabled) |
| `MS_TENANT_ID` / `MS_CLIENT_ID` / `MS_CLIENT_SECRET` / `MS_SPREADSHEET_ID` | Excel sync | **Dormant** (no Azure credentials) |
| `R2_*` | Backup storage | Active |
| `ENABLE_BACKUP_SCHEDULE` | Daily schedule flag | Default false |
| `ENABLE_AIRTABLE_SYNC` | Airtable toggle | Default true (in production) |
| `ENABLE_EXCEL_SYNC` | Excel toggle | Default false (dormant) |

## Conflict Resolution & Data Integrity
- Each synced record tracks `sync_version` in `sync_state` table
- Mismatches stored in `sync_conflicts` for manual resolution
- Web UI at `/sync/conflicts` for reviewing conflicts

## Performance & Reliability
1. **Graceful Degradation**: All external syncs lazy-import
2. **Non-blocking Core Flow**: App continues even if sync targets fail
3. **Atomic Operations**: Database transactions ensure consistency
4. **Error Isolation**: Each backup target in independent try/catch
5. **Audit Trail**: All operations logged via Python logging

## Monitoring
- `/health`: Basic application health
- `/health/backups`: Backup freshness dead-man's-switch
- `/sync/*`: Supabase sync status and conflict management

## Summary
The NSA Webservice implements a multi-layered persistence strategy:
1. **Primary**: PostgreSQL (local/Supabase)
2. **Active Sync**: Supabase, Sheets, Airtable, Excel (dormant)
3. **Backup**: Multi-target R2 redundant backups
4. **Restore**: Full-fidelity restoration from R2 archives
5. **Safety**: Lazy imports, conflict resolution, isolated errors, health monitoring