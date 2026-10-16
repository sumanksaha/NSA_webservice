import json
import logging
import os
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user

from app.extensions import csrf, db

health_bp = Blueprint("health", __name__)

# Module-scoped logger — deliberately NOT ``current_app.logger``: the shared
# "app" logger gets silenced in some embedding contexts (e.g. pytest sessions),
# and CSP reports must always reach the configured root handler.
logger = logging.getLogger(__name__)


@health_bp.route("/health")
def health():
    """Lightweight liveness/readiness probe used by the Docker HEALTHCHECK.

    Returns 200 when the database is reachable, 503 otherwise. Intentionally
    public (no auth) so orchestrators and load balancers can probe it.
    """
    db_status = "unavailable"
    if db.engine is not None:
        try:
            from sqlalchemy import text

            with db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_status = "connected"
        except Exception:
            db_status = "unavailable"

    try:
        import resource

        mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        memory_mb = round(mem_kb / 1024, 1)
    except (ImportError, AttributeError):
        memory_mb = None

    # WeasyPrint availability — the PDF-generation pipeline depends on it and
    # its import can fail on hosts missing the Pango system libraries. Surfacing
    # it here makes the PDF engine's health checkable via a public GET.
    weasyprint = "unavailable"
    try:
        import weasyprint

        weasyprint = f"ok ({weasyprint.__version__})"
    except Exception as exc:
        weasyprint = f"unavailable: {exc!s}"

    # QStash status — reports whether the serverless task-queue webhook
    # can verify incoming signatures. When keys are absent the webhook
    # returns 503; surfacing this here makes the gap visible via /health.
    # ``qstash_missing`` names the exact blank vars so an operator can fix
    # the Render env in one pass instead of guessing which of the four is off.
    try:
        from app.utils.qstash_client import missing_qstash_vars

        _missing = missing_qstash_vars()
        qstash = "not-configured" if _missing else "configured"
    except Exception:
        _missing = []
        qstash = "not-configured"

    payload = {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "authenticated": current_user.is_authenticated if current_user else False,
        "memory_mb": memory_mb,
        "weasyprint": weasyprint,
        "qstash": qstash,
        "qstash_missing": _missing,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    code = 200 if db_status == "connected" else 503
    return jsonify(payload), code


@health_bp.route("/health/cloudinary")
def cloudinary():
    """Cloudinary backend probe (Priority 5 — Testing & Hardening).

    Public + always **200**: orchestrators must be able to distinguish
    ``not-configured`` from ``configured-but-unreachable`` without either
    being treated as a service outage.  Reports the resolved credential
    source, whether the SDK is installed, and a live API reachability probe
    (``api.ping()``) — the latter only when credentials exist.
    """
    from app.utils import storage

    creds = storage._cloudinary_credentials()
    configured = creds is not None
    if configured:
        url_value = os.environ.get("CLOUDINARY_URL", "")
        credential_source = "cloudinary_url" if storage._parse_cloudinary_url(url_value) else "discrete"
    else:
        credential_source = "none"

    api_reachable = None
    api_error = None
    if configured:
        cld = storage._get_cloudinary()
        if cld is None:
            api_reachable = False
            api_error = "Cloudinary SDK not installed"
        else:
            try:
                cld.api.ping()
                api_reachable = True
            except Exception as exc:
                api_reachable = False
                api_error = str(exc)

    try:
        import cloudinary  # noqa: F401

        sdk = "installed"
    except ImportError:
        sdk = "missing"

    payload = {
        "status": "ok",
        "configured": configured,
        "credential_source": credential_source,
        "cloud_name": (creds or {}).get("cloud_name"),
        "sdk": sdk,
        "api_reachable": api_reachable,
        "api_error": api_error,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return jsonify(payload), 200


@health_bp.route("/health/backups")
def backups():
    """Backup dead-man's-switch (S10c — Database backup monitoring).

    Public so external uptime monitors can alert on it. Returns 200 while
    the last redundant backup is fresh and every target succeeded, and 503
    when no backup was ever recorded, the last run is stale, or a target
    failed — giving free-tier deployments an alerting path without extra
    infrastructure.
    """
    from app.services.backup_coordinator import last_backup_status

    status = last_backup_status()
    payload = {**status, "freshness_threshold_hours": 26, "timestamp": datetime.now(UTC).isoformat()}
    code = 200 if status["status"] == "ok" else 503
    return jsonify(payload), code


@csrf.exempt
@health_bp.route("/csp-report", methods=["POST"])
def csp_report():
    """Receive browser CSP violation reports (S2 residual).

    Browsers POST these fire-and-forget when a page violates the enforced
    policy configured in ``create_app()`` (``report_uri="/csp-report"``).
    The body uses non-JSON content types (``application/csp-report`` or
    ``application/reports+json``) and carries no CSRF token, so this route
    is CSRF-exempt and public by design; only a bounded slice of the body
    is read and everything is logged, never trusted.

    Always answers 204 — a violation collector must never become another
    error surface for the browser console.
    """
    raw = request.get_data(cache=False)[:4096]  # bound memory from hostile bodies
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        payload = {}

    report = payload.get("csp-report") or payload.get("csp-violation-report") or {}
    brief = {
        key: report.get(key)
        for key in (
            "document-uri",
            "violated-directive",
            "effective-directive",
            "blocked-uri",
            "source-file",
            "line-number",
        )
        if report.get(key)
    }
    logger.warning("CSP violation reported: %s", brief or "(unparseable report)")
    return "", 204


# ---------------------------------------------------------------------------
# Progressive Web App (issue #70) — manifest, root-scoped service worker,
# and branded offline fallback page.
#
# These endpoints are public (added to public_endpoints in app/__init__.py)
# so the browser can fetch them unauthenticated.  When ENABLE_PWA is off they
# still serve successfully (harmless), but the base template only injects the
# manifest link and SW registration script when the flag is on — so the
# browser never discovers or registers the worker when PWA is disabled.
# ---------------------------------------------------------------------------


def pwa_required(view_func):
    """404 unless ENABLE_PWA is enabled — keeps PWA routes flag-gated."""
    from functools import wraps

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        from flask import abort, current_app

        if not current_app.config.get("ENABLE_PWA", False):
            abort(404)
        return view_func(*args, **kwargs)

    return wrapped


def _pwa_manifest() -> dict:
    """Build the web app manifest dict (installability keys per PWA spec)."""
    return {
        "name": "NSA Webservice",
        "short_name": "NSA",
        "description": "Food Safety Officer adjudication and case management",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#0b3d91",
        "icons": [
            {
                "src": "/static/icons/pwa-192.png",
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": "/static/icons/pwa-512.png",
                "sizes": "512x512",
                "type": "image/png",
            },
            {
                "src": "/static/icons/pwa-192-maskable.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable",
            },
            {
                "src": "/static/icons/pwa-512-maskable.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }


@health_bp.route("/manifest.json")
@pwa_required
def manifest():
    """Serve the web app manifest as JSON (installability)."""
    from flask import jsonify

    return jsonify(_pwa_manifest())


@health_bp.route("/sw.js")
@pwa_required
def service_worker():
    """Serve the root-scoped service worker with a JavaScript content type."""
    from flask import Response, current_app
    import os

    sw_path = os.path.join(current_app.root_path, "static", "src", "sw.js")
    with open(sw_path, encoding="utf-8") as f:
        sw_code = f.read()
    return Response(sw_code, mimetype="application/javascript")


@health_bp.route("/offline")
@pwa_required
def offline():
    """Serve the branded offline fallback page."""
    from flask import render_template

    return render_template("offline.html")
