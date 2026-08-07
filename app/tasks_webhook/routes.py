"""QStash webhook — executes tasks delivered by QStash.

QStash POSTs each message to ``<PUBLIC_BASE_URL>/tasks/run/<task_name>``.
This endpoint verifies the ``Upstash-Signature`` header (raw body + JWT with
the destination URL as the subject), then runs the requested task synchronously
inside the web request. QStash retries on non-2xx responses, giving durable
delivery without a persistent worker.

A companion ``/tasks/failed/<task_name>`` endpoint receives QStash's failure
callback (a DLQ pattern) — without it, a permanently-failed message leaves the
Redis status stuck at "pending" forever with no signal to operators.
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
    qstash_configured,
    resolve_task,
    store_task_status,
)

logger = logging.getLogger(__name__)

# Root of locally-generated artifacts (PDFs/ZIPs). Computed once at import:
# matches the tasks' cwd-relative ``Path("pdfs")`` while being stable across
# requests (never depends on a per-request cwd).
PDFS_ROOT = (Path.cwd() / "pdfs").resolve()

# Number of seconds of clock skew allowed when verifying QStash signatures.
# QStash delivery nodes and the app host may drift slightly; without tolerance
# a 1-second drift on a valid webhook causes a silent 401 on exp/nbf claims.
_QSTASH_CLOCK_TOLERANCE = 5


def _verify_qstash_signature(signature: str) -> bool:
    """Verify an Upstash-Signature header against the current+next signing keys.

    Precondition: the caller has already checked ``qstash_configured()``
    (all 4 env vars present) and that ``signature`` is non-empty.

    The ``url`` parameter is deliberately omitted from ``Receiver.verify()``:
    the HMAC + exp/nbf claims already authenticate the sender (only QStash
    holds the signing key). Binding the JWT ``sub`` to ``request.url`` would
    be defense-in-depth, but behind ProxyFix (which does not trust x_host) or
    a custom domain / trailing-slash drift, a mismatch silently 401s every
    webhook. HMAC verification is the security boundary.
    """
    current_key = os.environ["QSTASH_CURRENT_SIGNING_KEY"]
    next_key = os.environ["QSTASH_NEXT_SIGNING_KEY"]

    try:
        from qstash import Receiver

        receiver = Receiver(current_signing_key=current_key, next_signing_key=next_key)
        receiver.verify(
            signature=signature,
            body=request.get_data(as_text=True),
            clock_tolerance=_QSTASH_CLOCK_TOLERANCE,
        )
    except Exception as exc:
        logger.warning("QStash signature verification failed: %s", exc)
        return False

    return True


@tasks_webhook_bp.route("/tasks/run/<task_name>", methods=["POST"])
@csrf.exempt
def run_task(task_name):
    """Execute a QStash-delivered task (signature-verified, CSRF-exempt)."""
    # QStash sends the message id on every delivery — used to update the
    # Redis status store so the frontend can poll for completion.
    message_id = request.headers.get("Upstash-Message-Id", "")

    if not qstash_configured():
        logger.warning("QStash not fully configured; rejecting webhook")
        return jsonify({"error": "QStash not configured"}), 503

    signature = request.headers.get("Upstash-Signature", "")
    if not signature:
        return jsonify({"error": "Missing Upstash-Signature header"}), 401

    if not _verify_qstash_signature(signature):
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


@tasks_webhook_bp.route("/tasks/failed/<task_name>", methods=["POST"])
@csrf.exempt
def delivery_failed(task_name):
    """QStash failure callback — invoked after all retries are exhausted.

    This is the DLQ pattern: without it, a permanently-failed message leaves
    the Redis status stuck at "pending" forever with no signal to operators.
    QStash POSTs a JSON body with ``messageId``, ``url``, ``error``, and
    ``timestamp`` to this endpoint when delivery definitively fails.

    Auth: verified via ``Upstash-Signature`` (same mechanism as ``run_task``),
    not via session cookie — QStash has no session.
    """
    if not qstash_configured():
        logger.warning("QStash not fully configured; rejecting failure callback")
        return jsonify({"error": "QStash not configured"}), 503

    signature = request.headers.get("Upstash-Signature", "")
    if not signature:
        return jsonify({"error": "Missing Upstash-Signature header"}), 401

    if not _verify_qstash_signature(signature):
        return jsonify({"error": "Invalid signature"}), 401

    body = request.get_json(silent=True) or {}
    message_id = body.get("messageId", "") or request.headers.get("Upstash-Message-Id", "")

    if message_id:
        store_task_status(
            message_id,
            "failed",
            task_name=task_name,
            error=f"QStash delivery exhausted all retries: {body.get('error', 'unknown')}",
        )
        logger.error(
            "QStash delivery FAILED permanently for task %s (message_id=%s): %s",
            task_name,
            message_id,
            body.get("error", "unknown"),
        )

    return jsonify({"ok": True}), 200


@tasks_webhook_bp.route("/tasks/status/<message_id>", methods=["GET"])
def task_status(message_id):
    """Return the status of a previously queued task (frontend polling).

    Reads the Redis status store written by :func:`run_task` and
    :func:`delivery_failed`. Login-gated like other app routes (the poll
    comes from the authenticated frontend).
    Returns 404 with ``{"status": "unknown"}`` when the message id is not
    tracked (e.g. before QStash delivers, or if Redis was unavailable).
    """
    found, record = get_task_status(message_id)
    if not found:
        return jsonify({"message_id": message_id, "status": "unknown"}), 404

    # record is guaranteed non-None here (found=True), but the return-type
    # annotation of get_task_status is ``dict | None`` — narrow it for static
    # analysis so ``**record`` is provably a mapping.
    assert record is not None
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

    return send_file(str(target), as_attachment=True, download_name=target.name)
