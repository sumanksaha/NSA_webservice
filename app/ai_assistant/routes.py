"""HTTP endpoints for the AI Assistant.

- ``POST /ai-assistant/assist`` — dispatch an action to the LLM service.
"""

from __future__ import annotations

import logging

from flask import jsonify, request

from app.ai_assistant import ai_bp
from app.ai_assistant.service import AIAssistantService

logger = logging.getLogger(__name__)

#: Maps API action names to service methods.
_ACTION_METHODS = {
    "summarize": "summarize_text",
    "refine_legal": "refine_legal_language",
    "detect_contradictions": "detect_contradictions",
    "suggest_annexures": "suggest_missing_annexures",
    "draft_prayers": "draft_prayers",
}


@ai_bp.route("/assist", methods=["POST"])
def assist():
    """Dispatch an AI action and return the result.

    Request JSON:
        ``{"action": str, "content": str, "context": dict | None}``

    Response JSON:
        ``{"result": str | list, "tokens_used": int, "action": str}``
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    action = payload.get("action")
    if action not in _ACTION_METHODS:
        return jsonify({"error": f"Invalid action. Must be one of: {', '.join(sorted(_ACTION_METHODS))}."}), 400

    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return jsonify({"error": "content must be a non-empty string."}), 400

    # draft_prayers uses context for facts/grounds; other actions use content.
    context = payload.get("context") or {}

    service = AIAssistantService()

    if not service.is_enabled():
        return jsonify({"error": "AI Assistant is not configured."}), 503

    method_name = _ACTION_METHODS[action]
    method = getattr(service, method_name)

    try:
        if action == "draft_prayers":
            facts = context.get("facts", "")
            grounds = context.get("grounds", "")
            result = method(facts, grounds)
        elif action in ("detect_contradictions", "suggest_annexures"):
            # These return list[str]; serialize to JSON string for transport.
            import json

            lst = method(content)
            result = json.dumps(lst)
        else:
            result = method(content)
    except RuntimeError as exc:
        if "not configured" in str(exc):
            return jsonify({"error": "AI Assistant is not configured."}), 503
        logger.error("AI action '%s' failed: %s", action, exc)
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        logger.error("AI action '%s' raised unexpected error: %s", action, exc)
        return jsonify({"error": "AI request failed."}), 500

    return jsonify({"result": result, "tokens_used": service.tokens_used, "action": action})
