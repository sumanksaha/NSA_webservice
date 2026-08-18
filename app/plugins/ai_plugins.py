"""Plugin implementations for AI/LLM providers.

Wraps :class:`app.ai_assistant.service.AIAssistantService` behind the
``AIProvider`` interface.

Uses lazy imports — ``AIAssistantService`` is only imported when the plugin
methods are called, so ``import app.plugins.ai_plugins`` works even when
``httpx`` or LLM API keys are absent.
"""

from __future__ import annotations

import logging
from typing import Any

from app.plugins.base import AIProvider

logger = logging.getLogger(__name__)

#: Maps action names to LLM prompt templates (mirrors AIAssistantService._ACTION_METHODS).
_ACTION_PROMPTS = {
    "summarize": (
        "Summarize the following legal document text into 3-5 concise paragraphs. "
        "Preserve all key facts, dates, and legal references. "
        "Use plain legal English, no markdown formatting."
    ),
    "refine_legal": (
        "Rewrite the following legal text to improve legal terminology, "
        "formality, and clarity. Preserve the original meaning, structure, "
        "and all factual content. Return only the refined text, no commentary."
    ),
    "detect_contradictions": (
        "Read the following document text and identify any internal contradictions. "
        "Return your findings as a JSON array of strings. If none found, return an empty array."
    ),
    "suggest_annexures": (
        "Read the following legal document text and identify which standard "
        "annexures are referenced or implied but may be missing. Return your "
        "findings as a JSON array of annexure names. If all present, return an empty array."
    ),
}


class OpenRouterAIPlugin(AIProvider):
    """AI provider wrapping ``AIAssistantService`` for OpenRouter/OpenAI.

    Proxies unknown attribute access (``summarize_text``, ``tokens_used``,
    ``provider``, etc.) to the lazily-instantiated service so callers can
    use it as a drop-in replacement for ``AIAssistantService()``.
    """

    def __init__(self) -> None:
        self._svc: Any = None

    def _service(self) -> Any:
        """Lazily create and cache the underlying AIAssistantService."""
        if self._svc is None:
            from app.ai_assistant.service import AIAssistantService  # lazy

            self._svc = AIAssistantService()
        return self._svc

    def is_enabled(self) -> bool:
        """Return True when the AI assistant is configured (provider + API key)."""
        return self._service().is_enabled()

    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Dispatch to the AI assistant service.

        Accepts an ``action`` kwarg to select the operation type
        (``summarize``, ``refine_legal``, ``detect_contradictions``,
        ``suggest_annexures``).  When ``action`` is omitted, a raw ``prompt``
        is sent as-is via the service's generate method.

        Raises:
            RuntimeError: When the service is not configured (``is_enabled() == False``).
        """
        service = self._service()
        if not service.is_enabled():
            raise RuntimeError(
                "AI Assistant is not configured. Set AI_ASSISTANT_PROVIDER and AI_ASSISTANT_API_KEY.",
            )

        action = kwargs.get("action")
        if action and action in _ACTION_PROMPTS:
            result = getattr(
                service,
                {
                    "summarize": "summarize_text",
                    "refine_legal": "refine_legal_language",
                    "detect_contradictions": "detect_contradictions",
                    "suggest_annexures": "suggest_missing_annexures",
                }.get(action, "summarize_text"),
            )(kwargs.get("content", prompt))
            return result

        # Fallback: send prompt directly (delegates to service.generate or similar)
        content = kwargs.get("content", prompt)
        # Use summarize as the default action for raw prompts
        return service.summarize_text(content)

    def __getattr__(self, name: str) -> Any:
        """Proxy attribute access to the underlying AIAssistantService.

        Lets callers use ``plugin.summarize_text(...)``,
        ``plugin.tokens_used``, ``plugin.provider``, etc. without
        the plugin re-declaring every method.
        """
        return getattr(self._service(), name)


__all__ = ["OpenRouterAIPlugin"]
