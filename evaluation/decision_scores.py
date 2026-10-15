"""Shared decision-model scoring + cache for the decision-pathway evals.

Used by ``tau_sweep.py`` and ``ab_decision_selector.py``: scores each
benchmark question's candidate reservoir with the SANCTIONED decision
model (``typesafe/jev-1.13``), caches ``{chunk_id: P(keep)}`` per
question under ``evaluation/out/decision/``, and lets both scripts
re-derive selections from the cache without paying for another model
call.

Two transports (model-dispatched inside :func:`score_pool`):

* **Decisions API** (``/api/alpha/decisions``) — what every sanctioned
  model speaks (today ``typesafe/jev-1.13``):
  one typed ``noul`` question per candidate, answered with a calibrated
  probability — no text generation, nothing to parse, output tokens free.
  The models are served directly by their own provider; there is no
  backend routing, so a banned backend cannot appear.
* **Chat completions** — retained as the generic transport for chat-type
  models, importing the prompt/JSON contract from
  ``app.rag.retrieval.decision_selector`` so eval and production cannot
  drift.  Responses a router resolves to a blocked backend (deepseek)
  are rejected and retried, never scored.  ``typesafe/jev-router`` used
  to live here — banned 2026-10-15: its policy routed most requests to
  deepseek (see ``ALLOWED_DECISION_MODELS``).

Transport mirrors ``llm_ssl_client`` (TLS-intercepting corporate proxy →
``verify=False``) — an environment workaround, not a product change:
production traffic uses the ordinary verifying client.

Cache file: ``out/decision/keep_scores_v1.jsonl`` — append-mode JSON
records from every model run; load with a *model* filter so one
calibration never mixes scorers.  Fields: ``question_id``, ``query``,
``model`` (requested), ``resolved_model`` (backend/version that served),
``transport`` (``decisions`` | ``chat``), ``scores`` (``{chunk_id:
P(keep) 0-1}``), ``n_candidates``, ``latency_ms``, ``tokens``, ``error``
(None on success).
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock, Semaphore
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)

from app.ai_assistant.service import parse_json_object  # noqa: E402
from app.rag.retrieval.decision_selector import (  # noqa: E402
    _DECISION_CRITERIA,
    _DECISION_INSTR,
    _MAX_TOKENS,
    _SNIPPET_CHARS,
    _candidate,
    _render_user_prompt,
    _SYSTEM_PROMPT,
)
from evaluation.bench_p0_1_packing import RAW_DIR, _chunks_for, _load_jsonl, _load_payload_index  # noqa: E402
from evaluation.benchmark import load_questions  # noqa: E402

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "decision"
SCORE_CACHE = OUT_DIR / "keep_scores_v1.jsonl"
TABULATION = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "full_review_tabulation.json"

DEFAULT_ARM = "C_hybrid"
#: Evaluation default: the sanctioned Jev decision model over the
#: Decisions API — calibrated probabilities instead of parsed text,
#: served directly by its own provider (no backend routing possible).
#: Cheap: ~$0.042/M input tokens, output free (the full 150-pool sweep
#: measured $0.0785; cached scores make reruns free).
DEFAULT_MODEL = "typesafe/jev-1.13"
MAX_ATTEMPTS = 3
TIMEOUT_S = 60.0
SCORE_WORKERS = 4

#: The only decision model sanctioned for this pathway (plan §3.4; user
#: decision 2026-10-15): the **direct** Jev decision model
#: (``typesafe/jev-1.13``) — speaks the Decisions API and is served by
#: its own provider, no backend routing.  ``inception/mercury-decide:free``
#: was dropped the same day after the head-to-head (jev won f1 at every
#: tau; gold-in-prompt 60.3% vs 41.1%; 2.5x faster); its historical
#: records stay in the cache behind the model filter.
#:
#: ``typesafe/jev-router`` is BANNED: it is a meta-router (Jev picks a
#: backend per request, "balancing quality, speed and cost") and its policy
#: resolved the majority of our scoring requests to
#: ``deepseek/deepseek-v4.1-flash`` — a banned backend — spending real
#: money on every call and exhausting the key's daily budget.  Excluding
#: deepseek there happens only after the paid response arrives (the router
#: plugin's ``excluded_models`` is the preventive mechanism, and even that
#: we no longer rely on: the router itself is out of the allowlist).
#:
#: Scoring aborts on anything else so a stray RAG_DECISION_MODEL cannot
#: silently calibrate the sweep / A-B against an unsanctioned model.
ALLOWED_DECISION_MODELS = ("typesafe/jev-1.13",)

#: Backends no sanctioned call may ever be served by (user decision:
#: deepseek is banned outright).  The Decisions API cannot route — each
#: model is served by its own provider — but the chat transport can, so a
#: chat response resolved to one of these is rejected and retried, never
#: scored.  Defense in depth behind the allowlist.
BLOCKED_BACKENDS = ("deepseek",)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"

#: Per-candidate keep judgment (``_DECISION_INSTR``) and its pass/fail
#: criteria live in ``app.rag.retrieval.decision_selector`` — eval and
#: serving share the exact question text.


def decision_model() -> str:
    model = os.environ.get("RAG_DECISION_MODEL") or DEFAULT_MODEL
    if model not in ALLOWED_DECISION_MODELS:
        raise RuntimeError(
            f"RAG_DECISION_MODEL={model!r} is not a sanctioned decision model; "
            f"allowed: {', '.join(ALLOWED_DECISION_MODELS)}"
        )
    return model


def openrouter_key() -> str:
    key = os.environ.get("AI_ASSISTANT_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
    if not key:
        raise RuntimeError("no OpenRouter key: set AI_ASSISTANT_API_KEY (or OPENROUTER_API_KEY)")
    return key


# --------------------------------------------------------------------------- #
# Pools + gold resolution (shared with the other ceiling_v5 harnesses)
# --------------------------------------------------------------------------- #


def load_pools(arm: str = DEFAULT_ARM) -> tuple[dict[str, list], dict[str, Any], dict[str, dict]]:
    """Cached arm rows → ``(qid -> RetrievedChunk pool, questions, payload_index)``."""
    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}
    pools = {
        str(r["question_id"]): _chunks_for([str(c) for c in (r.get("chunk_ids") or [])], payload_index)
        for r in _load_jsonl(RAW_DIR / f"{arm}.jsonl")
    }
    return pools, questions, payload_index


def model_wrong_qids() -> set[str]:
    """The 89 ``model_wrong`` qids from the frozen review tabulation."""
    if not TABULATION.exists():
        return set()
    tab = json.loads(TABULATION.read_text(encoding="utf-8"))
    return {str(r["qid"]) for r in tab.get("records") or [] if r.get("verdict") == "model_wrong"}


def gold_chunk_ids(question: Any, chunks: list, payload_index: dict[str, dict], family_map: Any) -> set[str]:
    """Chunk ids in *chunks* that resolve to any of the question's gold units."""
    from evaluation.resolution import matches_gold

    units = question.recall_units()
    out: set[str] = set()
    if not units:
        return out
    for c in chunks:
        payload = payload_index.get(str(c.chunk_id))
        if payload is None:
            continue
        for unit in units:
            try:
                if matches_gold(payload, unit, family_map):
                    out.add(str(c.chunk_id))
                    break
            except Exception:
                continue
    return out


