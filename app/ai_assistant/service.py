"""AI Assistant service — httpx-based LLM client with token tracking.

Follows the service-layer pattern from ``app/food_cell/services.py``:
- Reads config from ``current_app`` at call time (lazy, per-request).
- Graceful degradation when API key / provider is absent.
- Token usage tracked via ``usage.total_tokens`` from provider responses
  (satisfies S10c operational monitoring).

No external LLM SDK is required — ``httpx`` (already a project dependency)
is used directly for HTTP calls to OpenRouter/OpenAI-compatible endpoints.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Whitelist of actions exposed by the API.
ACTIONS = frozenset({"summarize", "refine_legal", "detect_contradictions", "suggest_annexures"})

# ---------------------------------------------------------------------------
# Prompt templates — module-level constants (fewest-files principle).
# Each action gets a focused system instruction + the user text.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are an expert legal assistant working on food-safety adjudication "
    "cases under the Food Safety and Standards Act, 2006. "
    "Provide clear, concise, legally accurate responses. "
    "Never fabricate facts or invent case details. When uncertain, state so."
)

_SUMMARIZE_PROMPT = (
    "Summarize the following legal document text into 3-5 concise paragraphs. "
    "Preserve all key facts, dates, and legal references. "
    "Use plain legal English, no markdown formatting."
)

_REFINE_PROMPT = (
    "Rewrite the following legal text to improve legal terminology, "
    "formality, and clarity. Preserve the original meaning, structure, "
    "and all factual content. Do not add or remove substantive information. "
    "Return only the refined text, no commentary."
)

_CONTRADICTIONS_PROMPT = (
    "Read the following document text and identify any internal contradictions — "
    "statements that conflict with each other regarding facts, dates, parties, "
    "or legal positions. Return your findings as a JSON array of strings, "
    "each describing one contradiction. If none found, return an empty array. "
    'Example: ["Statement A says the inspection was on Jan 5, but the sample '
    'collection note says Jan 4."]'
)

_ANNEXURES_PROMPT = (
    "Read the following legal document text and identify which standard "
    "annexures are referenced or implied but may be missing. Standard "
    "annexures include: lab report, sample collection form, site layout plan, "
    "FSSAI licence copy, notice of hearing, show-cause notice, evidence "
    "photographs, inventory list, and compliance report. Return your findings "
    "as a JSON array of annexure names that appear to be referenced or "
    "required but are absent. If all referenced annexures are present, "
    'return an empty array. Example: ["lab report", "site layout plan"]'
)


class AIAssistantService:
    """LLM-backed assistant service with per-request token accounting.

    Configuration is read lazily from ``current_app.config`` so the service
    works inside both request context and Celery task context.

    Args:
        provider: Override the provider ('openrouter' or 'openai'). When
            ``None``, reads ``AI_ASSISTANT_PROVIDER`` from config.
    """

    def __init__(self, provider: str | None = None) -> None:
        from flask import current_app

        self._provider = provider or current_app.config.get("AI_ASSISTANT_PROVIDER")
        self._api_key = current_app.config.get("AI_ASSISTANT_API_KEY")
        self._base_url = current_app.config.get("AI_ASSISTANT_BASE_URL")
        self._model = current_app.config.get("AI_ASSISTANT_MODEL")
        self._tokens_used = 0

    # ------------------------------------------------------------------ #
    # Public state
    # ------------------------------------------------------------------ #

    def is_enabled(self) -> bool:
        """Return ``True`` when the service has the minimum config to call an LLM."""
        return bool(self._provider and self._api_key)

    @property
    def tokens_used(self) -> int:
        """Total tokens consumed by this instance across all requests."""
        return self._tokens_used

    @property
    def provider(self) -> str | None:
        return self._provider

    @property
    def model(self) -> str | None:
        return self._model

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def summarize_text(self, text: str, max_tokens: int = 500) -> str:
        """Summarize legal document text into 3-5 concise paragraphs."""
        prompt = _SUMMARIZE_PROMPT + "\n\nDocument text:\n" + text
        result, _ = self._request(prompt, max_tokens)
        return result

    def refine_legal_language(self, text: str) -> str:
        """Refine legal terminology and formality, preserving meaning."""
        prompt = _REFINE_PROMPT + "\n\nText to refine:\n" + text
        result, _ = self._request(prompt, max_tokens=2048)
        return result

    def detect_contradictions(self, text: str) -> list[str]:
        """Return a list of internal contradictions found in the text."""
        prompt = _CONTRADICTIONS_PROMPT + "\n\nDocument text:\n" + text
        result, _ = self._request(prompt, max_tokens=1024)
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            # If the LLM doesn't return valid JSON, fall back to a single-item
            # list with the raw response so the caller always gets a list.
            return [result] if result.strip() else []

    def suggest_missing_annexures(self, text: str) -> list[str]:
        """Suggest standard annexures that are referenced but absent."""
        prompt = _ANNEXURES_PROMPT + "\n\nDocument text:\n" + text
        result, _ = self._request(prompt, max_tokens=1024)
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return [result] if result.strip() else []

    def evaluate_note(self, text: str) -> dict:
        """Evaluate a Notepad note through four structured lenses.

        Returns a dict with exactly the seven payload keys: ``summary``,
        ``implementation_plan``, ``risks``, ``game_theory``, ``talebian``,
        ``first_principles``, ``feasibility_score`` (int 1-10). Raises on
        provider failure; light validation fills missing fields so a
        slightly-lazy LLM response still lands as a complete record.
        """
        prompt = (
            "You are evaluating an idea or to-do submitted by a Food Safety "
            "Officer for implementation in their legal-workflow platform. "
            "Respond with ONLY a JSON object (no markdown fences, no prose) "
            "with exactly these keys:\n"
            '- "summary": 2-3 sentence restatement of what is being proposed\n'
            '- "implementation_plan": concrete ordered steps and which parts '
            "of a Flask/SQLAlchemy codebase they would touch\n"
            '- "risks": risks, unknowns and failure modes\n'
            '- "game_theory": incentives of everyone involved — who gains, '
            "who bears the cost, where skin-in-the-game is missing\n"
            '- "talebian": fragility vs antifragility analysis — what breaks '
            "under stress, how to gain from disorder, optionality over "
            "forecasting\n"
            '- "first_principles": what would we build with zero assumptions? '
            "Strip away convention and reason from fundamentals\n"
            '- "feasibility_score": integer 1-10 (10 = trivially feasible)\n\n'
            "Submission:\n" + text
        )
        result, _ = self._request(prompt, max_tokens=2048)
        try:
            data = json.loads(result)
        except json.JSONDecodeError as err:
            raise ValueError("AI did not return valid JSON") from err
        if not isinstance(data, dict):
            raise ValueError("AI did not return a JSON object")
        for key in (
            "summary",
            "implementation_plan",
            "risks",
            "game_theory",
            "talebian",
            "first_principles",
            "feasibility_score",
        ):
            data.setdefault(key, "")
        return data

    def draft_prayers(self, facts: str, grounds: str) -> str:
        """Draft prayer clauses for a legal document based on facts and grounds.

        # ponytail: kept as a method on the service for API parity; routes.py
        # and tasks.py dispatch via _ACTION_METHODS dict. No separate file needed.
        """
        prompt = (
            "You are drafting the 'Prayer' (prayer clause) section of a legal "
            "petition under the Food Safety and Standards Act, 2006. "
            "Based on the facts and grounds provided, draft clear, numbered "
            "prayer clauses that the petitioner seeks from the adjudicating "
            "officer. Use formal legal language and a numbered list format. "
            "Return only the prayer clauses, no preamble or commentary.\n\n"
            "Facts:\n" + facts + "\n\nGrounds:\n" + grounds
        )
        result, _ = self._request(prompt, max_tokens=1500)
        return result

    # ------------------------------------------------------------------ #
    # Internal: HTTP request to LLM provider
    # ------------------------------------------------------------------ #

    def _build_url(self) -> str:
        """Construct the API endpoint URL for the configured provider."""
        if self._base_url:
            return self._base_url.rstrip("/") + "/chat/completions"
        if self._provider == "openai":
            return "https://api.openai.com/v1/chat/completions"
        # Default to OpenRouter
        return "https://openrouter.ai/api/v1/chat/completions"

    def _get_headers(self) -> dict[str, str]:
        """Build request headers for the provider."""
        if not self._api_key:
            raise RuntimeError("AI Assistant is not configured (missing API key)")
        headers: dict[str, str] = {
            "Authorization": "Bearer " + self._api_key,
            "Content-Type": "application/json",
        }
        # OpenRouter requires the HTTP-Referer header for analytics.
        if self._provider in (None, "openrouter"):
            headers["HTTP-Referer"] = "https://nsa-webservice.gov.in"
            headers["X-Title"] = "NSA Webservice"
        return headers

    def _get_model(self) -> str:
        """Return the model identifier, using provider-appropriate defaults."""
        if self._model:
            return self._model
        if self._provider == "openai":
            return "gpt-4o-mini"
        # Default to the project's sole model (OpenRouter free tier)
        return "poolside/laguna-s-2.1:free"

    def _request(self, prompt: str, max_tokens: int) -> tuple[str, int]:
        """Send a chat completion request and return (content, tokens_used).

        Retries with exponential backoff on 429/503 (3 attempts).
        Raises ``RuntimeError`` if the service is not enabled or the request
        fails after retries.
        """
        if not self.is_enabled():
            raise RuntimeError("AI Assistant is not configured (missing API key or provider)")

        url = self._build_url()
        headers = self._get_headers()
        model = self._get_model()

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(url, headers=headers, json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    if content is None:
                        # Some OpenRouter reasoning models (e.g.
                        # poolside/laguna-s-2.1:free) may return content=None
                        # while emitting the response in the reasoning field.
                        content = data["choices"][0]["message"].get("reasoning") or ""
                    tokens = data.get("usage", {}).get("total_tokens", 0)
                    self._tokens_used += tokens
                    return content, tokens
            except httpx.HTTPError as exc:
                last_exc = exc
                status = getattr(exc, "response", None)
                status_code = status.status_code if status else None
                if status_code in (429, 408, 503) and attempt < 2:
                    time.sleep(2**attempt)
                    continue
                raise

        # All retries exhausted
        raise RuntimeError("LLM request failed after 3 attempts: " + str(last_exc)) from last_exc


__all__ = ["ACTIONS", "ACTION_METHODS", "AIAssistantService", "dispatch_ai_action"]


# ---------------------------------------------------------------------------- #
# Action dispatch — the one seam both transports share
# ---------------------------------------------------------------------------- #


#: Maps API action names to AIAssistantService methods. Single source of
#: truth — previously duplicated verbatim in ``ai_assistant/routes.py`` and
#: ``api/routers.py`` (and already drifted there).
ACTION_METHODS: dict[str, str] = {
    "summarize": "summarize_text",
    "refine_legal": "refine_legal_language",
    "detect_contradictions": "detect_contradictions",
    "suggest_annexures": "suggest_missing_annexures",
    "draft_prayers": "draft_prayers",
}

#: Actions whose results are ``list[str]`` — serialized to a JSON string for
#: transport (the historical response contract consumed by the UI).
_LIST_RESULT_ACTIONS = frozenset({"detect_contradictions", "suggest_annexures"})


def dispatch_ai_action(
    service: Any,
    action: str,
    content: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and dispatch one AI action, returning the response payload.

    The shared domain function behind both the Flask ``/ai-assistant/assist``
    route and ``POST /api/v2/ai-assistant/assist`` — transports reduce to
    parse → call → translate-errors.

    Args:
        service: The active AI provider (plugin-registry resolved; proxies
            ``AIAssistantService``).
        action: One of :data:`ACTION_METHODS` keys.
        content: The text to process (must be non-blank after stripping).
        context: Optional dict; ``draft_prayers`` reads ``facts``/``grounds``.

    Raises:
        ValueError: Unknown action, or blank/whitespace-only content
            (transports map to 400).
        RuntimeError: ``"AI Assistant is not configured."`` when the service
            is disabled (transports map to 503); any other RuntimeError
            propagates (transports map to 500).
        Exception: Provider failures propagate (transports map to 500).

    Returns:
        ``{"result": str, "tokens_used": int, "action": str}`` — list-valued
        results are ``json.dumps``-ed to match the established contract.
    """
    if action not in ACTION_METHODS:
        raise ValueError(f"Invalid action. Must be one of: {', '.join(sorted(ACTION_METHODS))}.")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string.")
    if not service.is_enabled():
        raise RuntimeError("AI Assistant is not configured.")

    context = context or {}
    method = getattr(service, ACTION_METHODS[action])

    if action == "draft_prayers":
        # Calling convention per the service contract: positional facts/grounds
        # from the request context (NOT the content field).
        result = method(context.get("facts", ""), context.get("grounds", ""))
    elif action in _LIST_RESULT_ACTIONS:
        result = json.dumps(method(content))
    else:
        result = method(content)

    return {"result": result, "tokens_used": service.tokens_used, "action": action}
