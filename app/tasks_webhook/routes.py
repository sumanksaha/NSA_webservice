"""QStash webhook — executes tasks delivered by QStash.

QStash POSTs each message to ``<PUBLIC_BASE_URL>/tasks/run/<task_name>``.
This endpoint verifies the ``Upstash-Signature`` header (raw body + JWT with
the destination URL as the subject), then runs the requested task synchronously
inside the web request. QStash retries on non-2xx responses, giving durable
delivery without a persistent worker.
"""

import logging
import os
from pathlib import Path

from flask import jsonify, request, send_file

from app.extensions import csrf
from app.tasks_webhook import tasks_webhook_bp
from app.utils.qstash_client import (
    TASK_REGISTRY,
    get_task_status,
    resolve_task,
    store_task_status,
)

logger = logging.getLogger(__name__)

# Root of locally-generated artifacts (PDFs/ZIPs). Computed once at import:
# matches the tasks' cwd-relative ``Path("pdfs")`` while being stable across
# requests (never depends on a per-request cwd).
PDFS_ROOT = (Path.cwd() / "pdfs").resolve()


@tasks_webhook_bp.route("/tasks/run/<task_name>", methods=["POST"])
@csrf.exempt
def run_task(task_name):
    """Execute a QStash-delivered task (signature-verified, CSRF-exempt)."""
    # QStash sends the message id on every delivery — used to update the
    # Redis status store so the frontend can poll for completion.
    message_id = request.headers.get("Upstash-Message-Id", "")

    signature = request.headers.get("Upstash-Signature", "")
    if not signature:
        return jsonify({"error": "Missing Upstash-Signature header"}), 401

    current_key = os.environ.get("QSTASH_CURRENT_SIGNING_KEY")
    next_key = os.environ.get("QSTASH_NEXT_SIGNING_KEY")
    if not (current_key and next_key):
        logger.warning("QStash signing keys not configured; rejecting webhook")
        return jsonify({"error": "QStash not configured"}), 503

    try:
        from qstash import Receiver

        receiver = Receiver(current_signing_key=current_key, next_signing_key=next_key)
        # Note: we deliberately do NOT pass ``url`` here. The HMAC + exp/nbf
        # claims already authenticate the sender (only QStash holds the signing
        # key). Binding the JWT ``sub`` to request.url would be defense-in-depth,
        # but behind ProxyFix (which does not trust x_host) or a custom domain /
        # trailing-slash drift, a mismatch silently 401s every webhook. See the
        # review note; HMAC verification is the security boundary.
        receiver.verify(
            signature=signature,
            body=request.get_data(as_text=True),
        )
    except Exception as exc:
        logger.warning("QStash signature verification failed: %s", exc)
        return jsonify({"error": "Invalid signature"}), 401

    if task_name not in TASK_REGISTRY:
        return jsonify({"error": f"Unknown task: {task_name}"}), 404

    payload = request.get_json(silent=True) or {}

    if message_id:
        store_task_status(message_id, "running", task_name=task_name)

    try:
        task = resolve_task(task_name)
        result = task.apply(kwargs=payload).result
    except Exception as exc:
        logger.error("Webhook task %s failed: %s", task_name, exc)
        if message_id:
            store_task_status(message_id, "error", task_name=task_name, error=str(exc))
        return jsonify({"error": str(exc)}), 500

    # Eager .apply() captures task exceptions (e.g. ValueError for a
    # missing/unsupported file) INTO the result instead of raising — treat
    # that as an error, and reflect task-returned error dicts too, so the
    # status store never reports a failure as "completed".
    if isinstance(result, Exception):
        logger.error("Webhook task %s returned exception: %s", task_name, result)
        if message_id:
            store_task_status(message_id, "error", task_name=task_name, error=str(result))
        return jsonify({"error": str(result)}), 500

    if isinstance(result, dict) and result.get("status") == "error":
        if message_id:
            store_task_status(
                message_id,
                "error",
                task_name=task_name,
                result=result,
                error=result.get("error"),
            )
    elif message_id:
        store_task_status(message_id, "completed", task_name=task_name, result=result)

    return jsonify({"ok": True, "task": task_name, "result": result}), 200


@tasks_webhook_bp.route("/tasks/status/<message_id>", methods=["GET"])
def task_status(message_id):
    """Return the status of a previously queued task (frontend polling).

    Reads the Redis status store written by :func:`run_task`. Login-gated like
    other app routes (the poll comes from the authenticated frontend).
    Returns 404 with ``{"status": "unknown"}`` when the message id is not
    tracked (e.g. before QStash delivers, or if Redis was unavailable).
    """
    found, record = get_task_status(message_id)
    if not found:
        return jsonify({"message_id": message_id, "status": "unknown"}), 404

    return jsonify({"message_id": message_id, **record}), 200


@tasks_webhook_bp.route("/tasks/download", methods=["GET"])
def download_task_file():
    """Serve a generated PDF/ZIP from the local ``pdfs/`` directory.

    The task results store a relative ``file_path`` (e.g.
    ``pdfs/bills/2026/08/bill_5.pdf``). This endpoint resolves it against the
    project's ``pdfs/`` root and serves it. Login-gated (not in
    public_endpoints). The path is validated to prevent traversal.
    """
    file_path = request.args.get("path", "")
    if not file_path:
        return jsonify({"error": "path is required"}), 400

    target = (PDFS_ROOT / file_path).resolve()

    # Prevent path traversal — the file must live under the pdfs/ directory.
    try:
        target.relative_to(PDFS_ROOT)
    except ValueError:
        return jsonify({"error": "Invalid path"}), 403

    if not target.is_file():
        return jsonify({"error": "File not found"}), 404

    return send_file(str(target), as_attachment=True)
