"""Tasks for the AI Assistant.

``run_ai_action`` wraps :class:`AIAssistantService` for long-running or
batch operations that should not block the request thread.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Maps API action names to service methods (mirrors routes.py).
_ACTION_METHODS = {
    "summarize": "summarize_text",
    "refine_legal": "refine_legal_language",
    "detect_contradictions": "detect_contradictions",
    "suggest_annexures": "suggest_missing_annexures",
    "draft_prayers": "draft_prayers",
}


def run_ai_action(action: str, content: str, context: dict | None = None) -> dict:
    """Run an AI action synchronously.

    Returns ``{"result": str, "tokens_used": int}``.
    Raises ``ValueError`` for unknown actions.
    """
    from app.ai_assistant.service import AIAssistantService

    if action not in _ACTION_METHODS:
        raise ValueError(f"Unknown action: {action}")

    service = AIAssistantService()
    if not service.is_enabled():
        raise RuntimeError("AI Assistant is not configured")

    method_name = _ACTION_METHODS[action]
    method = getattr(service, method_name)

    if action == "draft_prayers":
        facts = (context or {}).get("facts", "")
        grounds = (context or {}).get("grounds", "")
        result = method(facts, grounds)
    else:
        result = method(content)

    return {"result": result, "tokens_used": service.tokens_used}
