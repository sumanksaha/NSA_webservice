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

    payload = {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "authenticated": current_user.is_authenticated if current_user else False,
        "memory_mb": memory_mb,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    code = 200 if db_status == "connected" else 503
    return jsonify(payload), code
