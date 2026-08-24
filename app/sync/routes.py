"""Phase 17: Supabase sync routes.

HTML dashboard + JSON probe + push/pull/resolve API endpoints.
Auth-gated via the global ``require_login`` gate.  Sync endpoints return 503
when Supabase is disabled (``ENABLE_SUPABASE_SYNC=false``).
"""

from __future__ import annotations

import json
import logging

from flask import jsonify, render_template, request
from flask_login import login_required

from app.extensions import db
from app.shared.config import cfg
from app.sync import sync_bp
from app.sync.models import SyncConflict, SyncLog

logger = logging.getLogger(__name__)


def _sync_enabled() -> bool:
    return bool(cfg.supabase_sync_enabled and cfg.supabase_url and cfg.supabase_api_key)


def _log_sync(operation: str, result) -> None:
    """Persist a SyncResult to the sync_log table (best-effort)."""
    try:
        entry = SyncLog(
            operation=operation,
            status=result.status,
            pushed=result.pushed,
            pulled=result.pulled,
            conflicts=result.conflicts,
            errors_json=json.dumps(result.errors) if result.errors else None,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as exc:  # pragma: no cover - logging must never block
        logger.warning("sync log write failed: %s", exc)
        db.session.rollback()


@sync_bp.route("/")
@login_required
def index():
    """Sync-status dashboard (HTML).

    Renders the per-model row counts, dirty-record counts, pending conflict
    count, and a manual push / pull / resolve UI. Auto-refreshes at
    ``SUPABASE_SYNC_INTERVAL`` seconds.
    """
    from app.sync.supabase_sync import get_sync_service

    service = get_sync_service()
    status = service.status()

    # Pending conflicts for the resolve modal.
    pending_conflicts = db.session.query(SyncConflict).order_by(SyncConflict.created_at.desc()).limit(50).all()

    # Recent sync log entries.
    recent_logs = db.session.query(SyncLog).order_by(SyncLog.created_at.desc()).limit(20).all()

    return render_template(
        "sync/index.html",
        sync_enabled=status["enabled"],
        client_connected=status["client_connected"],
        supabase_url=status["supabase_url"],
        synced_models=status["synced_models"],
        row_counts=status["row_counts"],
        dirty_counts=status["dirty_counts"],
        pending_conflicts=pending_conflicts,
        recent_logs=recent_logs,
        sync_interval=status["sync_interval"],
        supabase_not_configured=(not _sync_enabled()),
    )


@sync_bp.route("/status")
@login_required
def status():
    """JSON sync-status probe (public-ish: auth-gated via global gate)."""
    from app.sync.supabase_sync import get_sync_service

    service = get_sync_service()
    return jsonify(service.status())


@sync_bp.route("/push", methods=["POST"])
@login_required
def push():
    """Push local dirty records to Supabase."""
    if not _sync_enabled():
        return jsonify({"status": "disabled", "error": "Supabase sync is not configured."}), 503

    from app.sync.supabase_sync import get_sync_service

    service = get_sync_service()
    result = service.push()
    _log_sync("push", result)
    return jsonify(result.to_dict())


@sync_bp.route("/pull", methods=["POST"])
@login_required
def pull():
    """Pull remote changes from Supabase."""
    if not _sync_enabled():
        return jsonify({"status": "disabled", "error": "Supabase sync is not configured."}), 503

    from app.sync.supabase_sync import get_sync_service

    service = get_sync_service()
    result = service.pull()
    _log_sync("pull", result)
    return jsonify(result.to_dict())


@sync_bp.route("/resolve-conflict/<int:conflict_id>", methods=["POST"])
@login_required
def resolve_conflict(conflict_id: int):
    """Resolve a pending sync conflict.

    Request JSON: ``{"winner": "local" | "remote"}``
    """
    if not _sync_enabled():
        return jsonify({"status": "disabled", "error": "Supabase sync is not configured."}), 503

    payload = request.get_json(silent=True) or {}
    winner = payload.get("winner", "local")
    if winner not in ("local", "remote"):
        return jsonify({"error": "winner must be 'local' or 'remote'."}), 400

    from app.sync.supabase_sync import get_sync_service

    service = get_sync_service()
    result = service.resolve_conflict(conflict_id, winner)
    _log_sync("resolve", result)
    return jsonify(result.to_dict()), result.http_status
