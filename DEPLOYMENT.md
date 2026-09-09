# NSA Webservice — Deployment, Data & Operations Guide

> Consolidated from: `POSTGRES_MIGRATION.md`, `docs/FSSAI_LOOKUP_*`, `docs/FSSAI_REINGEST_PLAN.md`, `docs/BACKUP_SYNC_RESEARCH.md`, `docs/INGESTION_READINESS.md`, `docs/MULTIDOMAIN_INTEGRATION.md`, `docs/COVERAGE_COMPLETENESS.md`, `docs/WSL_PI_BRIDGE.md`, `docs/LINE_ENDINGS_SETUP.md`, `docs/kmc_csv_supabase_upsert.md`, `docs/DOCUMENT_LOADER_PERFORMANCE.md`.

---

## 1. Render Deployment (PostgreSQL)

The `render.yaml` blueprint provisions a managed PostgreSQL database (`nsa-webservice-db`), wires the connection string to the web service via `fromDatabase`, and automatically connects the application to PostgreSQL.

**Important:** Free-tier Render PostgreSQL databases expire after 90 days unless upgraded. See the warning in `render.yaml`.

### WSL Dev Stack (Local)

Development runs inside **WSL2 Ubuntu 26.04 (Resolute)** with a full Linux toolchain. The Windows host only provides editors (VS Code/Cursor) via the **Remote-WSL** extension. PostgreSQL/Redis run natively in WSL — Docker Desktop is redundant and can be uninstalled.

### Git Line Endings

The target deployment is Linux. Configure Git to normalize line endings to LF to eliminate LF/CRLF warnings:

```bash
git config --global core.autocrlf input
git config --global core.eol lf
```

### Document Loader Performance (100,000+ Docs)

The document loader is **I/O-bound** (reading files from disk), not CPU-bound. `ThreadPoolExecutor(max_workers=8)` is appropriate for local NVMe SSD; use 4–8 workers locally, 8–16 on network NAS/NFS.

### Multi-Domain Corpus — Ingestion Readiness

All 26 docs evaluated (2026-08-10): 24 text-extractable, ready; 2 image-only scans requiring OCR (`Prevention_of_Cruelty_to_Animals_Rules_2017.pdf`, `The_WestBengal_…_Animals_Rules_2016.pdf` — measured 148.8 s/page @ 300 DPI CPU).

### Corpus Identity Coverage

- **Status:** P1 (`document_title`) + P2 (L7 propagation) implemented and applied live (2026-08-18).
- **Result:** 58.0% of all chunks / **82.4% of substantive (hl≥2)** chunks carry retrievable identity (up from 51.3% / 71.6%).
- **Remaining gap:** 2,989 substantive chunks — rule docs (1,414), BNS space-stripped OCR (715), Nutraceuticals bilingual (399) — all requiring re-ingestion (P3/P4), not payload-side fixes.
- **Tooling:** `evaluation/coverage_audit.py` (repeatable audit, JSON at `out/cache/coverage_audit.json`).

### FSSAI Lookup — Postgres Migration (Complete)

The two SQLite lookup databases (`db/license_data.db`, `db/registration_data.db`, ~21 MB git-tracked binaries) were moved into the existing Supabase Postgres (tables `fssai_licenses` / `fssai_registrations`, model `app/models/lookup.py`) on 2026-08-25.

- **Rationale:** Eliminates fragile filesystem-path resolution (`_resolve_db_path` in `app/utils/lookup.py`), which is a recurring failure source on Render's ephemeral filesystem; simplifies the bi-monthly refresh.
- **Only one function touched:** `lookup_fssai()` in `app/utils/lookup.py` (`lookup_ce` is KMC portal scraping — untouched).
- **Current state:** SQLite export files in `db/` are now **refresh inputs only** — never commit them again, and nothing at runtime reads them.
- **Bi-monthly refresh runbook:** Drop new `license_data.db` / `registration_data.db` into `db/` (replacing local copies; keep old ones until verification passes). Expected schema:
    - `license_records(license_no TEXT PK, company_name TEXT, full_address TEXT, expiry_date TEXT)`
    - `registration_records(registration_no TEXT PK, company_name TEXT, full_address TEXT, expiry_date TEXT)`
- **KMC CSVs:** `kmc_license_issued.csv` and `kmc_registration_issued.csv` were searched for across all 893 files — **NOT FOUND**. No upsert is currently possible for KMC data.

### FSSAI Re-ingest (Complete)

P1-4 remediation rebuilt `fssai_legal_768` from the local DB (2026-08-11): **12,819 chunks, full §5.1 metadata**, verified results: 12,819 points, 29/29 docs, `act_name` 100%, reconcile matched 12,819 / failed 0 / unexplained 0. Qdrant total 27,343 = Neo4j 27,343. Live evidence: `reports/fssai_reingest_run.log`, `CORPUS_IDENTITY_REPORT.md` §8.

---

## 2. Backup & Sync Targets

Application data flows to multiple systems:

1. **Primary:** PostgreSQL (local/Supabase)
2. **Secondary sync:** Google Sheets, Airtable, Excel Online
3. **Backup:** Cloudflare R2 (via multiple sync paths)
4. **Conflict resolution:** Supabase sync (cloud sync)

Core models participating in sync (from `app/sync/supabase_sync.py`): `CaseFile` (sample-based violations), `Adjudication` (non-sample adjudications).

### Case File & Adjudication Editability

Both `CaseFile` and `Adjudication` entities support updates through the web UI/API. Routes exist and handle field modifications, though certain conditions (`is_dismissed`, `notice_issued_at`, `is_locked`) may prevent edits on certain records.

---

## 3. Cloud Sync (Phase 17) — ⚠️ In Progress

- ✅ R2/B2 + Cloudinary + Sheets done
- ⚠️ Supabase bridge, conflict resolution, sync-status UI pending

See `PROJECT_PLAN.md` §2 for the full sequence and `docs/BACKUP_SYNC_RESEARCH.md` for the data model.

---

## 4. Deployment Checklist

- [ ] Render `render.yaml` blueprint deployed (PostgreSQL provisioned, connection string wired)
- [ ] Supabase sync targets configured (Sheets, Airtable, Excel Online)
- [ ] Cloudflare R2 backup targets configured
- [ ] WSL2 Ubuntu dev stack running (PostgreSQL/Redis native)
- [ ] Git line endings normalized to LF
- [ ] FSSAI lookup data refreshed from SQLite exports into Postgres (bi-monthly)
- [ ] Qdrant corpus coverage audited (`evaluation/coverage_audit.py`)
- [ ] Phase 17 cloud sync (Supabase bridge, conflict resolution, sync-status UI) implemented
- [ ] Rust extension (`nsa_rust`) built and deployed via maturin (see `PROJECT_PLAN.md` §3)

---