# --------------------------------------------------------------------------- #
# Decision-model transport (SSL-bypass twin of DecisionModelClient)
# --------------------------------------------------------------------------- #


def score_pool(query: str, chunks: list, *, model: str | None = None) -> dict[str, Any]:
    """Score one pool with a sanctioned decision model; never raises.

    Every sanctioned model (``ALLOWED_DECISION_MODELS``) speaks the
    Decisions API — typed ``noul`` questions → calibrated probabilities,
    zero output cost, no text to parse.  Returns the cache record
    (``scores`` may be empty when ``error`` is set); ``transport`` records
    which API served the call.  An explicitly-passed unsanctioned model
    yields an error record, never a call.
    """
    model = model or decision_model()
    if model not in ALLOWED_DECISION_MODELS:
        started = time.monotonic()
        return _error_record(
            model,
            len(chunks),
            started,
            RuntimeError(f"unsanctioned decision model {model!r}; allowed: {', '.join(ALLOWED_DECISION_MODELS)}"),
            transport="rejected",
        )
    # Live spend is gated by the app's kill switch too: eval scoring must
    # never run against an unset LLM_ENABLED (the reason for the $1 charge).
    from app.shared.llm_client import llm_enabled

    if not llm_enabled():
        started = time.monotonic()
        return _error_record(
            model,
            len(chunks),
            started,
            RuntimeError(
                "LLM disabled (LLM_ENABLED=false) — refusing to score; "
                "set LLM_ENABLED=true and MAX_LLM_CALLS for eval runs."
            ),
            transport="rejected",
        )
    return _score_decisions(query, chunks, model)


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer " + openrouter_key(),
        "Content-Type": "application/json",
        "HTTP-Referer": "https://nsa-webservice.local",
        "X-Title": "NSA Webservice Eval Harness",
    }


