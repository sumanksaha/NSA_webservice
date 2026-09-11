# FSSAI Lookup Data — Bi-Monthly Refresh Runbook

The FSSAI license/registration reference data lives in Postgres tables
`fssai_licenses` / `fssai_registrations` (`app/models/lookup.py`). The SQLite
export files in `db/` are now only **refresh inputs** — never commit them again,
and nothing at runtime reads them.

## 1. Receive & inspect the new exports (~every 2 months)

Drop the new `license_data.db` and `registration_data.db` into `db/`
(replacing local copies; keep the old ones until verification passes).
Expected schema — anything else means a malformed export, stop:

- `license_records(license_no TEXT PK, company_name TEXT, full_address TEXT, expiry_date TEXT)`
- `registration_records(registration_no TEXT PK, company_name TEXT, full_address TEXT, expiry_date TEXT)`

```bash
python -c "import sqlite3; c=sqlite3.connect('db/license_data.db'); print(c.execute('SELECT COUNT(*) FROM license_records').fetchone())"
```

Compare counts against the previous run's numbers and sanity-check freshness:
spot-check a few `expiry_date` values — past exports have shipped stale rows
(expiries in 2020), so verify the export is actually current before loading.

## 2. Load into Postgres

```bash
python scripts/load_fssai_lookup.py --dry-run   # preview counts, no writes
python scripts/load_fssai_lookup.py             # idempotent upsert via DATABASE_URL
```

The loader streams every row as `INSERT … ON CONFLICT (<pk>) DO UPDATE`, so it
can be safely re-run at any time. Use `--db-url` to target a non-default database.

## 3. Verify after loading

1. Row counts in Postgres match the source files exactly (22,599 / 57,453 as of
   the initial migration; expect drift over time — match _this_ export's counts).
2. Spot-check one known number through the app path:

```bash
python -c "from app import create_app; from app.utils.lookup import lookup_fssai; app=create_app(); print(lookup_fssai('10000000000000') if False else 'use a real number')"
```

1. Hit any lookup route in staging (e.g. inspection `/inspection` lookup) once.

## 4. Safety & rollback notes

- Upserts overwrite in place: records **removed** upstream vanish on refresh.
  If audit history ever matters ("what did the lookup say on date X?"),
  snapshot both tables first (`CREATE TABLE fssai_licenses_YYYYMMDD AS SELECT …`)
  before loading — see research doc §5.3.
- Never re-introduce the `.db` files into git; they stay local-only as loader inputs.
- If a load fails mid-way, simply fix the cause and re-run — batches already
  committed are idempotent.
