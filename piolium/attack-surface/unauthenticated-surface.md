# Unauthenticated Attack Surface

Reachable by an anonymous attacker — no valid session, token, or API key.

**Coverage**: 2 entry points | 2 by-design public | 0 missing-guard / middleware-gap
**Auth model**: Flask-Login session-based authentication via requireAuth middleware (app/__init__.py:111-116)
**Coverage gaps**: none detected

## Pre-Auth HTTP / API Routes

| # | Method | Path | Handler (file:line) | Why pre-auth | Notable inputs / sinks | Blast radius |
|---|--------|------|---------------------|--------------|------------------------|--------------|
| 1 | GET | /auth/login | app/auth/routes.py:auth.login | by-design | username, password (form) | Login page, credential validation |
| 2 | POST | /auth/login | app/auth/routes.py:auth.login | by-design | username, password (form) | Authentication, session creation |
| 3 | POST | /auth/logout | app/auth/routes.py:auth.logout | by-design | session | Session termination |
| 4 | GET | /static/<path:filename> | Flask static | by-design | filename (path) | Static asset serving |

## Other Unauthenticated Entry Points

Non-route surface reachable without auth — include only kinds that apply: webhook / OAuth / payment callback, health / metrics / debug endpoint, GraphQL introspection, WebSocket pre-handshake handler, static / file server, unauthenticated queue / topic consumer, file-upload endpoint, SSRF-reachable fetcher, server-to-server endpoint trusting only a network position or shared secret.

| Kind | Entry point (file:line) | Why pre-auth | Notes |
|------|-------------------------|--------------|-------|

## Analysis Notes

### Public Endpoints (by-design)

The application explicitly defines public endpoints in `app/__init__.py:109`:

```python
PUBLIC_ENDPOINTS = {
    "auth.login",
    "static",
}
```

- **auth.login**: Login page accessible to unauthenticated users for credential entry
- **static**: Static file serving for CSS, JS, images

### Authentication Guard Implementation

The `require_login` before_request handler (lines 111-116) enforces authentication:

```python
@app.before_request
def require_login():
    if request.endpoint and request.endpoint not in PUBLIC_ENDPOINTS:
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.url))
```

This means:
- All routes NOT in PUBLIC_ENDPOINTS require authentication
- Unauthenticated requests to protected routes are redirected to login
- The `next` parameter preserves the original URL for post-login redirect

### No Missing Guards Detected

All route handlers are properly protected by the global before_request handler. The following blueprints are NOT publicly accessible:
- `/adjudication/*` - Adjudication workflows
- `/case_file_generator/*` - Case file generation
- `/bill_generator/*` - Bill PDF generation
- `/billing/*` - Billing operations
- `/inspection/*` - Inspection management
- `/sample/*` - Sample tracking
- `/settings/*` - Settings (admin)
- `/admin/*` - Audit logs

### Potential Concerns

1. **Static File Serving**: The `/static/` endpoint serves user-uploaded content. While not directly exploitable without auth, it could serve cached sensitive files if misconfigured.

2. **Password Reset Flow**: No password reset functionality is implemented. If added in the future, it would need careful consideration to prevent unauthorized account takeover.

3. **API Key Authentication**: Currently not implemented. All API endpoints use session-based auth.

### Attack Surface Summary

| Category | Count |
|----------|-------|
| Pre-auth routes | 4 |
| By-design public | 2 |
| Missing-guard candidates | 0 |
| Middleware-gap candidates | 0 |

The unauthenticated attack surface is minimal and well-controlled by the application's authentication architecture.
