# Module Memory: Backend / Application Core

## Purpose
Flask application factory, shared extensions, ORM models, and session-level audit
instrumentation — the foundation every blueprint and service builds on.

## Responsibilities
- Create and configure the Flask `App` (subclass of `Flask` with typed `celery` attr).
- Load env vars (`.env`), configure `SECRET_KEY`, DB URI (PG primary / SQLite fallback).
- Initialise security stack: Flask-Talisman (CSP/HSTS), CSRF, Flask-Login.
- Register all 10 blueprints under their URL prefixes.
- Enforce global login gate via `before_request` (public endpoints allow-list).
- Seed `db.session.info["audit_user_id"]` for audit hooks.
- Run FSO startup sync (thread-locked).
- Bootstrap Celery (graceful `None` fallback).

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/__init__.py` | ~12 KB | `create_app()` factory; `app = create_app()` at module load |
| `app/extensions.py` | 352 B | `db`, `csrf`, `talisman`, `login_manager` singletons |
| `app/models.py` | ~18 KB | All SQLAlchemy models; `CodeSequence` util |
| `app/audit_hooks.py` | ~6.6 KB | `after_flush` → `RecordAudit` |
| `app/audit/__init__.py` | 113 B | `audit_bp` Blueprint (name "audit") |
| `app.py` | 9 B | WSGI entry: `app = create_app()` |

## Public Interfaces
- `create_app()` → `App` instance.
- `app` (module-level, for `gunicorn app:app`).
- `celery` (module-level; `app.celery`).
- `App` class with `celery: Any` attribute.

## Dependencies
Flask, Flask-Login, Flask-Migrate, Flask-SQLAlchemy, Flask-Talisman,
Flask-WTF, SQLAlchemy, Werkzeug, python-dotenv, threading, pathlib, secrets.

## Configuration Files
- `.env.example` (env vars), `render.yaml` (deployment env),
  `pyproject.toml` (`[tool.mypy]`, `[tool.ruff]`, `[tool.black]`).
- `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `SPREADSHEET_ID`,
  `GOOGLE_CREDENTIALS_JSON`, `R2_*`, `SKIP_FSO_STARTUP_SYNC`, `PORT`.

## Known Issues
- `create_app()` called twice (once in `app/__init__.py` line 293, once in
  `app.py`): the second call re-runs FSO sync and re-registers blueprint —
  works in practice because it is idempotent, but is wasteful/redundant.
- All authenticated users have full access; no RBAC (see README roadmap).
- Celery import is gracefully degraded — tasks silently become no-ops if
  `app.celery is None`.

## Future Improvements
- Single canonical `create_app()` call path; remove double-instantiation.
- Add RBAC / role-based access control.
- FastAPI migration (target stack, levels 5-10).

## Current TODOs
- RBAC implementation (FSO, Admin, Auditor roles) — roadmap Phase 1.
- Health-check endpoints — roadmap Phase 2.
- Structured logging (structlog) + Sentry/Prometheus — roadmap Phase 2.
