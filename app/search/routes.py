"""Search routes: page view, JSON API, and re-index trigger."""

from __future__ import annotations

from flask import jsonify, render_template, request
from flask_login import current_user

from app.search import search_bp
from app.search.indexer import (
    ENTITY_TYPES,
)
from app.search.indexer import (
    index_all as search_index_all,
)
from app.search.indexer import (
    search as search_index,
)


@search_bp.route("/")
def index():
    """Render the search page."""
    return render_template("search/index.html", entity_types=sorted(ENTITY_TYPES))


@search_bp.route("/api")
def api_search():
    """JSON search API.

    Query parameters:
        q   — search terms (required)
        type — optional entity type filter (case_file, adjudication, annexure, evidence)
        limit — max results (default 20, capped at 100)

    Returns:
        ``{"results": [...], "query": "...", "total": N}``
    """
    q = request.args.get("q", "").strip()
    entity_type = request.args.get("type", None)
    if entity_type and entity_type not in ENTITY_TYPES:
        return jsonify({"error": f"Invalid entity type: {entity_type}"}), 400
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (ValueError, TypeError):
        limit = 20

    results = search_index(q, entity_type=entity_type, limit=limit)
    return jsonify({"results": results, "query": q, "total": len(results)})


@search_bp.route("/reindex", methods=["POST"])
def reindex():
    """Manually trigger a full re-index of the FTS5 table.

    Returns JSON with the count of indexed records.
    Audit-logged for traceability.
    """
    try:
        from app.services.audit import log_audit

        count = search_index_all()
        actor = current_user.username if current_user.is_authenticated else "system"
        log_audit(
            entity_type="search",
            entity_id="all",
            action="index_rebuilt",
            actor=actor,
            details={"records_indexed": count, "dialect": _dialect()},
        )
        return jsonify({"status": "ok", "records_indexed": count})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


def _dialect() -> str:
    """Return the current database dialect name."""
    from app.extensions import db

    try:
        return str(db.session.get_bind().dialect.name)
    except Exception:
        return "unknown"
