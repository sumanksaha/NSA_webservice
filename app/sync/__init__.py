"""Phase 17: Supabase Cloud Sync Bridge.

Provides distributed / offline-first synchronization of core legal-workflow
records (CaseFile, Adjudication, Bill, Sample, Inspection) to a Supabase
project.  The Supabase client is lazy-imported so the app boots without the
``supabase`` package installed.

Blueprint prefix: ``/sync``

Routes:
    GET  /sync/                          — sync-status dashboard (HTML)
    POST /sync/push                      — push local changes to Supabase
    POST /sync/pull                      — pull remote changes from Supabase
    GET  /sync/status                    — JSON sync-status probe
    POST /sync/resolve-conflict/<id>     — resolve a pending sync conflict
"""

from flask import Blueprint

sync_bp = Blueprint(
    "sync",
    __name__,
    url_prefix="/sync",
    template_folder="templates",
    static_folder="static",
)

# Import routes so decorators register.
from app.sync import routes  # noqa: F401
