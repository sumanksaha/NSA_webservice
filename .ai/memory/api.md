# Module Memory: API / Web Layer (Flask App & Blueprints)

## Purpose
Single-process Flask web application exposing server-rendered HTML UIs for every
legal-adjudication domain. No public JSON/REST API exists yet (planned via FastAPI).

## Responsibilities
- WSGI entry point (`app:app`).
- Per-domain blueprint routing with URL prefixes.
- Server-side rendering with Jinja2 templates + shared CSS.
- Global authentication gate + per-endpoint public-allowlist.
- Consistent JSON-style error responses in routes (status codes: 200/201/204/400/404/409/500).

## Blueprints (URL prefix → module)
| Prefix | Module | Routes file (KB) |
|--------|--------|------------------|
| `/auth`             | auth             | routes.py (3) |
| `/inspection`       | inspection       | routes.py (38) |
| `/sample`           | sample           | routes.py (13) |
| `/case_file_generator` | case_file_generator | routes.py (20) |
| `/adjudication`     | adjudication     | routes.py (28) |
| `/bill_generator`   | bill_generator   | routes.py (9) |
| `/billing`          | billing          | routes.py (3) + billing_utils.py (8) |
| `/fbo-issue`        | fbo_issue        | routes.py (12) |
| `/settings`         | settings         | routes.py (1) |
| `/admin`            | audit            | routes.py (1) |

## Public Interfaces
- All routes are HTTP endpoints rendering HTML templates.
- Lookup endpoints (autofill/datalist) are PUBLIC:
  `case_file_generator.lookup_sample`,
  `case_file_generator.list_samples_for_datalist`,
  `adjudication.lookup_ce_route` / `.lookup_fssai_route`,
  `inspection.lookup_ce_route` / `.lookup_fssai_route`,
  `sample.lookup_retailer`,
  `bill_generator.lookup_fbo_issues`,
  `adjudication.lookup_fbo_issues`.
- Root `/` → redirects to `case_file_generator.index`.

## Dependencies
Flask, Jinja2, Flask-Login, Flask-WTF (forms), SQLAlchemy models.

## Configuration Files
Blueprint registration lives in `app/__init__.py`. Templates in
`app/<module>/templates/<module>/`; shared base at `app/templates/base.html`.
Static assets at `app/static/css/theme.css`.

## Known Issues
- Routes are server-rendered only; a programmatic API does not yet exist.
- `inspection/routes.py` (38 KB) is the largest route file — heavy inline logic.

## Future Improvements
- FastAPI migration (OpenAPI/Swagger, async handlers, DI).
- Add structured JSON API for integration with frontend/RAG agents.
- Health-check + readiness endpoints.

## Current TODOs
- OpenAPI/Swagger documentation (Phase 2).
- Dependency injection (Phase 2).
- Async request handling (Phase 2).
