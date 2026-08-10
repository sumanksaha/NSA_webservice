"""Celery tasks for the AI Assistant.

``run_ai_action`` wraps :class:`AIAssistantService` for long-running or
batch operations that should not block the request thread.

Follows the lazy-import pattern from ``app/food_cell/tasks.py``:
the module boots even when Celery is unavailable, and the task is
registered only when a Celery app exists.
"""

from __future__ import annotations

import logging

# Lazy import so the module boots even when Celery isn't installed.
try:
    from celery_app import celery
except ImportError:
    celery = None  # type: ignore[assignment]

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
    """Run an AI action synchronously (used as a Celery task).

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


# Register as a Celery task if celery is available.
if celery is not None:
    run_ai_action = celery.task(bind=True, name="ai_assistant.run_ai_action")(run_ai_action)  # type: ignore[assignment]
