# 🔐 Security Hardening — Remaining Work Items

Generated from the full security audit (July 26, 2026).

**Legend:** 🔴 P0 (security risk) · 🟡 P1 (missing feature) · 🟠 P2 (hardening) · ⚪ P3 (CI/DevOps)

---

## 🔴 P0 — Security Risk

### S7: Scraper TLS — Remove `check_hostname=False` and `CERT_NONE`

**File:** `app/utils/lookup.py`, lines 110–111

**What:** The KMC trade license scraper has `ctx.check_hostname = False` and `ctx.verify_mode = ssl.CERT_NONE`, which disables certificate verification entirely. The TLS probe showed KMC's certificate is valid (signed by Sectigo), so the root cause is an OpenSSL SECLEVEL mismatch, not a broken cert.

**Fix:** Remove lines 110–111 (`check_hostname=False`, `verify_mode=CERT_NONE`). Keep line 109 (`SECLEVEL=1`) — that's the actual working fix.

**Risk:** Without this fix, a MITM attacker could intercept KMC data.

---

## 🟡 P1 — Missing Feature

### S9a: Extend StaleDataError handling to remaining blueprints

**Files:** `app/inspection/routes.py` (PUT/DELETE), `app/sample/routes.py` (PUT/DELETE)

**What:** Optimistic locking (`version_id_col`) was added to `Adjudication`, `Bill`, and `CaseFile`, and `StaleDataError` handling was added to those route handlers. The `Inspection` and `Sample` models already **have** `version_id_col` and `__mapper_args__` (from a prior step?), or they don't — verify first.

**Check:**
- [ ] Do `Inspection` and `Sample` models already have `version_id_col`?
- [ ] If not, add `version_id` + `__mapper_args__`
- [ ] Add try/except `StaleDataError` around commits in PUT/DELETE routes
- [ ] Return 409 with user-friendly message on conflict

---

## 🟠 P2 — Hardening Completion

### S2: Enforce CSP (flip from report-only)

**File:** `app/__init__.py`, line 131

**Current:** `content_security_policy_report_only=True`

**What:** CSP is still in report-only mode. Before enforcing:
1. Deploy to production with report-only still on
2. Check `/csp-report` endpoint for any violation reports
3. If violations exist, add the offending domains to the allowlist
4. Flip `report_only=True` → `False` (or remove the parameter)

**Check:**
- [ ] Verify no violations in production CSP reports
- [ ] Add any missing domains to CSP allowlist
- [ ] Flip to enforcement

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

### S10a: Create Dependabot configuration

**File:** `.github/dependabot.yml` (does not exist)

**What:** Automated dependency vulnerability scanning. Create a Dependabot config targeting the `gis-implementation` branch, checking `pip` for `requirements.txt`.

**Template:**
```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    target-branch: "gis-implementation"
```

### S10b: Create pip-audit GitHub Action

**File:** `.github/workflows/pip-audit.yml` (does not exist)

**What:** Run `pip-audit` on every push to catch known vulnerabilities in dependencies.

**Template:**
```yaml
name: pip-audit
on: [push, pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pypa/gh-action-pip-audit@v1
        with:
          inputs: requirements.txt
```

### S10c: Database backup monitoring

**What:** Current Render free-tier PostgreSQL has automated daily backups (7-day retention on free plan), but there's no monitoring or alerting.

**Consider:**
- Add a health-check endpoint that reports last successful sync time
- Document the 90-day free-tier expiry in an operational runbook
- Set a calendar reminder 2 weeks before the 90-day expiry to upgrade the DB plan

---

## ✅ Completed Steps

| # | Step | Date |
|---|------|------|
| 1 | Secrets Migration (env vars, .gitignore, .env.example) | ✅ |
| 2 | HTTPS + Headers (Talisman, CSP report-only, ProxyFix) | ✅ (partial — CSP still report-only) |
| 3 | Session Cookies (Secure, HttpOnly, SameSite, 30min TTL) | ✅ |
| 4 | CSRF Protection (flask-wtf, tokens on all POST forms, AJAX) | ✅ |
| 5 | Authentication (flask-login, global before_request gate, werkzeug hashing) | ✅ |
| 6 | Output Sanitization (no |safe, no raw HTML for PDFs) | ✅ |
| 7 | Scraper TLS (diagnosis done, **fix not yet applied**) | ⏳ |
| 8 | Audit Logging (RecordAudit table, after_flush, login events) | ✅ |
| 9 | Optimistic Locking (version_id_col on Adjudication/Bill/CaseFile) | ✅ |
| 10 | Dependency + Backup (not started) | ❌ |
