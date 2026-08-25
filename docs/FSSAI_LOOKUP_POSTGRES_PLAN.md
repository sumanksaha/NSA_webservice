# Plan: Move FSSAI Lookup DBs into Supabase Postgres

**Status:** Proposed — not yet implemented
**Date:** 2026-08-25
**Motivation:** The two SQLite lookup databases (`db/license_data.db`, `db/registration_data.db`) are git-tracked binaries (~21 MB) resolved via fragile filesystem-path discovery (`_resolve_db_path` in `app/utils/lookup.py`). On Render's ephemeral filesystem this is a recurring failure source. Moving them into the existing Supabase Postgres (which already hosts application data) eliminates path resolution entirely and simplifies the bi-monthly data refresh.

---

## 1. Current state (codebase survey)

- **Only one function touches the SQLite files:** `lookup_fssai()` in `app/utils/lookup.py`. (`lookup_ce` is KMC portal scraping — unrelated, untouched.)
- **Data shape:**

    | Table                  | Rows   | Columns                                                  | Key               |
    | ---------------------- | ------ | -------------------------------------------------------- | ----------------- |
    | `license_records`      | 22,599 | license_no, company_name, full_address, expiry_date      | `license_no`      |
    | `registration_records` | 57,453 | registration_no, company_name, full_address, expiry_date | `registration_no` |

- **Dispatch logic:** FSSAI numbers starting with `"1"` → license table; starting with `"2"` → registration table; anything else → error.
- **Callers** (all import `lookup_fssai` / `lookup_ce`; all call `lookup_fssai(no)` expecting `(dict|None, error|None)`):
    - `app/sample/routes.py`
    - `app/case_file_generator/routes.py`
    - `app/adjudication/routes.py`
    - `app/fbo_issue/routes.py`
    - `app/inspection/routes/lookup_routes.py`
- **Tests touching lookup:** `tests/test_step1.py`, `tests/test_step3.py`.

**Key insight:** every caller goes through `lookup_fssai()`, so only that function's internals change. No caller edits.

---

## 2. Implementation steps

### Step 1 — Models (`app/models/lookup.py`)

```python
class FssaiLicense(db.Model):          # table "fssai_licenses"
    license_no: PK
    company_name, full_address, expiry_date

class FssaiRegistration(db.Model):     # table "fssai_registrations"
    registration_no: PK
    company_name, full_address, expiry_date
```

- Register both in `app/models/__init__.py`.
- Primary key gives the exact-match index for free.
- Tables are static-schema reference data; once created by migration they never drift, so Alembic autogenerate is safe.

### Step 2 — One-time loader + refresh tool (`scripts/load_fssai_lookup.py`)

- Opens each local `.db` file via `sqlite3`, streams rows into Postgres with bulk upsert on the primary key → idempotent, safe to re-run.
- ~80k rows total: runs in seconds.
- Run once after `flask db upgrade`; re-run whenever fresh data arrives.

### Step 3 — Rewrite `lookup_fssai()` (same file, same signature)

```python
def lookup_fssai(license_no):
    # prefix "1" -> db.session.get(FssaiLicense, no)
    # prefix "2" -> db.session.get(FssaiRegistration, no)
    # return contract unchanged: (dict|None, error|None)
```

- Keep exact return contract — zero caller changes.
- Requires app/request context; all callers are routes, so fine.
- Delete `_resolve_db_path`, `LICENSE_DB_PATH`, `REGISTRATION_DB_PATH`, and the `sqlite3` usage from `lookup_fssai` — this removes the `_resolve_db_path` complexity introduced in commit `3c90212` wholesale.

### Step 4 — Migration + repo cleanup

- One Alembic migration creating both tables.
- `git rm db/license_data.db db/registration_data.db` (~21 MB out of repo). Keep local copies somewhere outside git as refresh inputs.
- Update `AGENTS.md`: note lookups now live in Postgres tables; document refresh procedure.

### Step 5 — Bi-monthly refresh workflow

Data updates every two months. Laziest sufficient flow:

- Receive updated `.db` (or CSV) export → run. The loader resolves the target from
  `--db-url` or the `DATABASE_URL` env var; SSL (`sslmode=require`) and a
  15 s connect timeout are forced for any `postgresql://` URL, and batches
  default to 5,000 rows:

    ```
    export DATABASE_URL="postgresql://postgres.<project>:<password>@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres"
    python scripts/load_fssai_lookup.py            # idempotent upsert
    ```

    Upsert semantics make it safe to re-run any time. A full refresh of all
    80,052 rows (22,599 licenses + 57,453 registrations) completes in ~48 s
    (~1,600 rows/s) and leaves counts exact with no duplicate primary keys
    (verified via `ON CONFLICT (pk) DO UPDATE`). Bi-monthly = re-run the same
    command; every existing row UPDATEs in place.

- Optional later (skip until painful): admin-only settings route accepting CSV upload calling the same loader.

### Step 6 — Tests

- Update `tests/test_step1.py` / `test_step3.py`: seed rows into the test DB via the new models instead of shipping SQLite fixtures.
- Add a small test covering prefix dispatch ("1", "2", bad prefix) if not already covered.
- Full suite green: `python -m pytest tests/ -q`.

---

## 3. Risks / notes

- **Supabase connection load:** negligible — occasional single-row exact-match queries through the same pooled connections as everything else.
- **Alembic:** models are plain declarative with stable columns; autogenerate will not produce churn.
- **Availability:** lookups need DB connectivity, but they already did; Postgres-backed is strictly more reliable than ephemeral-disk paths.
- **Rollback:** old implementation stays in git history until `.db` files are removed; revert is trivial before that point.

## 4. Effort estimate

Half a day including tests (models + loader + rewrite + migration + test updates).
