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

        self._provider: str | None = provider or current_app.config.get("AI_ASSISTANT_PROVIDER")
        self._api_key: str | None = current_app.config.get("AI_ASSISTANT_API_KEY")
        self._base_url: str | None = current_app.config.get("AI_ASSISTANT_BASE_URL")
        self._model: str | None = current_app.config.get("AI_ASSISTANT_MODEL")
        self._tokens_used = 0

    # ------------------------------------------------------------------ #
    # Public state
    # ------------------------------------------------------------------ #

    def is_enabled(self) -> bool:
        """Return ``True`` when the service may call an LLM.

        Requires the configured provider + API key AND the app-wide kill
        switch (``LLM_ENABLED=true``); when disabled the routes already
        translate this to 503.
        """
        from app.shared.llm_client import llm_enabled

        return bool(self._provider and self._api_key and llm_enabled())

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
            contradictions: list[str] = json.loads(result)
            return contradictions
        except (json.JSONDecodeError, TypeError):
            # If the LLM doesn't return valid JSON, fall back to a single-item
            # list with the raw response so the caller always gets a list.
            return [result] if result.strip() else []

    def suggest_missing_annexures(self, text: str) -> list[str]:
        """Suggest standard annexures that are referenced but absent."""
        prompt = _ANNEXURES_PROMPT + "\n\nDocument text:\n" + text
        result, _ = self._request(prompt, max_tokens=1024)
        try:
            annexures: list[str] = json.loads(result)
            return annexures
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

    def plan_open_notes(self, notes: list[dict]) -> dict:
        """Build a Daily Plan from a user's open Notepad notes.

        ``notes`` is a list of ``{"id": int, "content_text": str}``.
        Returns a dict with exactly three keys: ``items`` (top 3-5 battle
        plan entries: note_id / effort_bucket quick|medium|long / why),
        ``ranking`` (full ordered list, same entry shape), and
        ``portfolio_rationale`` (markdown prose applying the game-theory,
        Talebian and first-principles lenses to the *sequence*).
        Sequencing principle: shortest-task-first (SPT) — effort ascending,
        tie-broken by lens score. Raises on provider failure; the route
        owns payload validation.
        """
        notes_block = "\n".join(f"- id={n['id']}: {n['content_text']}" for n in notes)
        prompt = (
            "You are planning one day of work for a Food Safety Officer. "
            "Below are their open to-do notes. Respond with ONLY a JSON "
            "object (no markdown fences, no prose) with exactly these keys:\n"
            '- "items": array of 3-5 entries worth doing TODAY — each an '
            'object {"note_id": int, "effort_bucket": "quick"|"medium"|'
            '"long", "why": 1-2 sentence justification}\n'
            '- "ranking": ALL offered notes ordered best-to-do-first, same '
            "entry shape\n"
            '- "portfolio_rationale": markdown prose applying these lenses '
            "to the SEQUENCE, not individual items: game theory (which quick "
            "win buys cooperation for the hard ones), Talebian antifragility "
            "(prefer the reversible experiment first; name what should be "
            "ignored today), first principles (what unblocks or makes the "
            "rest trivial)\n\n"
            "Sequencing rule: least-time-first — order by effort_bucket "
            "ascending (quick before medium before long); break ties by "
            "which task teaches most or unlocks others.\n\n"
            "Open notes:\n" + notes_block
        )
        result, _ = self._request(prompt, max_tokens=2048)
        try:
            data = json.loads(result)
        except json.JSONDecodeError as err:
            raise ValueError("AI did not return valid JSON") from err
        if not isinstance(data, dict):
            raise ValueError("AI did not return a JSON object")
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
        """Return the model identifier for this assistant request.

        Provider-appropriate default: ``gpt-4o-mini`` for the OpenAI
        provider, ``minimax/minimax-m3.1-flash`` for OpenRouter (the
        project's answer model); an explicit ``AI_ASSISTANT_MODEL`` override
        is honoured as-is.

        The spend controls that gate this surface — the ``LLM_ENABLED`` kill
        switch, the ``MAX_LLM_CALLS`` budget, and request/response
        accounting — are enforced in :meth:`_post_messages` via
        :mod:`app.shared.llm_client`.  The answer-model
        ``ALLOWED_MODELS`` allow-list is intentionally applied only on the
        RAG answer path (:func:`get_llm_client` / ``GroundedLLMClient``):
        the assistant is a separate chat surface and must remain usable with
        provider-specific models (e.g. ``gpt-4o-mini``), so it is *not*
        restricted to the allow-list — a stray model never silently switches
        backend providers because there is no fallback logic; failures surface.
        """
        if self._model:
            model_id = self._model
        elif self._provider == "openai":
            model_id = "gpt-4o-mini"
        else:
            # Default to the project's answer model (OpenRouter free tier).
            model_id = "minimax/minimax-m3.1-flash"
        return model_id

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Complete with a caller-owned system prompt; return parsed JSON object.

        Used by agents (e.g. the FBO auditor) that need structured output
        under their own persona. Raises ``ValueError`` when the provider
        does not return a JSON object (fail-closed — callers must not render
        unparsed prose as fact); ``RuntimeError`` propagates when disabled.
        """
        raw, _ = self._request(user_prompt, max_tokens, system_prompt=system_prompt, temperature=temperature)
        return parse_json_object(raw)

    def complete_json_with_model(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.1,
        timeout: float = 30.0,
    ) -> tuple[dict[str, Any], str | None]:
        """Complete against an explicitly chosen model; return ``(parsed_object, resolved_model)``.

        The text-mode counterpart to :meth:`complete_json_vision` — used when
        a caller pins a model other than the configured default (an
        inspection probe or a chat-type decision-model call).  For router
        models the served model differs from the requested one, so the
        resolved model is returned for the audit trail.  Raises ``ValueError`` when the provider does
        not return a JSON object (fail-closed — callers must not treat
        unparsed prose as a score) and ``RuntimeError`` when disabled or
        after retries are exhausted.
        """
        content, _tokens, resolved = self._post_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
            timeout=timeout,
        )
        return parse_json_object(content), resolved

    def complete_json_vision(
        self,
        system_prompt: str,
        user_prompt: str,
        image_url: str,
        *,
        model: str | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.1,
        timeout: float = 90.0,
    ) -> tuple[dict[str, Any], str | None]:
        """Complete with an image attached; return ``(parsed_object, resolved_model)``.

        The multimodal counterpart to :meth:`complete_json`. ``image_url`` may be
        an ``https://`` URL or a ``data:image/...;base64,...`` URI. Returns the
        **resolved** model as the second element: for router models (a
        router id selects a backend per request, e.g. ``jev-router``-style
        meta-models) the served model differs from the requested one, and
        callers that persist scores need both to reproduce one later.

        Raises ``ValueError`` when the provider does not return a JSON object
        (fail-closed — callers must not render unparsed prose as fact) and
        ``RuntimeError`` when disabled or after retries are exhausted. The
        default timeout is longer than the text path's because a vision call
        over a real photograph measured ~14s.
        """
        content, tokens, resolved = self._post_messages(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
            timeout=timeout,
        )
        return parse_json_object(content), resolved

    def _request(
        self,
        prompt: str,
        max_tokens: int,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.1,
    ) -> tuple[str, int]:
        """Send a text chat completion request and return (content, tokens_used).

        Retries with exponential backoff on 429/503 (3 attempts).
        Raises ``RuntimeError`` if the service is not enabled or the request
        fails after retries.
        """
        content, tokens, _resolved = self._post_messages(
            [
                {"role": "system", "content": system_prompt or _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return content, tokens

    def _post_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> tuple[str, int, str | None]:
        """Post a full ``messages`` array; return ``(content, tokens, resolved_model)``.

        The single transport for every call this service makes — text and
        vision alike — so retry, token accounting, and the
        ``content is None`` reasoning-field fallback have exactly one
        implementation. ``model`` overrides the configured default (the photo
        scorer pins its own vision model). ``resolved_model`` is what the
        provider reports it actually served, which differs from the request
        when the endpoint is a router; ``None`` when the provider omits it.
        """
        # --- Enforce the hard kill switch at call time (defense in depth;
        # is_enabled() already gates the routes to 503). --- #
        from app.shared.llm_client import _increment_call_count, _log_llm_call, llm_enabled

        if not llm_enabled():
            raise RuntimeError(
                "LLM calls are DISABLED by the hard kill switch (LLM_ENABLED=false). Set LLM_ENABLED=true to re-enable."
            )

        if not self.is_enabled():
            raise RuntimeError("AI Assistant is not configured (missing API key or provider)")

        url = self._build_url()
        headers = self._get_headers()
        # Default-model resolution validates against the Laguna allow-list
        # (_get_model); an explicitly pinned model (decision pathway) carries
        # its own allow-list, enforced by the caller.
        model_id = model or self._get_model()

        # --- Call accounting: budget + request log --- #
        _increment_call_count()
        _log_llm_call(
            provider=self._provider or "unknown",
            model=model_id,
            call_site=f"{self.__class__.__module__}.{self.__class__.__name__}._post_messages",
        )

        body: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(url, headers=headers, json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    if content is None:
                        # Some OpenRouter reasoning models may return
                        # content=None while emitting the response in the
                        # reasoning field.
                        content = data["choices"][0]["message"].get("reasoning") or ""
                    tokens = data.get("usage", {}).get("total_tokens", 0)
                    self._tokens_used += tokens
                    # Call accounting: record the provider-reported tokens.
                    _log_llm_call(
                        provider=self._provider or "unknown",
                        model=data.get("model") or model_id,
                        call_site=f"{self.__class__.__module__}.{self.__class__.__name__}._post_messages",
                        outcome="response",
                        total_tokens=tokens,
                    )
                    return content, tokens, data.get("model") or None
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


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse *content* into a JSON object, tolerating markdown code fences.

    Chat backends vary in how they wrap JSON — several wrap it in
    ```` ```json ... ``` ```` fences (observed with router-resolved
    backends) — ``json.loads`` alone fails at char 0 on those.  Strips a
    leading/prose-embedded fence first; then fails closed with the
    established messages (``ValueError`` — callers must never treat
    unparsed prose as a score/fact).
    """
    text = (content or "").strip()
    if not text.startswith("```"):
        start = text.find("```")
        if start != -1:
            text = text[start:]
    if text.startswith("```"):
        # Drop the opening fence line, then cut at the closing fence
        # (prose may follow the block).
        nl = text.find("\n")
        text = text[nl + 1 :] if nl != -1 else ""
        end = text.find("```")
        if end != -1:
            text = text[:end]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError("AI did not return valid JSON") from err
    if not isinstance(data, dict):
        raise ValueError("AI did not return a JSON object")
    return data


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
