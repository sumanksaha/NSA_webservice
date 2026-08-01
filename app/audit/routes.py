"""Read-only admin route to view recent ``RecordAudit`` entries.

Paginated, filterable by ``record_type`` and ``user_id``.
No editing capability — this is strictly a view.
"""

from flask import render_template, request
from flask_login import login_required

from app.audit import audit_bp
from app.extensions import db
from app.models import RecordAudit, User


@audit_bp.route("/audit-log")
@login_required
def view_audit_log():
    """Render a paginated, filterable view of the audit log."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = min(per_page, 200)  # cap

    record_type_filter = request.args.get("record_type", "").strip()
    user_id_filter = request.args.get("user_id", "", type=int)

    query = RecordAudit.query.order_by(RecordAudit.id.desc())

    if record_type_filter:
        query = query.filter(RecordAudit.record_type == record_type_filter)
    if user_id_filter:
        query = query.filter(RecordAudit.user_id == user_id_filter)

    paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    # Distinct record types for filter dropdown
    distinct_types = [
        row[0] for row in db.session.query(RecordAudit.record_type).distinct().order_by(RecordAudit.record_type).all()
    ]

    # All users for filter dropdown
    users = User.query.order_by(User.username).all()

    return render_template(
        "audit/index.html",
        entries=paginated.items,
        pagination=paginated,
        distinct_types=distinct_types,
        users=users,
        current_record_type=record_type_filter,
        current_user_id=user_id_filter,
    )
