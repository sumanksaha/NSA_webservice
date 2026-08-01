# PROJECT_MEMORY.md — NSA Webservice

## Project Overview

**NSA Webservice** (`nsa-webservice`, v0.8.0) is a government-grade, digitisation
and workflow-automation platform for the **Food Safety and Standards Act, 2006
(FSS Act)**. It replaces paper-based legal proceedings of Food Safety Officers
(FSOs) with an auditable digital platform covering inspection tracking, sample
management, legal case-file generation, non-sample adjudication, FBO issue
tracking, billing, and tamper-evident audit logging.

The repository contains **two** independent but related codebases:

1. **Main Flask application** (`app/` package + `app.py` + root scripts) — the
   primary web application with blueprints, SQLAlchemy models, Alembic migrations,
   Celery background tasks, WeasyPrint PDF generation, R2/B2 photo storage, and
   Google Sheets sync.
2. **Legal Paragraph Detection Engine** (`legal_paragraph_detection_engine/`) —
   a standalone, thread-safe legal-document parser that extracts structured
   paragraphs, clauses, sections, citations, and hierarchical relationships from
   legal text. It has its own `src/` layout, unit/integration tests, examples,
   and benchmarks, and can be used both standalone and as a library.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│ PRESENTATION      Jinja2 templates · CSS (Vanilla JS)    │