def _error_record(
    model: str,
    n_candidates: int,
    started: float,
    exc: Exception,
    *,
    transport: str,
    resolved: str | None = None,
    tokens: int = 0,
) -> dict[str, Any]:
    return {
        "scores": {},
        "model": model,
        "resolved_model": resolved,
        "n_candidates": n_candidates,
        "tokens": tokens,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "transport": transport,
        "error": f"{type(exc).__name__}: {exc}",
    }


def _score_decisions(query: str, chunks: list, model: str) -> dict[str, Any]:
    """Mercury Decide over ``/api/alpha/decisions``: one ``noul`` per candidate.

    The Decisions API answers typed questions with calibrated probabilities
    instead of generated text — no JSON parsing, no fence variance, no
    reasoning-token truncation, and output is free.
    """
    import httpx

    state = {
        "query": query,
        "candidates": [
            {
                "act": getattr(c, "act_name", None),
                "section": getattr(c, "section_number", None),
                "snippet": (getattr(c, "text", "") or "")[:_SNIPPET_CHARS],
            }
            for c in chunks
        ],
    }
    questions = {
        f"c{i}": {
            "type": "noul",
            "instructions": _DECISION_INSTR.format(i=i),
            "criteria": _DECISION_CRITERIA,
        }
        for i in range(len(chunks))
    }
    body = {"model": model, "state": state, "questions": questions}

    started = time.monotonic()
    resolved: str | None = None
    tokens = 0
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=TIMEOUT_S, verify=False) as client:
                resp = client.post(DECISIONS_URL, headers=_headers(), json=body)
                data = resp.json()
            if resp.status_code >= 400 or "error" in data:
                raise RuntimeError(f"HTTP {resp.status_code}: {data.get('error')}")
            answers = data.get("answers") or {}
            scores: dict[str, float] = {}
            for i, c in enumerate(chunks):
                answer = answers.get(f"c{i}")
                if not answer or "noul" not in answer:
                    raise ValueError(f"missing noul answer for candidate {i}")
                scores[str(c.chunk_id)] = max(0.0, min(1.0, float(answer["noul"])))
            usage = data.get("usage") or {}
            resolved = data.get("model") or None
            tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
            return {
                "scores": scores,
                "model": model,
                "resolved_model": resolved,
                "n_candidates": len(chunks),
                "tokens": tokens,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "transport": "decisions",
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 - record and retry, never raise
            last_exc = exc
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2**attempt)
    return _error_record(
        model, len(chunks), started, last_exc, transport="decisions", resolved=resolved, tokens=tokens
    )


