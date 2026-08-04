"""Version Control API + UI Routes

Flask routes for version control functionality:
- Version snapshot creation
- Version comparison
- Version restoration
- Version branching
- Version history retrieval (JSON API + HTML UI page)

All routes accept a single ``case_id_or_adjudication_id`` path segment and
disambiguate it against the ``CaseFile`` / ``Adjudication`` tables so the same
endpoints work for both case-file documents and non-sample adjudications.
"""

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.services.version_control import VersionError, VersionService

# Create version control blueprint
version_control_bp = Blueprint(
    "version_control",
    __name__,
    url_prefix="/api/version-control",
    template_folder="templates",
)

logger = logging.getLogger(__name__)

# Get version service instance
version_service = VersionService()

_VALID_DOC_TYPES = ("petition", "permission")


def _resolve_target(case_id_or_adjudication_id: int, kind: str | None = None):
    """Resolve whether a path ID refers to a CaseFile or an Adjudication.

    ``kind`` (``"case_file"`` | ``"adjudication"``) disambiguates when both
    tables happen to contain the same numeric ID (each table has its own
    autoincrement). When omitted, CaseFile is checked first, then Adjudication.

    Returns ``(case_id, adjudication_id)`` with exactly one non-None when the
    record exists, or ``(None, None)`` when neither table has that ID.
    """
    from app.models import Adjudication, CaseFile

    if kind == "adjudication":
        if db.session.get(Adjudication, case_id_or_adjudication_id) is not None:
            return None, case_id_or_adjudication_id
        return None, None
    if kind == "case_file":
        if db.session.get(CaseFile, case_id_or_adjudication_id) is not None:
            return case_id_or_adjudication_id, None
        return None, None
    if db.session.get(CaseFile, case_id_or_adjudication_id) is not None:
        return case_id_or_adjudication_id, None
    if db.session.get(Adjudication, case_id_or_adjudication_id) is not None:
        return None, case_id_or_adjudication_id
    return None, None


def _kind_param():
    """Read the optional ``?kind=case_file|adjudication`` disambiguator."""
    return request.args.get("kind") or None


