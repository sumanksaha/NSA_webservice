"""Comments API — Phase 18 RBAC completion.

Comments hang off a parent case (CaseFile or Adjudication) and inherit its
visibility wholesale: an fso-role user may read/add on any case they can see,
and delete only their own; admins may act on any visible case's comments.
"""

from __future__ import annotations

from flask import abort, jsonify, request
from flask_login import current_user

from app.comments import comments_bp
from app.extensions import db
from app.models import Comment
from app.shared.rbac import case_visible_to_user

_VALID_CASE_TYPES = ("case_file", "adjudication")


@comments_bp.route("", methods=["POST"])
@comments_bp.route("/", methods=["POST"])
def add_comment():
    """Add a comment to a case visible to the caller."""
    payload = request.get_json(silent=True) or {}

    case_type = payload.get("case_type")
    if case_type not in _VALID_CASE_TYPES:
        return jsonify({"error": f"case_type must be one of {list(_VALID_CASE_TYPES)}"}), 400

    content = (payload.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400

    try:
        case_id = int(payload.get("case_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "case_id must be an integer"}), 400

    if not case_visible_to_user(current_user, case_type, case_id):
        abort(404)

    section_id = payload.get("section_id") or None
    comment = Comment(
        case_id=case_id,
        case_type=case_type,
        user_id=current_user.id,
        content=content,
        section_id=section_id,
    )
    db.session.add(comment)
    db.session.commit()

    return (
        jsonify({
            "id": comment.id,
            "case_type": comment.case_type,
            "case_id": comment.case_id,
            "user_id": comment.user_id,
            "content": comment.content,
            "section_id": comment.section_id,
            "created_at": comment.created_at.isoformat() if comment.created_at else None,
        }),
        201,
    )


@comments_bp.route("", methods=["GET"])
@comments_bp.route("/", methods=["GET"])
def list_comments():
    """List comments for a case visible to the caller."""
    case_type = request.args.get("case_type")
    if case_type not in _VALID_CASE_TYPES:
        return jsonify({"error": f"case_type must be one of {list(_VALID_CASE_TYPES)}"}), 400
    try:
        case_id = int(request.args.get("case_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "case_id must be an integer"}), 400

    if not case_visible_to_user(current_user, case_type, case_id):
        abort(404)

    rows = Comment.query.filter_by(case_id=case_id, case_type=case_type).order_by(Comment.created_at.asc()).all()
    return (
        jsonify([
            {
                "id": c.id,
                "user_id": c.user_id,
                "content": c.content,
                "section_id": c.section_id,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ]),
        200,
    )


@comments_bp.route("/<int:comment_id>", methods=["DELETE"])
def delete_comment(comment_id: int):
    """Delete a comment. Author or admin only."""
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        abort(404)

    if not case_visible_to_user(current_user, comment.case_type, comment.case_id):
        abort(404)

    if comment.user_id != current_user.id and not getattr(current_user, "is_admin", False):
        abort(403)

    db.session.delete(comment)
    db.session.commit()
    return jsonify({"status": "deleted", "id": comment_id})
