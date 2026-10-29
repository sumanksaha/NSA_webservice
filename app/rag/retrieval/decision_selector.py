"""Decision-model selection seam — typed decision-model evidence selector.

Alternate pathway (plan step 1, default off; ``RAG_DECISION_PATH`` /
``RAG_DECISION_SHADOW`` in ``app/shared/config.py``):

    merged CE reservoir → decision model (``typesafe/jev-1.13``: one
    Decisions-API call, one typed ``noul`` question per candidate) →
    threshold τ + top-k + legal-unit dedupe → ``EvidenceSet`` dict →
    ContextBuilder role-pack → Laguna answer

The sanctioned decision model (``typesafe/jev-1.13``) is a **typed
decision model** served directly by its own provider over OpenRouter's
Decisions API (``POST /api/alpha/decisions``: ``state`` + typed
questions → calibrated probabilities, output tokens free).  There is no
backend routing on that surface, so a sanctioned call can never land on
a banned backend.  Dropped 2026-10-15 after the head-to-head sweep:
``inception/mercury-decide:free`` (f1 0.0521 vs 0.0763, gold-in-prompt
41.1% vs 60.3%, 2.5x slower; it remains the PHOTO-SCORING default, a
separate allow-list).  Banned the same day: ``typesafe/jev-router``, a
meta-router whose policy resolved most of our scoring requests to the
banned ``deepseek/deepseek-v4.1-flash``, spending real money on every
call until it exhausted the key's daily budget.

:class:`DecisionModelClient` posts to the Decisions API with its own
httpx call (key ``OPENROUTER_API_KEY`` / ``AI_ASSISTANT_API_KEY``,
ordinary verifying client).  The chat transport through
:meth:`AIAssistantService.complete_json_with_model` is retained for
chat-type models (none sanctioned today).  The answer model
(``minimax/minimax-m3.1-flash`` by default) remains on the
untouched ``GroundedLLMClient`` path.

Contract (unchanged from the original design — only the scorer changed):

* :func:`select_decision_set` is the single entry point.  It never raises:
  any transport / HTTP / parse failure (including a missing answer or an
  unsanctioned ``RAG_DECISION_MODEL``) falls back to the heuristic
  :func:`~app.rag.retrieval.evidence_selector.select_evidence_set` and
  records the reason in the returned trace (fail-closed, mirrors the
  remote-reranker degradation pattern and the photo scorer's JSON gate).
* Returns ``(evidence_set_dict_or_None, trace)`` — the dict is exactly the
  ``EvidenceSet.to_dict()`` shape :func:`apply_stages` and
  ``ContextBuilder._roles_from_evidence_set`` already consume, so no wire
  layer changes.
* One Decisions-API call per (query, reservoir), questions batched at
  ``_DECISIONS_BATCH`` (50) to stay under the per-request token cap
  (~4.1K input tokens measured on mercury:free; materially more returns
  HTTP 422).  The retrieval-stage trace is reused by
  ``run_generation_pipeline`` so a served path call never scores twice.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from app.rag.retrieval.chunk import (
    chunk_id as chunk_id_of,
    field,
    score as chunk_score,
    text as chunk_text,
)
from app.shared.config import cfg

logger = logging.getLogger(__name__)

#: Hard cap on candidates sent per decision call (100 × 400-char snippets
#: ≈ 12k input tokens; the chat transport renders them into one index-keyed
#: score map, the Decisions API asks one ``noul`` question per candidate).
#: The only decision model sanctioned for this pathway (plan §3.4; user
#: decision 2026-10-15): the **direct** Jev decision model — a typed
#: decision model served by its own provider over the Decisions API, no
#: backend routing possible.  ``inception/mercury-decide:free`` was
#: dropped the same day after the head-to-head (jev won f1 at every tau;
#: gold-in-prompt 60.3% vs 41.1%; 2.5x faster) — it remains solely the
#: photo-scoring default (``PHOTO_SCORING_MODEL``, a separate list).
#:
#: ``typesafe/jev-router`` is BANNED: a meta-router whose policy resolved
#: most of our requests to the banned ``deepseek/deepseek-v4.1-flash``
#: (money burned per call until the key's daily limit broke).  An
#: unsanctioned ``RAG_DECISION_MODEL`` now REFUSES to construct —
#: :func:`select_decision_set` fails closed to the heuristic selector,
#: so a banned model can never serve or spend.
ALLOWED_DECISION_MODELS = ("typesafe/jev-1.13",)

#: Decisions API (typed state + questions → calibrated probabilities).
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"

#: Questions per HTTP call.  The endpoint's per-request token cap is
#: tight (~4.1K input tokens passed on mercury:free; materially more →
#: HTTP 422 ``request_token_limit_exceeded``) — 50 candidates × size-neutral
#: instructions is the proven shape; a larger reservoir splits across calls.
_DECISIONS_BATCH = 50

#: Attempts per Decisions-API call (transient 429/5xx get one backoff).
_DECISIONS_ATTEMPTS = 3


def _uses_decisions_api(model: str) -> bool:
    """Whether *model* is served over the Decisions API (vs chat)."""
    return model in ALLOWED_DECISION_MODELS


_MAX_CANDIDATES = 100

#: Text shown to the model for each candidate.
_SNIPPET_CHARS = 400

#: Chat-transport output budget for the score map (index-keyed JSON — a
#: few tokens per candidate).  Reasoning-capable chat backends burn
#: completion tokens before emitting the map (gemini measured ~509 of
#: 1065), so leave headroom: a truncated map fails the JSON gate and
#: wastes the call.  Unused by the Decisions API (typed answers, no
#: generated JSON).
_MAX_TOKENS = 3072

#: Decision temperature — scoring must be reproducible, not creative
#: (chat transport only; the Decisions API returns calibrated probabilities).
_TEMPERATURE = 0.1

#: Minimum evidence items after selection (mirrors select_evidence_set).
_MIN_SIZE = 2

#: Per-candidate keep judgment for the Decisions API — instructions carry
#: the inclusion/exclusion rules (question keys are not sent to the model,
#: so each question points at its own ``candidates[i]`` state path).  The
#: truncation clause matters: snippets cut mid-provision, and without it
#: the model drops relevant sections whose visible fragment lacks the
#: answer.  Keep this SIZE-NEUTRAL — the endpoint has a per-request token
#: cap (~4.1K input tokens was fine, materially more returns 422).
_DECISION_INSTR = (
    "Judge whether the legal provision at `candidates[{i}]` is evidence worth keeping for "
    "`query`. Keep it if it states or directly relates to a rule, requirement, exception, "
    "penalty, definition, or procedure that helps answer the query; drop duplicates, "
    "boilerplate, and off-topic provisions. The snippet is truncated; judge topical "
    "relevance, not completeness."
)
_DECISION_CRITERIA = {
    "true": "Keep: this provision is relevant evidence for the query.",
    "false": "Drop: not needed as evidence for the query.",
}

_SYSTEM_PROMPT = (
    "You are an evidence-selection judge for Indian food-safety legal queries "
    "under the Food Safety and Standards Act, 2006. Given a query and numbered "
    "candidate provisions, decide for each candidate whether it belongs in the "
    "minimal evidence set needed to answer the query: the governing rule, its "
    "provisos/exceptions, its penalty, or a definition the rule depends on. "
    "Return ONLY a JSON object of the form "
    '{"keep": {"<candidate index>": <probability from 0 to 1>}} '
    "with exactly one entry per candidate index, keyed by the bracketed "
    "index. Do not invent indexes, omit candidates, or add commentary."
)


def decision_enabled() -> bool:
    """Gate for the retrieval-stage trace (shadow or served)."""
    return bool(cfg.decision_shadow or cfg.decision_path)


# --------------------------------------------------------------------------- #
# Decision-model client
# --------------------------------------------------------------------------- #


class DecisionModelClient:
    """Typed client for the decision model (default ``typesafe/jev-1.13``).

    Decisions-API models (:func:`_uses_decisions_api`) post ``state`` +
    typed ``noul`` questions straight to OpenRouter
    (:meth:`_decide_decisions`) — calibrated probabilities come back with
    nothing to parse, output tokens free, no backend routing possible.
    Chat-type models (none sanctioned today) ride the shared
    :class:`~app.ai_assistant.service.AIAssistantService` transport
    (config ``AI_ASSISTANT_*``) via
    :meth:`AIAssistantService.complete_json_with_model`, built lazily at
    call time so configuration problems surface as raised errors.

    An unsanctioned model (e.g. the banned ``typesafe/jev-router``)
    REFUSES to construct — the ban fails closed, and
    :func:`select_decision_set` turns the error into the heuristic
    fallback.

    Args:
        model: Decision model id (default ``cfg.decision_model``).
        timeout: Per-call timeout seconds (default ``cfg.decision_timeout``).
        service: Pre-built chat service (testing / injection).

    """

    def __init__(
        self,
        model: str | None = None,
        timeout: float | None = None,
        service: Any | None = None,
    ) -> None:
        self.model = model or cfg.decision_model
        if self.model not in ALLOWED_DECISION_MODELS:
            raise ValueError(
                f"RAG_DECISION_MODEL={self.model!r} is not a sanctioned decision model "
                f"({', '.join(ALLOWED_DECISION_MODELS)}) — refusing to serve; "
                "fix the config (typesafe/jev-router is banned: it routed to deepseek)"
            )
        self.timeout = cfg.decision_timeout if timeout is None else float(timeout)
        self._service = service

    def _llm(self) -> Any:
        if self._service is not None:
            return self._service
        from app.ai_assistant.service import AIAssistantService

        return AIAssistantService()

    def decide_keep_scores(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[int, float], dict[str, Any]]:
        """P(keep) per candidate index + call metadata (never partial).

        Dispatches on the model: sanctioned typed models over the
        Decisions API, chat-type models over the AI-assistant chat
        transport.  Returns ``({candidate_index: prob}, info)`` where
        ``info`` carries the requested ``model``, the served
        ``resolved_model``, ``tokens``, and ``transport``.  Raises on
        transport, HTTP, disabled-service, parse, or missing-answer
        failure — callers fail closed to the heuristic selector.
        """
        if not candidates:
            return {}, {}
        if _uses_decisions_api(self.model):
            return self._decide_decisions(query, candidates)
        return self._decide_chat(query, candidates)

    # ------------------------------------------------------------------ #
    # Decisions API — the sanctioned path (typed noul → calibrated prob)
    # ------------------------------------------------------------------ #

    def _decide_decisions(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[int, float], dict[str, Any]]:
        """Decisions API: ``state`` + one ``noul`` question per candidate.

        The same shape the eval harness ships (shared
        :data:`_DECISION_INSTR` text — what gets calibrated is what gets
        served), batched at :data:`_DECISIONS_BATCH` to respect the
        per-request token cap.  Probabilities come from
        ``answers["c<index>"].noul``; nothing is generated or parsed.
        Raises on kill-switch / call-budget, key, transport, HTTP, or shape
        failure — fail closed to the heuristic selector.
        """
        # --- Fail-closed spend controls (shared module is the only reader) #
        from app.shared.llm_client import _increment_call_count, _log_llm_call, llm_enabled

        if not llm_enabled():
            raise RuntimeError(
                "LLM calls are DISABLED by the hard kill switch (LLM_ENABLED=false). Set LLM_ENABLED=true to re-enable."
            )
        key = os.environ.get("AI_ASSISTANT_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
        if not key:
            raise RuntimeError("decision model transport not configured (OPENROUTER_API_KEY)")
        _increment_call_count()  # raises when MAX_LLM_CALLS<=0 or exhausted
        _log_llm_call(
            provider="openrouter",
            model=self.model,
            call_site=f"{__name__}.DecisionModelClient._decide_decisions",
        )
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

        resolved: str | None = None
        tokens = 0
        probs: dict[int, float] = {}
        for start in range(0, len(candidates), _DECISIONS_BATCH):
            batch = candidates[start : start + _DECISIONS_BATCH]
            state = {
                "query": query,
                "candidates": [
                    {
                        "act": c.get("act"),
                        "section": c.get("section"),
                        "snippet": (c.get("text") or "")[:_SNIPPET_CHARS],
                    }
                    for c in batch
                ],
            }
            questions = {
                f"c{c['index']}": {
                    "type": "noul",
                    "instructions": _DECISION_INSTR.format(i=j),
                    "criteria": _DECISION_CRITERIA,
                }
                for j, c in enumerate(batch)
            }
            data = self._post_decisions(
                {"model": self.model, "state": state, "questions": questions},
                headers,
                timeout=self.timeout,
            )
            answers = data.get("answers") or {}
            for c in batch:
                answer = answers.get(f"c{c['index']}")
                if not answer or "noul" not in answer:
                    raise ValueError(f"missing noul answer for candidate {c['index']}")
                probs[int(c["index"])] = max(0.0, min(1.0, float(answer["noul"])))
            resolved = data.get("model") or resolved
            usage = data.get("usage") or {}
            tokens += int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
        info = {
            "model": self.model,
            "resolved_model": resolved or self.model,
            "tokens": tokens,
            "transport": "decisions",
        }
        return probs, info

    def _post_decisions(self, body: dict[str, Any], headers: dict[str, str], *, timeout: float) -> dict[str, Any]:
        """POST one Decisions-API request with a retry ladder (test seam).

        Ordinary verifying client (production pattern — the eval harness
        carries its own TLS-bypass twin).  HTTP ≥400 or an ``error`` body
        raises; transient failures back off up to ``_DECISIONS_ATTEMPTS``.
        """
        import httpx

        last: Exception | None = None
        for attempt in range(_DECISIONS_ATTEMPTS):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(DECISIONS_URL, headers=headers, json=body)
                    data = resp.json()
                if resp.status_code >= 400 or "error" in data:
                    raise RuntimeError(f"HTTP {resp.status_code}: {data.get('error')}")
                return data
            except Exception as exc:  # noqa: BLE001 - retry then fail closed
                last = exc
                if attempt < _DECISIONS_ATTEMPTS - 1:
                    time.sleep(2**attempt)
        raise RuntimeError(f"decisions call failed after {_DECISIONS_ATTEMPTS} attempts: {last}")

    # ------------------------------------------------------------------ #
    # Chat transport (chat-type models; none sanctioned today)
    # ------------------------------------------------------------------ #

    def _decide_chat(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[int, float], dict[str, Any]]:
        """One chat call: JSON keep-map + call metadata.

        Retained for chat-type decision models (the sanctioned set is
        decisions-only today).  Returns ``({candidate_index: prob},
        info)``; raises on transport, HTTP, disabled-service, non-JSON,
        or missing-score failure — callers fail closed to the heuristic
        selector.
        """
        llm = self._llm()
        if not llm.is_enabled():
            raise RuntimeError("decision model transport not configured (AI_ASSISTANT_*)")

        data, resolved = llm.complete_json_with_model(
            _SYSTEM_PROMPT,
            _render_user_prompt(query, candidates),
            model=self.model,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            timeout=self.timeout,
        )
        keep = data.get("keep")
        if not isinstance(keep, dict):
            raise ValueError("decision response missing keep map")
        probs: dict[int, float] = {}
        for i in range(len(candidates)):
            if str(i) not in keep:
                raise ValueError(f"decision response missing score for candidate {i}")
            probs[i] = max(0.0, min(1.0, float(keep[str(i)])))
        info = {
            "model": self.model,
            "resolved_model": resolved,
            "tokens": int(getattr(llm, "tokens_used", 0) or 0),
            "transport": "chat",
        }
        return probs, info


def _render_user_prompt(query: str, candidates: list[dict[str, Any]]) -> str:
    """Render the query + numbered candidate rows for the decision call."""
    lines = [f"Query: {query}", "", "Candidates:"]
    for c in candidates:
        lines.append(
            f"[{c['index']}] id={c['chunk_id']} section={c['section']!r} "
            f"act={c['act']!r} retrieval_score={c['score']:.4f}\n{c.get('text') or ''}"
        )
    return "\n\n".join(lines)


# --------------------------------------------------------------------------- #
# Candidate building
# --------------------------------------------------------------------------- #


def _candidate(chunk: Any, index: int) -> dict[str, Any]:
    """Serializable candidate row for the decision prompt.

    Snippets (not full text) keep a 100-candidate call well inside router
    backends' context; section/act/score give the model the identity
    features the ensemble reranker already trusts.
    """
    return {
        "index": index,
        "chunk_id": chunk_id_of(chunk, ""),
        "section": field(chunk, "section_number"),
        "act": field(chunk, "act_name"),
        "score": chunk_score(chunk),
        "text": chunk_text(chunk)[:_SNIPPET_CHARS],
    }


# --------------------------------------------------------------------------- #
# Reservoir + pure selection walk (shared with the offline eval harness)
# --------------------------------------------------------------------------- #


def merge_reservoir(ranked_chunks: list[Any], limit: int) -> list[Any]:
    """Dedupe the ranked pool into the decision reservoir (plan step 2).

    Exact ``chunk_id`` duplicates collapse first, then legal-unit
    duplicates (``canonical_unit_id``) — the best-scored member represents
    the unit, matching ``group_chunks_by_unit`` semantics.  Input order is
    score-descending, so the walk keeps the head; truncated to *limit*.
    """
    from app.rag.retrieval.evidence_selector import canonical_unit_id

    seen_ids: set[str] = set()
    seen_units: set[str] = set()
    out: list[Any] = []
    for chunk in ranked_chunks:
        cid = chunk_id_of(chunk)
        if cid is not None:
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
        unit = canonical_unit_id(chunk)
        if unit in seen_units:
            continue
        seen_units.add(unit)
        out.append(chunk)
        if len(out) >= limit:
            break
    return out


def select_from_scores(
    query: str,
    ranked_chunks: list[Any],
    probs: dict[int, float],
    *,
    threshold: float | None = None,
    top_k: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure selection walk: P(keep) per index → threshold τ → dedupe → top-k.

    *probs* is keyed by position into *ranked_chunks* (the reservoir
    order); indices outside it are ignored, so partial score maps (a cache
    hit for some candidates) degrade gracefully.  This walk is shared
    verbatim between the shipped seam (:func:`select_decision_set`) and
    the offline τ sweep / A-B harness — what gets calibrated is what gets
    served.  Returns ``(EvidenceSet dict, core trace fields)``; never
    raises for degenerate inputs.
    """
    if threshold is None:
        threshold = cfg.decision_threshold
    if top_k is None:
        top_k = max(1, cfg.decision_top_k)
    n = len(ranked_chunks)
    order = sorted((i for i in probs if 0 <= i < n), key=lambda i: probs[i], reverse=True)
    all_below = bool(order) and probs[order[0]] < threshold

    from app.rag.retrieval.evidence_selector import (
        EvidenceItem,
        EvidenceSet,
        _compute_complementarity,
        _compute_redundancy,
        _detect_evidence_type,
        _get_query_section,
        canonical_unit_id,
    )

    query_section = _get_query_section(query)
    units: set[str] = set()
    items: list[EvidenceItem] = []
    dropped_dups: list[str | None] = []
    kept_idx: list[int] = []

    def _take(i: int) -> bool:
        chunk = ranked_chunks[i]
        unit = canonical_unit_id(chunk)
        if unit in units:
            dropped_dups.append(chunk_id_of(chunk))
            return False
        units.add(unit)
        item = EvidenceItem(
            chunk=chunk,
            evidence_type=_detect_evidence_type(chunk, query_section),
            confidence=probs[i],
            legal_identity=unit,
            section_number=field(chunk, "section_number"),
            act_name=field(chunk, "act_name"),
            text_snippet=chunk_text(chunk)[:500],
        )
        item.redundancy = _compute_redundancy(item, items)
        item.complementarity = _compute_complementarity(item, items)
        items.append(item)
        kept_idx.append(i)
        return True

    # 1. threshold pass (order is desc, so everything at/above threshold is a prefix)
    #    + legal-unit dedupe + top-k cap, in one walk.
    for i in order:
        if probs[i] < threshold:
            break
        _take(i)
        if len(items) >= top_k:
            break

    # 2. min-size backfill (below-threshold ranked members only — never an
    #    empty context; recorded in the trace as ``abstain``).
    if len(items) < _MIN_SIZE:
        for i in order:
            if len(items) >= _MIN_SIZE:
                break
            if i not in kept_idx:
                _take(i)

    evidence = EvidenceSet(
        query=query,
        items=items,
        total_pool=n,
        selection_rationale=(
            f"Decision-model selection: {len(items)}/{n} chunks "
            f"(threshold={threshold}, top_k={top_k}, all_below_threshold={all_below})"
        ),
    )
    core = {
        "threshold": threshold,
        "top_k": top_k,
        "pool": n,
        "keep_scores": {chunk_id_of(ranked_chunks[i]): round(probs[i], 4) for i in sorted(probs) if 0 <= i < n},
        "kept": [chunk_id_of(it.chunk) for it in items],
        "dropped_duplicates": dropped_dups,
        "abstain": all_below,
    }
    return evidence.to_dict(), core


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def select_decision_set(
    query: str,
    ranked_chunks: list[Any],
    client: Any | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Select the evidence set for *query* via the decision model.

    The merged pool is deduped and truncated into the candidate reservoir
    (:func:`merge_reservoir`, ``RAG_DECISION_RESERVOIR``), scored by the
    decision model, then walked by :func:`select_from_scores`.  Returns
    ``(evidence_set, trace)`` — ``evidence_set`` is the serialized
    ``EvidenceSet`` dict (``None`` for an empty pool) and ``trace`` always
    carries ``source`` (``"decision_model" | "heuristic" | "empty"``) plus
    the kept ids, keep probabilities, requested/resolved model, tokens,
    reservoir stats and latency for the audit log.

    Never raises: transport/HTTP/parse failures — and an unsanctioned
    ``RAG_DECISION_MODEL`` (the client refuses to construct) — degrade to
    the heuristic selector with ``trace["fallback_reason"]``.
    """
    if not ranked_chunks:
        return None, {"source": "empty", "pool": 0, "kept": []}

    limit = max(1, cfg.decision_reservoir)
    pool = merge_reservoir(ranked_chunks, limit)[:_MAX_CANDIDATES]
    threshold = cfg.decision_threshold
    top_k = max(1, cfg.decision_top_k)

    candidates = [_candidate(c, i) for i, c in enumerate(pool)]
    started = time.monotonic()
    try:
        client = client or DecisionModelClient()
        probs, info = client.decide_keep_scores(query, candidates)
    except Exception as exc:
        logger.warning("select_decision_set: decision model failed (%s) — heuristic fallback", exc)
        es = _heuristic_fallback(query, ranked_chunks)
        trace = {
            "source": "heuristic",
            "fallback_reason": str(exc),
            "threshold": threshold,
            "top_k": top_k,
            "reservoir_limit": limit,
            "pool": len(ranked_chunks),
            "kept": es.chunk_ids,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
        return es.to_dict(), trace
    latency_ms = int((time.monotonic() - started) * 1000)

    evidence, core = select_from_scores(query, pool, probs, threshold=threshold, top_k=top_k)
    # The wire dict reports the FULL input pool (what the caller may cite);
    # the reservoir/scored size is a trace detail.
    evidence["total_pool"] = len(ranked_chunks)
    trace = {
        "source": "decision_model",
        "model": info.get("model"),
        "resolved_model": info.get("resolved_model"),
        "tokens": info.get("tokens", 0),
        "reservoir_limit": limit,
        "deduped": len(ranked_chunks) - len(pool),
        **core,
        "pool": len(ranked_chunks),  # full input pool (overrides core's reservoir view)
        "scored": len(pool),
        "latency_ms": latency_ms,
    }
    logger.info(
        "select_decision_set: kept %d/%d via %s (resolved=%s, abstain=%s, %dms)",
        len(core["kept"]),
        len(pool),
        info.get("model"),
        info.get("resolved_model"),
        core["abstain"],
        latency_ms,
    )
    return evidence, trace


def _heuristic_fallback(query: str, ranked_chunks: list[Any]) -> Any:
    """Deterministic selector — the fail-closed path (never raises)."""
    from app.rag.retrieval.evidence_selector import select_evidence_set

    return select_evidence_set(query, ranked_chunks, max_size=6, min_size=2)
