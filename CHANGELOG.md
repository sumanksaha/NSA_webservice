# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

#### TLS Certificate Verification Fix for KMC Lookup

- **Fixed**: Removed insecure SSL settings (`check_hostname=False`, `verify_mode=CERT_NONE`) in KMC CE license lookup
- **Impact**: Prevents MITM attacks on government portal data scraping
- **Files**: `app/utils/lookup.py`
- **Risk Level**: P0 (Critical)

### Added

- Authentication system (Flask-Login)
- Authorization middleware (global login gate)
- CSRF protection (Flask-WTF)
- Security headers (Flask-Talisman: CSP, HSTS, X-Frame-Options)
- Session hardening (Secure, HttpOnly, SameSite cookies, 30-minute TTL)
- Audit logging system (RecordAudit model, SQLAlchemy event hooks)
- Optimistic locking (version_id column on Adjudication, Bill, CaseFile)
- User management (User model, login/logout routes)
- Audit module (`/admin` blueprint)
- Celery integration (`celery_app.py`)
- Dependency scanning (Dependabot configuration)
- Security audit CI (pip-audit GitHub Action)
- Security documentation (SECURITY.md, CODE_OF_CONDUCT.md)
- Development documentation (CONTRIBUTING.md)
- License file (MIT License)

### Changed

- CSP enforcement (from report-only to enforcement mode)
- Session cookie security settings
- Added before_request hooks for authentication and audit

## [1.0.0] - Initial Release

### Added

- Modular Flask blueprints for:
  - Inspection module
  - Sample module
  - Adjudication module
  - Case file generator
  - Bill generator
  - Billing module
  - FBO issue management
  - Settings
- OCR pipeline with Celery
- Photo evidence system with geo-verification
- Google Sheets integration
- State machine for FBO issues
- Canonical key system for cross-module consistency
- Derived context helpers
- Alembic database migrations
- Comprehensive test suite

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 1.0.0 | 2026-01-01 | Initial release |
| 1.0.1 | 2026-07-26 | Security updates (authentication, CSRF, CSP, TLS fix) |

---

For older versions, see the git history.
