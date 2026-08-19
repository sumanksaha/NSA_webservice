#!/usr/bin/env sh
# ──────────────────────────────────────────────────────────────────────────────
# docker-entrypoint.sh — NSA Webservice container entrypoint
#
# Runs database migrations (idempotent) then execs the CMD.
# Migrations are non-blocking: if the DB is unreachable, the app still boots
# (Flask will surface a 500 on first DB-touching request, which is better
# than failing cold-start on a transient DB outage).
#
# Environment variables recognised:
#   DATABASE_URL  — PostgreSQL or SQLite URL (required for first-run)
#   SKIP_DB_MIGRATION — set to "1" to skip `flask db upgrade` (e.g. when
#                       mounting code volume in dev and migrations are handled
#                       externally)
# ──────────────────────────────────────────────────────────────────────────────
set -e

echo "[entrypoint] NSA Webservice container starting"
echo "[entrypoint]   USER:    $(whoami) (uid $(id -u))"
echo "[entrypoint]   PYTHON:  $(python --version 2>&1)"
echo "[entrypoint]   WORKDIR: $(pwd)"

# Skip migrations if explicitly requested (dev volume-mount scenario)
if [ "${SKIP_DB_MIGRATION:-0}" = "1" ]; then
    echo "[entrypoint] SKIP_DB_MIGRATION=1 — skipping database migrations"
else
    echo "[entrypoint] Running database migrations (flask db upgrade)..."
    # flask db upgrade is idempotent — safe to run on every container start.
    # Uses FLASK_APP=app:create_app (app.py at project root → create_app factory).
    flask db upgrade || {
        echo "[entrypoint] WARNING: flask db upgrade failed — continuing anyway"
        echo "[entrypoint]           The app will start; DB errors surface per-request."
    }
    echo "[entrypoint] Migrations complete."
fi

# Hand off to the CMD (gunicorn by default, celery for worker containers)
exec "$@"