def _score_chat(query: str, chunks: list, model: str) -> dict[str, Any]:
    """Chat-completions transport (chat-type decision models — none
    sanctioned today; kept generic); never raises.

    Rejects responses a router resolved to a blocked backend (deepseek)
    and retries — a retry may route elsewhere; exhaustion is an error
    record, not a silent score.  Keep-map keys are normalized to bare
    digits (backends vary: ``"0"``, ``"[0]"``).
    """
    import httpx

    candidates = [_candidate(c, i) for i, c in enumerate(chunks)]
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _render_user_prompt(query, candidates)},
        ],
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.1,
    }
    headers = _headers()

    started = time.monotonic()
    resolved: str | None = None
    tokens = 0
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=TIMEOUT_S, verify=False) as client:
                resp = client.post(
                    (os.environ.get("AI_ASSISTANT_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
                    + "/chat/completions",
                    headers=headers,
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
            message = data["choices"][0].get("message", {})
            content = (message.get("content") or "").strip()
            if not content:
                # Reasoning-only responses: content empty, everything in
                # ``reasoning``.  Rarely JSON, but it is the only candidate
                # — parse it or retry (a retry may hit another backend).
                content = (message.get("reasoning") or "").strip()
            resolved = data.get("model") or None
            tokens = data.get("usage", {}).get("total_tokens", 0)
            if resolved and any(b in resolved.lower() for b in BLOCKED_BACKENDS):
                raise ValueError(f"blocked backend {resolved!r} (only {', '.join(ALLOWED_DECISION_MODELS)} are sanctioned)")
            keep = parse_json_object(content).get("keep")
            if not isinstance(keep, dict):
                raise ValueError("decision response missing keep map")
            # Normalize keys to bare digits — backends emit "0", "[0]", " 0 ".
            norm = {}
            for k, v in keep.items():
                digits = "".join(ch for ch in str(k) if ch.isdigit())
                if digits:
                    norm[digits] = v
            scores: dict[str, float] = {}
            for i, c in enumerate(candidates):
                if str(i) in norm:
                    scores[str(c["chunk_id"])] = max(0.0, min(1.0, float(norm[str(i)])))
            if len(scores) < len(candidates):
                # Partial map: flag the shortfall so a sweep can exclude the
                # question instead of silently shrugging.
                raise ValueError(f"decision response covered {len(scores)}/{len(candidates)} candidates")
            return {
                "scores": scores,
                "model": model,
                "resolved_model": resolved,
                "n_candidates": len(candidates),
                "tokens": tokens,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "transport": "chat",
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 - record and retry, never raise
            last_exc = exc
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2**attempt)
    return _error_record(
        model, len(candidates), started, last_exc, transport="chat", resolved=resolved, tokens=tokens
    )


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


def load_keep_scores(model: str | None = None) -> dict[str, dict[str, Any]]:
    """qid → last cached score record (append-mode file, last wins).

    When *model* is given only records scored by that model are returned —
    the file holds every model ever run (jev-chat, mercury, …) and a sweep
    must never mix scorers within one calibration.  Unparsable lines
    (an interleaved append from a crashed run) are skipped with a warning,
    never a crash — the affected question simply re-scores.
    """
    out: dict[str, dict[str, Any]] = {}
    if not SCORE_CACHE.exists():
        return out
    with open(SCORE_CACHE, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                print(f"warning: skipping corrupt cache line {lineno} in {SCORE_CACHE}", file=sys.stderr)
                continue
            if model is not None and rec.get("model") != model:
                continue
            out[str(rec.get("question_id"))] = rec
    return out


#: Serializes appends — concurrent thread flushes can interleave mid-line.
_CACHE_LOCK = Lock()


def append_keep_score(record: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _CACHE_LOCK, open(SCORE_CACHE, "a", encoding="utf-8") as f:
        f.write(line)


def ensure_scores(
    pools: dict[str, list],
    questions: dict[str, Any],
    *,
    qids: list[str] | None = None,
    refresh: bool = False,
    limit: int = 0,
    model: str | None = None,
) -> tuple[dict[str, dict[str, Any]], int]:
    """Score any uncached questions; return ``(qid -> record, n_scored_now)``.

    Cached success records are reused unless *refresh*; cached error
    records are retried.  Only records scored by *model* count as cached
    (the file may hold other models' runs).  Appends each new record
    immediately so an interrupted run keeps its partial work.
    """
    model = model or decision_model()
    cache = load_keep_scores(model)
    todo = [
        qid
        for qid in (qids if qids is not None else sorted(pools))
        if qid in pools
        and len(pools[qid]) >= 2
        and questions.get(qid)
        and (refresh or qid not in cache or cache[qid].get("error"))
    ]
    if limit:
        todo = todo[:limit]
    if not todo:
        return cache, 0

    sem = Semaphore(SCORE_WORKERS)

    def one(qid: str):
        with sem:
            rec = score_pool(questions[qid].question, pools[qid], model=model)
        rec["question_id"] = qid
        rec["query"] = questions[qid].question
        append_keep_score(rec)
        return qid, rec

    with ThreadPoolExecutor(max_workers=SCORE_WORKERS) as ex:
        for fut in as_completed([ex.submit(one, qid) for qid in todo]):
            qid, rec = fut.result()
            cache[qid] = rec
    return cache, len(todo)
