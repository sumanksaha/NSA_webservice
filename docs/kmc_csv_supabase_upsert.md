# KMc License/Issuance CSV Upsert Report

## Introduction

This report documents the search for `kmc_license_issued.csv` and `kmc_registration_issued.csv` files in the codebase and describes the current state of the FSSAI license/registration data infrastructure. It also examines the Supabase configuration and determines whether an upsert operation can be performed.

## Search for CSV Files

### kmc_license_issued.csv
**Status: NOT FOUND**

- Searched recursively across all 893 files in the codebase using `search_codebase` with pattern `kmc_license_issued`.
- Result: No matches found.
- The file does not exist in the repository.

### kmc_registration_issued.csv
**Status: NOT FOUND**

- Searched recursively across all 893 files in the codebase using `search_codebase` with pattern `kmc_registration_issued`.
- Result: No matches found.
- The file does not exist in the repository.

### Additional CSV References

While the specific `kmc_*` CSV files are absent, the codebase does contain FSSAI lookup reference data stored in SQLite files:
- `db/license_data.db` – contains FSSAI license records (tables: `fssai_licenses`)
- `db/registration_data.db` – contains FSSAI registration records (tables: `fssai_registrations`)
These SQLite files are gitignored (`.gitignore` line 409: `db/*.db`).

## Supabase Configuration

### Environment Variables

From `.env.example`:
- `SUPABASE_URL` – defined but empty (requires manual setup)
- `SUPABASE_API_KEY` – defined but empty (requires manual setup)
- `ENABLE_SUPABASE_SYNC` – set to `false` (disables Supabase sync)

### Supabase Sync Service

The `app/sync/supabase_sync.py` module implements a Supabase sync service that synchronizes business entities (CaseFile, Adjudication, Bill, Sample, Inspection) to a Supabase database. However:
- This service does **not** sync the FSSAI license/registration data.
- The Supabase sync is currently disabled (`ENABLE_SUPABASE_SYNC=false`).
- Without enabling the sync and providing valid `SUPABASE_URL`/`SUPABASE_API_KEY`, no data transfer can occur.

## Upsert Process Attempt

### Can the CSV files be uploaded?

**No.** The required files `kmc_license_issued.csv` and `kmc_registration_issued.csv` are not present in the codebase. Additionally:

1. **Missing source data**: The actual FSSAI license/registration data resides in SQLite files (`db/license_data.db`, `db/registration_data.db`), not in CSV format.
2. **Missing destination**: The Supabase sync service is disabled and requires `SUPABASE_URL` and `SUPABASE_API_KEY` environment variables to be configured.
3. **No CSV files to process**: Since the target files do not exist, there is nothing to upsert.

### Current State Summary

| Item | Status |
|------|---------|
| `kmc_license_issued.csv` | **Not Found** – file does not exist in the repository |
| `kmc_registration_issued.csv` | **Not Found** – file does not exist in the repository |
| Supabase (`SUPABASE_URL`, `SUPABASE_API_KEY`) | **Empty** – requires manual configuration |
| Supabase sync enabled (`ENABLE_SUPABASE_SYNC`) | **Disabled** – prevents any data synchronization |
| FSSAI data source (`db/license_data.db`, `db/registration_data.db`) | **Present** – SQLite files containing the reference data |

## Conclusion

**The upsert operation cannot be performed** because:

1. The target CSV files (`kmc_license_issued.csv`, `kmc_registration_issued.csv`) are **not present** in the codebase.
2. The underlying FSSAI data is stored in SQLite files (`db/license_data.db`, `db/registration_data.db`), not in CSV format.
3. Supabase credentials are not configured (empty `SUPABASE_URL`/`SUPABASE_API_KEY` in `.env.example`).
4. The Supabase sync service is disabled (`ENABLE_SUPABASE_SYNC=false`).

### Recommendations

1. **Provide the missing CSV files** – If the intended data should come from CSV files, add `kmc_license_issued.csv` and `kmc_registration_issued.csv` to the repository (outside the `db/` directory, as they are not database files).
2. **Configure Supabase** – Set `SUPABASE_URL` and `SUPABASE_API_KEY` in the environment (or `.env`) to enable the sync service.
3. **Enable Supabase sync** – Change `ENABLE_SUPABASE_SYNC` to `true` in `app/__init__.py` or via the deployment configuration.
4. **Use existing FSSAI data** – The FSSAI license/registration data is already available in `db/license_data.db` and `db/registration_data.db`. Consider migrating this data to Supabase if desired, using the existing `scripts/load_fssai_lookup.py` loader.

## References

- `app/models/lookup.py` – Defines `FssaiLicense` and `FssaiRegistration` SQLAlchemy models.
- `scripts/load_fssai_lookup.py` – Loads FSSAI data from SQLite into Postgres tables.
- `.env.example` – Contains `SUPABASE_URL`, `SUPABASE_API_KEY`, and `ENABLE_SUPABASE_SYNC` configuration.
- `app/sync/supabase_sync.py` – Supabase sync service implementation (business data only, not FSSAI).
- `.gitignore` – Line 409 ignores `db/*.db` files.