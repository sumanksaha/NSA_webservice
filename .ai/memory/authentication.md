# Module Memory: Authentication & Authorization

## Purpose
Session-based authentication gate ensuring all routes require login except a
small public allow-list (auth login + lookup/autocomplete endpoints).

## Responsibilities
- Flask-Login session management; `login_manager` configured in
  `app/extensions.py` (`login_view = "auth.login"`).
- `user_loader` callback loads `User` by integer ID.
- Global `before_request` `require_login()` redirects unauthenticated users to
  `/auth/login?next=…`, skipping public endpoints.
- `User` model stores `password_hash` (no plaintext).

## Main Source Files
| File | Notes |
|------|-------|
| `app/auth/routes.py` | login (3 KB) |
| `app/auth/__init__.py` | `auth_bp` Blueprint |
| `app/extensions.py` | `login_manager` singleton |
| `app/models.py` | `User(UserMixin)` model |
| `scripts/create_user.py` | CLI to create users (hashed) |

## Public Interfaces
- `auth_bp` (prefix `/auth`): `auth.login`, `auth.logout`.
- Public endpoints allow-list in `app/__init__.py::public_endpoints`.

## Dependencies
Flask-Login, Werkzeug (password hashing), SQLAlchemy.

## Configuration Files
- `SECRET_KEY` (required on Render; local fallback with warning).
- `render.yaml` (SECRET_KEY sync:false).

## Known Issues
- RBAC not implemented — all authenticated users have full access.
- No password-reset / email flow.
- Single `User` table only (no roles/tables yet).

## Future Improvements
- RBAC: FSO / Admin / Auditor roles.
- Password reset flow + email.

## Current TODOs
- RBAC implementation (FSO, Admin, Auditor roles) — Phase 1 hardening.
