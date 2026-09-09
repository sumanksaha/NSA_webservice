# NSA Webservice — AI Module Memory (Consolidated)

> From 20 `.ai/memory/*.md` files: adjudication, api, audit, authentication, backend, billing, case_file, database, deployment, document_cleaner, document_loader, fbo_issue, inspection, legal_engine, metadata_extractor, ocr, sample, services, shared, testing.

---

## 1. Core Application Architecture (api.md / backend.md / deployment.md)

- Flask WSGI (`app:app`) with 10 blueprints (`/auth`, `/inspection`, `/sample`, `/case_file_generator`, `/adjudication`, `/bill_generator`, `/billing`, `/fbo-issue`, `/settings`, `/admin`) — all server-rendered HTML via Jinja2, no REST JSON yet (planned FastAPI).
- `create_app()` factory (12 KB, `app/__init__.py`); `extensions.py` for `db`, `csrf`, `talisman`, `login_manager`; session-based auth with global `before_request` gate + public allow-list (`public_endpoints`).
- Render blueprint (`render.yaml`): managed Postgres + Gunicorn (`0.0.0.0:10000`), Celery worker for async PDF/verification tasks; env-var driven config (`SECRET_KEY`, `DATABASE_URL`).
- WSL2 Ubuntu 26.04 dev stack; PostgreSQL/Redis native; Docker redundant.
- Audit (audit.md): hash-chained `AuditLog` (SHA-256) + session-level `RecordAudit` (JSON diff) via `after_flush` on `Adjudication`/`Bill`/`CaseFile`; `/admin/audit-log` read-only viewer; `FboIssueAudit` state-transition log.

## 2. Data Layer (database.md)

- SQLAlchemy ORM (`app/models.py`, ~18 KB): 13 tables (user, case_files, adjudications, inspection, fbo_issue, sample, audit, etc.). Alembic migrations (14 scripts). Race-safe `CodeSequence` (PG advisory locks / SQLite retry). Optimistic concurrency (`version_id` on case/bill/adjudication). SQLite fallback (`instance/app.db`).

## 3. Domain Modules (inspection.md / sample.md / adjudication.md / fbo_issue.md / case_file.md / billing.md)

- **Inspection:** `inspection_bp` (38 KB routes) — FSO, FBO identity, photo evidence upload (R2/B2), geo/IP/distance verification, 15-day compliance deadline, `adjudication_id` FK, dismiss action.
- **Sample:** `sample_bp` (13 KB routes) — auto `sample_code` (`CodeSequence`), retailer FSSAI/name, collection→submission→lab chain, `lookup_retailer` (PUBLIC).
- **Adjudication:** `adjudication_bp` (28 KB) — section selection (55/56/58/63/64), checklist violations, KMC/CE lookup (PUBLIC), `version_id` OCC, links to `Inspection`/`CaseFile`.
- **FBO Issue:** `fbo_issue_bp` (12 KB) — state machine (`open → permission_pending → granted → closed` / `dismissed`), DB constraints prevent invalid states, `FboIssueAudit` transition tracking.
- **Case File:** `case_file_generator_bp` (20 KB) — sample-based violations, PDF via WeasyPrint + Celery, `applicable_sections` (51/52) derived via `context_derivers.py`, embedded photo base64/URL.
- **Billing:** `billing_bp` (3 KB dashboard + 8 KB Excel export via openpyxl) + `bill_generator_bp` (9 KB form + Celery PDF) — per-FSO counts/prices.

## 4. Document Pipeline (document_loader.md / document_cleaner.md / ocr.md / metadata_extractor.md / legal_engine.md)

- Loader: abstract `DocumentLoader` + PDF (`pdfplumber`/PyMuPDF) / DOCX (`python-docx`) / TXT (`chardet`) → `LoadedDocument`. Batch orchestration (`batch.py`).
- Cleaner: boilerplate removal (`removers.py`), whitespace/unicode normalization (`normalizers.py`), diffs (`differ.py`), pipeline orchestration + stats.
- OCR: `ocr_pipeline/` — detection (`detectors.py`), preprocessing (`preprocessing.py`), `pytesseract` wrapper (`ocr_engine.py`), routing (`decision.py`), batch (`batch.py`).
- Metadata extractor (`metadata_extractor/`): 13 KB `regex_library.py` (FSSAI/phone/address/date rules) + lightweight regex-based NER (`ner.py`) + `engine.py` orchestrator + `validation.py` (python-stdnum/FSSAI format) + `confidence.py` scoring.
- Legal engine (`legal_paragraph_detection_engine/`): standalone thread-safe parser — FAST/ACCURATE/COMPREHENSIVE modes, section/clause/citation extraction, hierarchical JSON export, `threading.RLock` + instance cache.

## 5. Shared Contracts (shared.md / services.md / testing.md)

- `case_keys.py` (16 KB): canonical field-name contract + `TypedDict` shapes + OLD→NEW mappings per module (inspection, sample, adjudication, case_file) + section/date disambiguation rules.
- `context_derivers.py` (15 KB): pure derivation functions (`applicable_sections`, `sections_display`, `case_track`, `violations`, `same_entity`) — no side effects.
- Services: `sheets_sync.py` (Google Sheets), `fso_data.py` (markdown→DB sync, thread-locked startup), `storage.py` (lazy boto3 for R2/B2), `lookup.py` (FSSAI/CE lookup helpers), `suggester.py` (section heuristics), `filters.py` (Jinja), `pdf_utils.py`.
- Testing: `tests/test_step{1-5}_integration.py` (11 files, ~200 KB total), route-collision guard (`test_route_collisions.py`), legal-engine separate suite (9 pytest modules, pytest-9.1.1). ~1,970 total tests.

---