@version_control_bp.route("/save-version", methods=["POST"])
@login_required
def save_version():
    """
    Create a version snapshot when a document is saved or auto-saved.

    Expected JSON body:
    {
        "case_id": 123 or null,
        "adjudication_id": 456 or null,
        "doc_type": "petition" or "permission",
        "html": "<html>...</html>",
        "delta": {...} or null,
        "change_summary": "Updated sections 1 and 2",
    }

    Returns:
    {
        "status": "success",
        "version_id": 789,
        "version_number": 3,
        "content_hash": "sha256_hash"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON"}), 400

        # Validate required fields
        if not data.get("doc_type") or data["doc_type"] not in _VALID_DOC_TYPES:
            return (
                jsonify({"error": "doc_type must be 'petition' or 'permission'"}),
                400,
            )

        if not data.get("html"):
            return jsonify({"error": "No HTML content provided"}), 400

        # Extract user ID from the flask-login current user
        user_id = current_user.get_id() if current_user.is_authenticated else None

        # Create version snapshot (branch_name routes to a named branch when set)
        version = version_service.create_version(
            case_id=data.get("case_id"),
            adjudication_id=data.get("adjudication_id"),
            doc_type=data["doc_type"],
            html_content=data["html"],
            delta_content=data.get("delta"),
            user_id=user_id,
            change_summary=data.get("change_summary"),
            branch_name=data.get("branch_name") or None,
        )

        return jsonify(
            {
                "status": "success",
                "version_id": version.id,
                "version_number": version.version_number,
                "content_hash": version.content_hash,
                "created_at": version.created_at.isoformat(),
            }
        )

    except VersionError as e:
        logger.warning(f"Version creation error: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Unexpected error creating version: {e}")
        return jsonify({"error": "Internal server error"}), 500


@version_control_bp.route(
    "/compare/<int:case_id_or_adjudication_id>/<doc_type>/<int:version_a>/<int:version_b>",
    methods=["GET"],
)
@login_required
def compare_versions(
    case_id_or_adjudication_id: int,
    doc_type: str,
    version_a: int,
    version_b: int,
):
    """
    Compare two versions and return diff information.

    Returns:
    {
        "version_a": {...},
        "version_b": {...},
        "diff": {
            "content_changed": bool,
            "insertions": [...],
            "deletions": [...],
            "word_count_diff": int,
            "similarity": float,
            "unified": "..."
        },
        "deltas": {...}
    }
    """
    try:
        if doc_type not in _VALID_DOC_TYPES:
            return jsonify({"error": "doc_type must be 'petition' or 'permission'"}), 400

        case_id, adjudication_id = _resolve_target(case_id_or_adjudication_id, _kind_param())
        if case_id is None and adjudication_id is None:
            return jsonify({"error": f"Case with ID {case_id_or_adjudication_id} not found"}), 404

        diff_data = version_service.compare_versions(
            case_id=case_id,
            adjudication_id=adjudication_id,
            doc_type=doc_type,
            version_a=version_a,
            version_b=version_b,
        )

        return jsonify(diff_data)

    except VersionError as e:
        logger.warning(f"Version comparison error: {e}")
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"Unexpected error comparing versions: {e}")
        return jsonify({"error": "Internal server error"}), 500


@version_control_bp.route(
    "/restore/<int:case_id_or_adjudication_id>/<doc_type>/<int:version_id>",
    methods=["POST"],
)
@login_required
def restore_version(
    case_id_or_adjudication_id: int,
    doc_type: str,
    version_id: int,
):
    """
    Restore a document to a specific version.

    Restoring writes the snapshot HTML back to ``instance/saved/`` (making it
    the current document) and records an append-only "Restored to version N"
    snapshot, so the history is never lost.

    Expected JSON body:
    {
        "change_summary": "Restored to version X",
    }

    Returns:
    {
        "status": "success",
        "restored_version": {...}
    }
    """
    try:
        if doc_type not in _VALID_DOC_TYPES:
            return jsonify({"error": "doc_type must be 'petition' or 'permission'"}), 400

        data = request.get_json() or {}
        user_id = current_user.get_id() if current_user.is_authenticated else None

        case_id, adjudication_id = _resolve_target(case_id_or_adjudication_id, _kind_param())
        if case_id is None and adjudication_id is None:
            return jsonify({"error": f"Case with ID {case_id_or_adjudication_id} not found"}), 404

        restored = version_service.restore_version(
            case_id=case_id,
            adjudication_id=adjudication_id,
            doc_type=doc_type,
            version_id=version_id,
            user_id=user_id,
            change_summary=data.get("change_summary"),
        )

        return jsonify(
            {
                "status": "success",
                "restored_version": {
                    "id": restored.id,
                    "version_number": restored.version_number,
                    "created_at": restored.created_at.isoformat(),
                    "content_hash": restored.content_hash,
                    "change_summary": restored.change_summary,
                },
            }
        )

    except VersionError as e:
        logger.warning(f"Version restore error: {e}")
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"Unexpected error restoring version: {e}")
        return jsonify({"error": "Internal server error"}), 500


@version_control_bp.route("/branch", methods=["POST"])
@login_required
def create_branch():
    """
    Create a branch/draft from a specific version.

    Expected JSON body:
    {
        "case_id": 123 or null,
        "adjudication_id": 456 or null,
        "doc_type": "petition" or "permission",
        "from_version": 1,
        "branch_name": "draft-2026-01-01",
        "change_summary": "Starting new branch from version 1",
    }

    Returns:
    {
        "status": "success",
        "branch": {...}
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON"}), 400

        # Validate required fields
        if not data.get("doc_type") or data["doc_type"] not in _VALID_DOC_TYPES:
            return (
                jsonify({"error": "doc_type must be 'petition' or 'permission'"}),
                400,
            )

        if not data.get("from_version"):
            return jsonify({"error": "from_version is required"}), 400

        if not data.get("branch_name"):
            return jsonify({"error": "branch_name is required"}), 400

        user_id = current_user.get_id() if current_user.is_authenticated else None

        branch_data = version_service.create_branch(
            case_id=data.get("case_id"),
            adjudication_id=data.get("adjudication_id"),
            doc_type=data["doc_type"],
            from_version=data["from_version"],
            branch_name=data["branch_name"],
            user_id=user_id,
        )

        return jsonify({"status": "success", "branch": branch_data})

    except VersionError as e:
        logger.warning(f"Branch creation error: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Unexpected error creating branch: {e}")
        return jsonify({"error": "Internal server error"}), 500


@version_control_bp.route("/history/<int:case_id_or_adjudication_id>", methods=["GET"])
@login_required
def get_version_history(case_id_or_adjudication_id: int):
    """Get version history (JSON) for a case or adjudication.

    Returns:
    {
        "petition": [...],
        "permission": [...],
        "branches": [...]
    }
    """
    try:
        case_id, adjudication_id = _resolve_target(case_id_or_adjudication_id, _kind_param())
        if case_id is None and adjudication_id is None:
            return jsonify({"error": f"Case with ID {case_id_or_adjudication_id} not found"}), 404

        history_data = version_service.get_version_history_ui_data(
            case_id=case_id,
            adjudication_id=adjudication_id,
        )

        return jsonify(history_data)

    except Exception as e:
        logger.error(f"Unexpected error getting version history: {e}")
        return jsonify({"error": "Internal server error"}), 500


@version_control_bp.route("/history/ui/<int:case_id_or_adjudication_id>", methods=["GET"])
@login_required
def history_page(case_id_or_adjudication_id: int):
    """Render the version-history UI page for a case or adjudication."""
    from app.models import Adjudication, CaseFile

    case_id, adjudication_id = _resolve_target(case_id_or_adjudication_id, _kind_param())
    if case_id is None and adjudication_id is None:
        return jsonify({"error": f"Case with ID {case_id_or_adjudication_id} not found"}), 404

    if case_id is not None:
        case_number = db.session.get(CaseFile, case_id).case_number
        case_type = "case_file"
    else:
        case_number = db.session.get(Adjudication, adjudication_id).case_number
        case_type = "adjudication"

    return render_template(
        "version_control/history.html",
        case_number=case_number,
        case_id=case_id,
        adjudication_id=adjudication_id,
        case_type=case_type,
    )
