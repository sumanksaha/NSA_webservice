# Module Memory: Database & Models

## Purpose
Persistence layer: SQLAlchemy ORM models, Alembic migrations, race-safe code
generation, and optimistic-concurrency control.

## Responsibilities
- Define 13 domain tables as SQLAlchemy models in `app/models.py`.
- PostgreSQL primary / SQLite fallback (`instance/app.db`).
- Alembic schema versioning under `migrations/versions/`.
- `CodeSequence` atomic counters (PG advisory locks / SQLite retry) for unique
  codes (inspection_code, sample_code, case_number).
- Optimistic locking via `version_id` columns on CaseFile/Bill/Adjudication.
- `db.create_all()` fallback on startup if `fso` table missing (fresh DB).

## Main Source Files
| File | Notes |
|------|-------|
| `app/models.py` | All models; `CodeSequence` utility class |
| `migrations/alembic.ini` | Alembic config (generated) |
| `migrations/env.py` | Migration env wiring (uses Flask app config) |
| `migrations/versions/*.py` | 14 migration scripts (baseline + features) |

## Models / Tables (schema summary)
| Table | Purpose | Key cols |
|-------|---------|----------|
| `user` | Auth users | id, username, password_hash |
| `case_files` | Sample-based case files | case_number, version_id (OCC), pdf_task_id |
| `adjudications` | Non-sample cases | case_number, section_55..64 flags, version_id, photos |
| `inspection` | Inspection visits | inspection_code, fso_name, compliance_deadline, adjudication_id |
| `sample` | Sample tracking | sample_code (unique), fso_name, billed |
| `bills` | Officer billing | EMP_ID, totals, version_id |
| `bill_sample` | Join: bills ↔ samples | (bill_id, sample_id) |
| `fso` | FSO master list | fso_name (PK) — seeded from `fso_list.md` |
| `fbo_issue` | Issue state machine | fbo_id, source_type, state; check constraints |
| `fbo_issue_audit` | State-transition log | issue_id, from_state, to_state |
| `inspection_photos` | Inspection photos | adjudication_id FK, indexed |
| `photo_evidence` | Verified photo metadata | image_id, case/inspection FK, geo, status |
| `audit_log` | Hash-chained tamper-evident log | prev_hash, curr_hash |
| `record_audit` | CRUD + login audit (after_flush) | record_type, record_id, changes (JSON) |
| `code_sequence` | Race-safe counters | key (PK), last_value |

## Public Interfaces
- `app.extensions.db` — SQLAlchemy instance (singletons).
- `CodeSequence.next_value(key, increment)` / `.current_value(key)`.
- `sync_fso_from_markdown()` → seeds `fso` table.

## Dependencies
SQLAlchemy, Flask-SQLAlchemy, psycopg2-binary, Alembic.

## Configuration Files
- `DATABASE_URL` env (PG primary; sqlite:/// fallback) — normalised
  `postgres://`→`postgresql://`.
- `pyproject.toml` `[tool.coverage.run]` omit patterns.
- `migrations/alembic.ini`, `render.yaml` (databaseName: nsa_db, user: nsa_user).

## Known Issues
- DB migrations must run before FSO startup sync; `SKIP_FSO_STARTUP_SYNC=1`
  allows fresh-DB seeding workflow.
- Free-tier Render Postgres expires after 90 days (see render.yaml comment).
- Some root scripts (`check_db_schema.py`, `verify_schema.py`,
  `check_tables.py`) are schema-inspection helpers.

## Future Improvements
- Connection pooling, index optimisation, N+1 fixes (Level 5).
- Full PostgreSQL production cutover.

## Current TODOs
- PostgreSQL production migration (in progress).
- End-to-end migration/seed validation.
