from datetime import UTC, datetime

from flask import Blueprint, jsonify
from flask_login import current_user

from app.extensions import db

health_bp = Blueprint("health", __name__)


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
    try:
        from app.utils.qstash_client import qstash_configured

        qstash = "configured" if qstash_configured() else "not-configured"
    except Exception:
        qstash = "not-configured"

    payload = {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "authenticated": current_user.is_authenticated if current_user else False,
        "memory_mb": memory_mb,
        "weasyprint": weasyprint,
        "qstash": qstash,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    code = 200 if db_status == "connected" else 503
    return jsonify(payload), code
