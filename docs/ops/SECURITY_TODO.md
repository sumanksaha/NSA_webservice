# 🔐 Security Hardening — Remaining Work Items

Generated from the full security audit (July 26, 2026).

> **🔄 Re-audited against the codebase (2026-08-26).** Every item was re-verified
> against primary sources (actual files/lines) and the remaining gaps were then
> implemented: S10c backup monitoring + the missing `/csp-report` collector are
> now done (`tests/test_backup_monitoring.py` 12/12). The only residual item is
> tightening `'unsafe-inline'` under S2.

**Legend:** 🔴 P0 (security risk) · 🟡 P1 (missing feature) · 🟠 P2 (hardening) · ⚪ P3 (CI/DevOps)

---

## 🔴 P0 — Security Risk

### S7: Scraper TLS — Remove `check_hostname=False` and `CERT_NONE` — ✅ DONE (2026-08-06)

**File:** `app/utils/lookup.py`

**Verified (2026-08-26):** lines 118–119 now read
`ctx = ssl.create_default_context()` + `ctx.set_ciphers("DEFAULT@SECLEVEL=1")`.
Zero occurrences of `check_hostname=False` or `CERT_NONE` anywhere in the repo.
Recorded in `task.md` ("S7: Scraper TLS Security Fix", 2026-08-06).

---

## 🟡 P1 — Missing Feature

### S9a: Extend StaleDataError handling to remaining blueprints — ✅ DONE (2026-08-06)

**Verified (2026-08-26):**
- Models: `version_id` + `__mapper_args__["version_id_col"]` present on
  `Inspection` (`app/models/inspection.py:24-28`), `Sample` + `Bill`
  (`app/models/billing.py:15-19, 59-62`), `CaseFile`
  (`app/models/document.py:17-21`) — also `DoIntimation`.
- Routes catch `StaleDataError` → 409 on **PUT and DELETE** for both blueprints:
  `app/inspection/routes/inspection_routes.py` (L293 PUT, L312 DELETE),
  `app/sample/routes.py` (L317, L338).
- Tests: `tests/test_concurrency_inspection.py` — 4/4 pass (incl. the original
  one-line 409-in-`jsonify()` bug fixed to return a proper tuple).

---

## 🟠 P2 — Hardening Completion

### S2: Enforce CSP (flip from report-only) — ✅ ENFORCED (verified 2026-08-26)

**File:** `app/__init__.py`, line 294

**Current:** `content_security_policy_report_only=False` — CSP is enforced,
with HSTS + Secure cookies in production (`force_https`/`session_cookie_secure`
gated on `is_production`). Recorded as done in `task.md`.

**Residual gaps (still worth doing):**
1. ~~**No `/csp-report` route exists.**~~ ✅ **Implemented (2026-08-26):**
   `POST /csp-report` (`app/health/routes.py`) — public, CSRF-exempt,
   bounded 4 KB body, accepts both legacy `application/csp-report`
   (`{"csp-report": …}`) and Report-To style (`{"csp-violation-report": …}`),
   logs directive/blocked-uri at WARNING, always answers 204. Covered by
   `tests/test_backup_monitoring.py`.
2. ⚠️ `script-src` still allows `'unsafe-inline'` (`__init__.py:277`). Moving
   inline JS into external bundles would let this be tightened.


### S6a: Remove legacy `suggester.py` from root — ✅ DONE (2026-08-04)

**File:** `suggester.py` (project root) — **deleted** (with S6a/S6b cleanup + wiring, see `task.md`).

**What:** There was a duplicate rule-based suggester at `app/utils/suggester.py` (the one actually imported). The root-level `suggester.py` was unused. Removed it to avoid confusion.

**Check:**
- [x] Confirmed `suggester.py` at root is not imported anywhere (0 matches)
- [x] Backported docstring + type annotations to `app/utils/suggester.py`
- [x] Deleted the file + tracked `__pycache__/suggester.cpython-313.pyc`
- [x] `pytest tests/` passes with 0 import errors

### S6b: Remove legacy root-level `sections_data.py` — ✅ DONE (2026-08-04)

**File:** `sections_data.py` (project root) — **deleted**.

**What:** Duplicate of canonical `app/utils/sections_data.py` (pathlib-based, typed). Root copy was older with a CWD-dependent relative path and stale docstring; neither version had live importers.

