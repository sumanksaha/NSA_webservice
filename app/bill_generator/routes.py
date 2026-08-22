from flask import Blueprint, current_app, jsonify, render_template, request

from app.bill_generator.issuance import issue
from app.bill_generator.utils import get_billable_samples
from app.extensions import db

bill_generator_bp = Blueprint("bill_generator", __name__, template_folder="templates", static_folder="static")


@bill_generator_bp.route("/")
def index():
    return render_template("bill_generator/index.html")


@bill_generator_bp.route("/lookup_fbo_issues", methods=["GET"])
def lookup_fbo_issues():
    """Lookup FBO issues by fbo_id to provide pre-fill options for bill generation.
    Returns open and permission_granted issues that can be used to pre-fill bill forms.
    Query params: fbo_id (required), issue_id (optional - specific issue lookup)

    Thin transport over :func:`app.bill_generator.lookup.lookup_fbo_issues`
    (shared with ``GET /api/v2/bill/lookup-fbo-issues``).
    """
    fbo_id = request.args.get("fbo_id")
    issue_id_raw = request.args.get("issue_id")

    if not fbo_id and not issue_id_raw:
        return jsonify({"error": "Either fbo_id or issue_id is required"}), 400

    issue_id = None
    if issue_id_raw:
        # Guarded parse (matches the adjudication sibling route's 400, not a 500).
        try:
            issue_id = int(issue_id_raw)
        except ValueError:
            return jsonify({"error": "issue_id must be an integer"}), 400

    from app.bill_generator.lookup import lookup_fbo_issues as _lookup

    result = _lookup(db.session, fbo_id=fbo_id, issue_id=issue_id)
    return jsonify(result), 200


@bill_generator_bp.route("/bill/preview", methods=["GET"])
def bill_preview():
    """Preview bill for a date range.
    Query params: start, end (ISO date strings YYYY-MM-DD)
    """
    start = request.args.get("start")
    end = request.args.get("end")

    # Validate
    if not start or not end:
        return jsonify({"error": "Both start and end dates are required"}), 400

    if end < start:
        return jsonify({"error": "End date must be >= start date"}), 400

    try:
        result = get_billable_samples(start, end)
        return jsonify(result), 200
    except Exception as e:
        current_app.logger.error("Bill preview error: %s", e)
        return jsonify({"error": str(e)}), 500


@bill_generator_bp.route("/generate_bill", methods=["POST"])
def generate_bill_route():
    """Issue a bill — thin transport over :func:`app.bill_generator.issuance.issue`.

    The whole transaction (validate → recompute → atomic persist → mark billed
    → best-effort sync → PDF dispatch) lives in the issuance module; this route
    only maps an :class:`~app.bill_generator.issuance.IssuanceResult` onto HTTP.
    """
    form_data = request.form.to_dict()

    start_date = form_data.get("start_date")
    end_date = form_data.get("end_date")

    # Thin UX guard with the same messages the module validates against.
    if not start_date or not end_date:
        return jsonify({"error": "Both start and end dates are required"}), 400

    if end_date < start_date:
        return jsonify({"error": "End date must be >= start date"}), 400

    result = issue(start_date, end_date, form_data)

    if result.status == "invalid":
        return jsonify({"error": result.detail}), 400

    if result.status == "conflict":
        return jsonify({"error": result.detail}), 409

    if result.status == "queued":
        return (
            jsonify({
                "message": "Bill created; PDF generation queued",
                "bill_id": result.bill_id,
                "task_id": result.task_id,
            }),
            202,
        )

    if result.status == "generated":
        return (
            jsonify({
                "message": "Bill created; PDF generated",
                "bill_id": result.bill_id,
                "pdf_result": result.pdf_result,
            }),
            200,
        )

    # status == "error" — the Bill persisted but the PDF step failed.
    # Include bill_id so the UI can offer regeneration instead of a
    # duplicate re-submit (the old bare 500 invited exactly that).
    return jsonify({"error": result.detail, "bill_id": result.bill_id}), 500