│   (Flask blueprints per domain)                          │
├─────────────────────────────────────────────────────────┤
│ APPLICATION       app/__init__.py (create_app factory)    │
│   - global auth gate (before_request)                    │
│   - FSO startup sync (thread-locked)                     │
│   - audit hook registration                              │
│   - blueprint registration                               │
├─────────────────────────────────────────────────────────┤
│ SERVICE LAYER     app/services/  app/utils/              │
│   sheets_sync, storage, audit, suggester, lookup         │
├─────────────────────────────────────────────────────────┤
│ DATA LAYER        PostgreSQL (primary) / SQLite (dev)   │
│   SQLAlchemy ORM + Alembic, Redis broker for Celery      │
└─────────────────────────────────────────────────────────┘
```

**Key design decisions:**
- **Blueprint-per-domain** — one Flask blueprint per functional domain enables
  independent development and a future microservice migration.
- **Canonical Key Contract** — `app/shared/case_keys.py` defines uniform field
  names across modules, preventing field-name drift.
- **Hash-chained audit** — tamper-evident `AuditLog` (SHA-256) + session-level
  `RecordAudit` via SQLAlchemy `after_flush` hooks for Adjudication/Bill/CaseFile.
- **Race-safe sequences** — `CodeSequence` table with atomic increments and
  PostgreSQL advisory locks for unique code generation.
- **Optimistic concurrency** — `version_id` columns with `StaleDataError`
  handling on CaseFile/Adjudication/Bill.
- **Storage abstraction** — S3-compatible interface (R2/B2) for photo evidence.

## Folder Structure

```
NSA_webservice/
├── app/                      # Application package (typed, py.typed)
│   ├── __init__.py           # create_app() factory
│   ├── extensions.py         # db, csrf, talisman, login_manager singletons
│   ├── models.py             # SQLAlchemy ORM models + CodeSequence util
│   ├── audit_hooks.py        # after_flush -> RecordAudit
│   ├── adjudication/         # blueprint  (/adjudication)
│   ├── audit/                # blueprint  (/admin/audit-log)
│   ├── auth/                 # blueprint  (/auth)
│   ├── billing/              # blueprint  (/billing)
│   ├── bill_generator/       # blueprint  (/bill_generator) + Celery tasks
│   ├── case_file_generator/  # blueprint  (/case_file_generator) + tasks
│   ├── fbo_issue/            # blueprint  (/fbo-issue)
│   ├── inspection/           # blueprint  (/inspection) + photo verification
│   ├── sample/               # blueprint  (/sample)
│   ├── services/             # sheets_sync, audit service
│   ├── settings/             # blueprint  (/settings)
│   ├── shared/               # case_keys (canonical keys), context_derivers
│   ├── static/               # CSS
│   ├── templates/            # base.html
│   ├── utils/                # fso_data, sync, storage, lookup, filters...
│   ├── document_cleaner/     # text cleaning pipeline (removers/normalizers)
│   ├── document_loader/      # PDF/DOCX/TXT loaders (pdfplumber/pymupdf)
│   ├── ocr_pipeline/         # OCR (pytesseract), detectors, preprocessing
│   └── metadata_extractor/   # NER, regex_library, validation, confidence
├── legal_paragraph_detection_engine/   # Standalone legal parsing engine
│   ├── src/  (legal_engine, core, parsers, storage, utils)
│   ├── tests/, examples/, benchmarks/, config/
│   └── LEGAL_ENGINE_ANALYSIS_TODO.md (empty)
├── migrations/               # Alembic (alembic.ini, env.py, versions/)
├── tests/                    # pytest suite (step1-5, route collisions)
├── scripts/                  # utility scripts (create_user, dedup, reports)
├── docs/                     # DOCUMENT_LOADER_PERFORMANCE.md, LINE_ENDINGS...
├── instance/                 # runtime SQLite DB (gitignored), credentials.json
├── celery_app.py             # Celery app factory (lazy, avoids circular import)
├── app.py                    # WSGI entry point (app = create_app())
├── render.yaml               # Render Blueprint deploy
├── pyproject.toml            # build + tool config (black/ruff/mypy/pytest/coverage/bandit)
├── requirements.txt          # -> -e .
├── requirements-dev.txt      # pytest/black/ruff/mypy/bandit/pip-audit/...
├── .env.example              # environment variable reference
├── AST_SKELETONIZATION.md    # existing project reference doc
├── POSTGRES_MIGRATION.md
├── ENGINEERING_ASSESSMENT.md
└── DOCUMENT_VIEWER_IMPLEMENTATION_PLAN.md
```

## Major Modules / Blueprints

| Blueprint (`_bp`)        | URL prefix            | Responsibility |
|--------------------------|-----------------------|----------------|
| `auth_bp`                | `/auth`               | Login/logout (Flask-Login), password hashing |
| `inspection_bp`          | `/inspection`         | Inspection CRUD, photo evidence, geo/IP verification pipeline |
| `sample_bp`              | `/sample`             | Sample collection, code gen, lab tracking |
| `case_file_generator_bp` | `/case_file_generator`| Case-file form, WeasyPrint PDF generation (Celery) |
| `adjudication_bp`        | `/adjudication`       | Non-sample cases, section selection, document gen |
| `bill_generator_bp`       | `/bill_generator`      | Bill PDF generation (Celery) |
| `billing_bp`             | `/billing`            | Billing dashboard, Excel export |
| `fbo_issue_bp`           | `/fbo-issue`          | FBO issue state machine open→permission_pending→granted→closed |
| `settings_bp`            | `/settings`           | Admin settings |
| `audit_bp`               | `/admin`              | Read-only audit-log viewer |

Core non-blueprint subsystems: `document_cleaner`, `document_loader`,
`ocr_pipeline`, `metadata_extractor`, `services` (sheets_sync, audit),
`shared` (case_keys, context_derivers), `utils`.

## Public APIs

All blueprint routes are server-rendered (Jinja2 → HTML). There is **no
public REST/JSON API** surface yet (planned: FastAPI migration, OpenAPI).
Endpoints are registered under `/auth`, `/inspection`, `/sample`,
`/case_file_generator`, `/adjudication`, `/bill_generator`, `/billing`,
`/fbo-issue`, `/settings`, `/admin`.

Celery tasks (async):
- `app.case_file_generator.tasks` — PDF case-file generation
- `app.bill_generator.tasks` — PDF bill generation
- `app.inspection.tasks` — photo-evidence verification pipeline
- `app.inspection.verification_service` — geo/IP/distance/photo verification

## Database Schema Summary

**Primary DB:** PostgreSQL (production via Render) with SQLite fallback
(`instance/app.db`) for local development. SQLAlchemy ORM; Alembic for migrations.

Key tables (see `app/models.py`):
- `user` — auth users (password_hash)
- `case_files` — sample-based case files (v_id for optimistic locking)
- `adjudications` — non-sample adjudication cases (v_id)
- `bills` — officer billing records (v_id); `bill_sample` join table
- `fbo_issue` / `fbo_issue_audit` — issue state machine + audit trail
- `fso` — Food Safety Officer master list (seeded from `fso_list.md`)
- `sample` — sample tracking (unique sample_code)
- `inspection` — inspection visits (adjudication_id FK)
- `inspection_photos` / `photo_evidence` — photo metadata + verification status
- `audit_log` — hash-chained tamper-evident log
- `record_audit` — CRUD + login audit trail
- `code_sequence` — race-safe counters (advisory locks on PG)

## Coding Conventions

- **Style:** Black, line-length 120; Ruff lint (pyflakes/pycodestyle/isort/pep8-naming/pyupgrade/bandit/bugbear/simplify/flake8-print/ruf).
- **Types:** Python 3.12; mypy present (non-strict, `ignore_missing_imports=true`).
- **Conventions:** snake_case modules/functions/variables; PascalCase classes;
  UPPER_CASE constants; Google-style docstrings; `from __future__ import annotations`.
- **Imports:** stdlib → third-party → local (first-party `app`), sorted (isort via ruff).

## Testing Conventions

- **Framework:** pytest (config in `pyproject.toml` → `tool.pytest.ini_options`).
- **Test paths:** `tests/` (canonical). Root-level `test_*.py` scripts also exist.
- **Legal engine:** `legal_paragraph_detection_engine/tests/unit/` (pytest-9.1.1).
- `addopts = "-v --tb=short --no-header"`; coverage configured (`tool.coverage`).
- `test_route_collisions.py` — regression guard for duplicate routes.

## Build Instructions (Local Dev)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt        # -> -e . (editable)
pip install -r requirements-dev.txt    # lint/test tools
cp .env.example .env                   # edit: SECRET_KEY, DATABASE_URL...
flask db upgrade                       # Alembic migrations
flask run                              # dev server on :8000
# lint
black --check . && ruff check .
# tests
pytest
```