**Check:**
- [x] Confirmed 0 imports of either version
- [x] Deleted root file + tracked `__pycache__/sections_data.cpython-313.pyc`
- [x] Kept `fss_sections.md`, `fso_list.md`, `app/utils/sections_data.py`

### S6c: Wire canonical `sections_data` into suggester — ✅ DONE (2026-08-04)

**What:** `app/utils/suggester.py` now imports `VALID_SECTION_IDS`/`SECTIONS` from `app/utils/sections_data.py` (single source of truth); `_MANUAL_ONLY_SECTIONS` is asserted against the whitelist and outputs are filtered by it. New tests in `tests/test_suggester_sections_data.py`.

---

## ⚪ P3 — CI/DevOps

### S10a: Create Dependabot configuration — ✅ DONE (verified 2026-08-26)

**File:** `.github/dependabot.yml` — exists and is **broader than the original
template**: three ecosystems (`pip`, `github-actions`, `npm`), weekly Monday
09:00 Asia/Kolkata schedule, targeting `main` (the template's
`gis-implementation` branch is obsolete), labels + commit-message prefixes,
PR limits, reviewer assignment.

### S10b: Create pip-audit GitHub Action — ✅ DONE (verified 2026-08-26)

**File:** `.github/workflows/pip-audit.yml` — exists; runs on push/PR to
`main` **plus a weekly scheduled scan** (cron Mon 06:00 UTC) with concurrency
grouping. Complements the broader CI gate set (`lint`, `validation`,
`docker-build`, `release`, `ce-v2-regression`, `deploy`).

### S10c: Database backup monitoring — ✅ IMPLEMENTED (2026-08-26)

**What was built (verified by `tests/test_backup_monitoring.py` — 12/12 pass):**
- **Bookkeeping:** `run_backup()` in `app/services/backup_coordinator.py` now
  persists its per-target outcome to the `settings` table via
  `record_backup_result()` (`last_backup_at` ISO-UTC + `last_backup_results`
  JSON). Best-effort — bookkeeping failure can never fail the backup.
  Covers both the daily QStash schedule and the admin `POST
  /settings/backup-redundant-to-r2` route (single choke point).
- **Dead-man's-switch endpoint:** `GET /health/backups`
  (`app/health/routes.py`, public) → **200** when fresh (≤26h) and every
  target succeeded; **503** on `never` / `stale` (>26h, tolerates one missed
  daily run) / `degraded` (a target failed). Point any uptime monitor at it
  for alerting without extra infrastructure.
- **Runbook note:** Render free-tier constraints documented here; a full ops
  runbook remains optional follow-up work.

**Alerting path:** external uptime monitor on `/health/backups` (503 = page).
No in-app email/webhook alerter — deliberate scope cut for the free tier.



---

## ✅ Completed Steps

| # | Step | Date |
|---|------|------|
| 1 | Secrets Migration (env vars, .gitignore, .env.example) | ✅ |
| 2 | HTTPS + Headers (Talisman, CSP enforced `report_only=False`, ProxyFix; HSTS prod-only) | ✅ (residuals: no `/csp-report` handler; `'unsafe-inline'` scripts) |
| 3 | Session Cookies (Secure, HttpOnly, SameSite, TTL) | ✅ |
| 4 | CSRF Protection (flask-wtf, tokens on all POST forms, AJAX) | ✅ |
| 5 | Authentication (flask-login, global before_request gate, werkzeug hashing) | ✅ |
| 6 | Output Sanitization (no \|safe, no raw HTML for PDFs) | ✅ |
| 7 | Scraper TLS (default context + SECLEVEL=1, verification on) | ✅ (2026-08-06) |
| 8 | Audit Logging (RecordAudit table, after_flush, login events) | ✅ |
| 9 | Optimistic Locking (version_id_col incl. Inspection/Sample/Bill/CaseFile/DoIntimation; StaleDataError → 409 on PUT+DELETE) | ✅ (2026-08-06) |
| 10 | Dependency scanning (Dependabot ×3 ecosystems + pip-audit workflow w/ weekly cron) | ✅ (verified 2026-08-26) |
| 11 | Backup **monitoring** (`/health/backups` dead-man's-switch + `run_backup()` bookkeeping) | ✅ (2026-08-26) |
| 12 | CSP violation collector (`POST /csp-report`) | ✅ (2026-08-26) |

> **Bottom line (2026-08-26):** all original audit items closed. Only residual:
> tighten `'unsafe-inline'` in `script-src` (S2 #2) if inline JS is ever bundled.

