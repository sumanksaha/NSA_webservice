# Module Memory: Deployment

## Purpose
Production hosting, configuration, and runtime orchestration for the Flask app
plus Celery workers, on Render (primary) with a Docker fallback defined in the
skeleton docs.

## Responsibilities
- Render Blueprint (`render.yaml`): one web service + managed Postgres DB.
- WSGI via Gunicorn (`app:app`); Alembic migrations run on boot.
- Celery worker for async PDF/verification tasks (separate process).
- Env-var driven config (no secrets in repo).

## Key Files
| File | Notes |
|------|-------|
| `render.yaml` | Render Blueprint (web + postgres:free) |
| `app.py` | WSGI entry |
| `celery_app.py` | Celery app factory |
| `.env.example` | All env vars documented |

## Runtime
- **Web:** `FLASK_APP=app:create_app flask db upgrade && gunicorn --bind 0.0.0.0:10000 app:app`
- **Worker:** `celery -A celery_app.celery worker --loglevel=info`
- **Port:** Render = 10000, local = 8000 (`PORT` env).

## Environment Variables
| Var | Required | Purpose |
|-----|----------|---------|
| `SECRET_KEY` | Yes (Render) | Flask session signing |
| `DATABASE_URL` | Yes | PostgreSQL connection |
| `REDIS_URL` | For Celery | Redis broker |
| `SPREADSHEET_ID` | For Sheets | Google Sheet ID |
| `GOOGLE_CREDENTIALS_JSON` | For Sheets | Service account JSON |
| `R2_ACCESS_KEY/SECRET_KEY/BUCKET/ENDPOINT` | For Storage | R2/B2 |
| `SKIP_FSO_STARTUP_SYNC` | No | Skip FSO markdown sync at boot |
| `DISABLE_PDF_GENERATION` | No | Skip WeasyPrint (no GTK) |
| `PDF_USE_DIRECT_URLS` | No | Embed URLs vs base64 in PDF |
| `PORT` | No | WSGI listen port |

## Known Issues
- Free-tier Render Postgres expires after 90 days (explicit warning in
  `render.yaml`).
- `app/__init__.py` instantiates `app` twice (factory + `app.py`); the
  double-call re-runs FSO sync — wasteful but idempotent.
- Celery gracefully degrades to `None` if import fails (no background tasks).

## Future Improvements
- Docker Compose (defined in skeleton doc) for local parity with prod.
- Separate Celery worker service on Render.
- TLS fix for KMC scraper.

## Current TODOs
- Persistent Celery worker deployment (Phase 1).
- Docker containerization (Phase 1).
- Connection pooling + index optimisation (Level 5).
