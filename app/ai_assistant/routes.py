"""HTTP endpoints for the AI Assistant.

- ``POST /ai-assistant/assist`` — dispatch an action to the LLM service.

Business logic lives in :func:`app.ai_assistant.service.dispatch_ai_action`
(the single seam shared with ``POST /api/v2/ai-assistant/assist``) — this
module is a thin transport: parse → call → translate-errors → jsonify.
"""

from __future__ import annotations

import logging

from flask import jsonify, request

from app.ai_assistant import ai_bp
from app.ai_assistant.service import ACTION_METHODS, dispatch_ai_action  # noqa: F401 — re-exported for tests/conftest

logger = logging.getLogger(__name__)


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

    # Resolve the active AI provider via the plugin registry (Phase 20).
    # The OpenRouterAIPlugin proxies unknown attribute access to the underlying
    # AIAssistantService, so service.is_enabled(), service.summarize_text(),
    # service.tokens_used, etc. all work transparently through the plugin.
    from app.plugins.registry import PluginRegistry

    service = PluginRegistry.get_instance().get_active("ai")

    try:
        data = dispatch_ai_action(
            service,
            payload.get("action"),
            payload.get("content"),
            payload.get("context") or {},
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        if "not configured" in str(exc):
            return jsonify({"error": "AI Assistant is not configured."}), 503
        logger.error("AI action '%s' failed: %s", payload.get("action"), exc)
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        logger.error("AI action '%s' raised unexpected error: %s", payload.get("action"), exc)
        return jsonify({"error": "AI request failed."}), 500

    return jsonify(data)