**Render deploy (prod):** `render.yaml` — `FLASK_APP=app:create_app flask db upgrade && gunicorn --bind 0.0.0.0:10000 app:app`.

## Deployment Overview

- **Host:** Render (free-tier Postgres warning noted in `render.yaml` — expires 90 days).
- **WSGI:** Gunicorn (`app:app`).
- **Async:** Celery worker (`-A celery_app.celery`) + Redis broker. Local dev Celery can be `None` (graceful import handling).
- **Storage:** Cloudflare R2 / Backblaze B2 (boto3), lazy singleton client.
- **Security:** Flask-Talisman (CSP/HSTS/Secure cookies), Flask-WTF CSRF, ProxyFix (Render TLS termination), session hardening (30 min TTL).

## External Dependencies

Runtime (key): Flask 3.x, Flask-SQLAlchemy, Flask-Migrate, Flask-Login,
Flask-Talisman, Flask-WTF, SQLAlchemy 2.x, Alembic, psycopg2-binary,
Redis, Celery, gspread, google-auth, WeasyPrint, openpyxl, boto3,
python-dotenv, Pillow, pytesseract, pdf2image, pdfplumber, PyMuPDF (fitz),
python-docx, pydantic, orjson, chardet, tqdm, num2words, httpx, gunicorn,
python-multipart.

Dev: black, ruff, mypy, bandit, pre-commit, pip-audit, safety, pytest(-cov/-xdist/-flask),
vulture, py-spy, type stubs (dateutil/requests/PyYAML).

## Important Design Decisions

- `app/__init__.py` sets `app = create_app()` AND `celery = app.celery` at module
  load — `app.py` is the canonical WSGI entry for Gunicorn.
- Celery is lazily wired (`try/except ImportError`) so deployments without Celery
  start successfully (`app.celery = None`).
- `app/shared/case_keys.py` + `app/shared/context_derivers.py` — canonical
  field-name contract across the 4 UIs (Inspection/Sample/CaseFile/Adjudication)
  with date disambiguation rules (see file docstring).
- `db.session.info["audit_user_id"]` is set in a `before_request` handler so
  audit hooks work without request-context dependency.
- FSO master data is synced from `fso_list.md` at app startup (thread-locked);
  skippable via `SKIP_FSO_STARTUP_SYNC=1`.

## Known Limitations

- No end-to-end test suite yet (module-specific tests, no full flow).
- RBAC not implemented — all authenticated users have full access.
- PostgreSQL production migration is "in progress" (schema ready).
- `legal_paragraph_detection_engine/LEGAL_ENGINE_ANALYSIS_TODO.md` is empty.
- Some root-level ad-hoc scripts (`check_*.py`, `fuzzy_dedup_stage0.py`,
  `filter_house_number.py`, `sections_data.py`) are analysis/migration helpers,
  not part of the core app.
- `instance/` (SQLite DB + `credentials.json`) is gitignored but present locally.

## Repository Navigation Guide

- **Start here:** `app/__init__.py` (app factory) → `app/models.py` (data) →
  `app/extensions.py` (shared singletons).
- **Business routes:** `app/<module>/routes.py` (one per blueprint).
- **Async tasks:** `app/<module>/tasks.py` (case_file_generator, bill_generator,
  inspection).
- **Legal engine:** `legal_paragraph_detection_engine/src/legal_engine.py`
  (entry) → `src/core/` (paragraph/hierarchy) → `src/parsers/` (clause/section)
  → `src/storage/` (citation/exporter).
- **Migrations:** `migrations/versions/*.py`.
- **Tests:** `tests/test_step1.py`–`test_step5_integration.py` + per-module.
- **Config:** `pyproject.toml` (all tool config), `.env.example` (env vars),
  `render.yaml` (deploy).
- **Reference data:** `fso_list.md` (FSO names), `fss_sections.md` (FSS Act
  sections) — consumed by `app/utils/sections_data.py` & `fso_data.py`.
