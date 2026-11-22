"""Per-question variant selector (Step 3, Strategy A).

No single prompt variant passes all three guardrails (brevity >=15% length
reduction, correctness drop <=0.02, abstention increase <=0.10pp) on real
retrieval context.  The fix is to pick the right variant *per question*.

``VariantSelector.generate_with_prompts`` supports two modes:

* **Multi-candidate mode** (default, no JEV): runs a small candidate set of
  system prompts, scores each answer, and returns the best result:
    1. Keep candidates whose source-content retention is within
       ``CONTENT_EQUIVALENCE_BAND`` of the best candidate.
    2. Prefer candidates that answered over refusals.
    3. Among content-equivalent candidates, pick the shortest (brevity).

* **JEV-guided mode** (``RAG_VARIANT_SELECTOR_JEV=true``): asks the sanctioned
  JEV decision model (``typesafe/jev-1.13``) via OpenRouter's Decisions API to
  score each candidate variant, then generates *one* answer with the top-scored
  variant.  Fail-closed: if JEV is disabled, unavailable, kill-switched, or
  returns unusable scores, fall back to running all candidates
  (multi-candidate mode) and never raise.

The scoring requires an expected answer, which the runtime doesn't have.  So
the selector uses a length-invariant source-token overlap as the content
retention proxy and raw answer length only as the final tiebreak — both
computed without a gold reference.  Citation breadth is retained as a
diagnostic signal only: it rewards verbosity and must not decide selection.

``typesafe/jev-router`` is banned and never used (enforced by
``DecisionModelClient``).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.rag.generation.grounded_service import GroundedGenerationService
from app.rag.retrieval.result import RetrievedChunk

logger = logging.getLogger(__name__)

#: Candidate system prompts (validated in evaluation/brevity_experiment.py).
#: Each is guardrail-protected: answer-if-evidence clause + one cited sentence
#: per conclusion + no scaffolding/background.
CANDIDATE_SYSTEM_PROMPTS: dict[str, str] = {
    "baseline": (
        "You are a legal assistant specialised in the Food Safety and Standards "
        "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
        "Work in two steps. Step 1 — quote: first extract the short passages "
        "that bear on the question, quoting them verbatim. Step 2 — answer: "
        "give a concise, legally precise answer derived strictly from those "
        "quotes. Citation contract: (1) every material legal claim carries at "
        "least one [n] citation; (2) each [n] maps to a source shown in the "
        "context — never cite a source that is not shown; (3) never fabricate "
        "facts. If the answer is not in the context, state so clearly and "
        "qualify what is unknown instead of guessing. "
        "RULE: No answer text may appear before at least one [n] citation "
        "from the shown context is provided."
    ),
    "tight_guardrail": (
        "You are a legal assistant specialised in the Food Safety and Standards "
        "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
        "Give a short, direct answer in one sentence per conclusion, with at least "
        "one [n] citation per material claim. Do not add background, restate the "
        "question, summarise, or include Step 1/Step 2 labels. "
        "RULE: If the context provides enough evidence to answer, give the answer "
        "— do not abstain or say 'not in context' unless the context truly lacks "
        "the answer. State supported conclusions directly with citations rather "
        "than hedging. If the answer is not in the context, say so briefly. Never "
        "fabricate facts."
    ),
    "guardrail_compact": (
        "You are a legal assistant specialised in the Food Safety and Standards "
        "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
        "Work in two steps. Step 1 — quote: first extract the short passages "
        "that bear on the question, quoting them verbatim (evidence only, not "
        "the answer). Step 2 — answer: give exactly one cited sentence per "
        "distinct legal conclusion the quotes support; state every supported "
        "conclusion completely and precisely in one sentence each, then stop. "
        "Do not add background, do not restate the question, do not summarise, "
        "do not state any conclusion twice. Step 1 quotes are evidence only; "
        "the answer is Step 2 alone. "
        "IMPORTANT: If the context provides enough evidence to answer the "
        "question, give the answer — do not abstain or say 'not in context' "
        "unless the context truly does not contain the answer. "
        "Citation contract: (1) every material legal claim carries at least one "
        "[n] citation; (2) each [n] maps to a source shown in the context "
        "(e.g. [1], [2]) — never cite a source that is not shown; "
        "(3) never fabricate facts. If the answer is not in the context, state "
        "so clearly and qualify what is unknown instead of guessing. "
        "RULE: No answer text may appear before at least one [n] citation "
        "from the shown context is provided. Keep both steps: never skip Step 2."
    ),
    "ultra_compact": (
        "You are a legal assistant specialised in the Food Safety and Standards "
        "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
        "Give a short, direct answer in one or two sentences, with at least one "
        "[n] citation per material claim. Do not add background, do not restate "
        "the question, do not summarise, do not include Step 1/Step 2 labels, do "
        "not quote the context at length. If the context provides enough evidence "
        "to answer the question, give the answer — do not abstain or say 'not in "
        "context' unless the context truly does not contain the answer. If the "
        "answer is not in the context, state so briefly and qualify what is "
        "unknown. Never fabricate facts."
    ),
    "guardrail_confident": (
        "You are a legal assistant specialised in the Food Safety and Standards "
        "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
        "Give a short, direct answer in one or two sentences per conclusion, with "
        "at least one [n] citation per material claim. Do not add background, do "
        "not restate the question, do not summarise, do not include Step 1/Step 2 "
        "labels. If the context provides enough evidence to answer the question, "
        "give the answer — do not abstain or say 'not in context' unless the "
        "context truly does not contain the answer. When the evidence supports a "
        "conclusion, state it directly with its citation rather than hedging. If "
        "the answer is not in the context, state so briefly and qualify what is "
        "unknown. Never fabricate facts."
    ),
}

#: Default candidate set run at inference (kept small to control LLM cost).
DEFAULT_CANDIDATES: tuple[str, ...] = (
    "tight_guardrail",
    "guardrail_compact",
    "ultra_compact",
    "guardrail_confident",
)

#: Max content-retention gap treated as noise (evaluation/noise_floor.py).
CONTENT_EQUIVALENCE_BAND = 0.0815
#: Max abstention increase vs baseline (matches experiment guardrail).
ABSTENTION_INCREASE_LIMIT = 0.10
#: Minimum brevity reduction vs baseline (percent).
BREVITY_REDUCTION_TARGET = 15.0
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: JEV variant-selection instructions — one noul question per candidate variant.
_JEV_VARIANT_INSTR = (
    "Judge whether answer variant `candidates[{i}]` is the best choice for "
    "answering `query` from `legal_context`.  Pick the variant that gives the "
    "shortest correct answer while still covering the evidence actually present "
    "in the context.  Do not pick a variant that over-abstains (refuses to answer "
    "when the context does contain the answer) or that fabricates.  Score each "
    "variant independently."
)
_JEV_VARIANT_CRITERIA = {
    "true": "Pick: this variant gives a concise, well-supported answer from the context.",
    "false": "Drop: this variant over-abstains, fabricates, or is overly verbose.",
}

#: Decisions API (typed state + questions -> calibrated probabilities).
_JEV_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
#: Questions per HTTP call.  Variant set is small (<=5), so one call suffices.
_JEV_BATCH = 10
#: Attempts per Decisions-API call (transient 429/5xx get one backoff).
_JEV_ATTEMPTS = 3


def _is_abstention(text: str) -> bool:
    if not text or not text.strip():
        return True
    t = text.strip().lower()
    markers = [
        "cannot be determined",
        "cannot determine",
        "not established",
        "does not establish",
        "not in the context",
        "not mentioned",
        "insufficient evidence",
        "no information",
        "unable to answer",
        "does not provide",
        "not specified",
        "not stated",
    ]
    return any(m in t for m in markers)


class VariantSelector:
    """Select the best prompt variant per question at inference time.

    Two modes:

    * Multi-candidate (default, JEV off): run every candidate + baseline,
      score each, return the best per guardrails.
    * JEV-guided (``RAG_VARIANT_SELECTOR_JEV=true``): ask JEV to score each
      candidate variant, generate *one* answer with the top-scored variant,
      fail-closed to multi-candidate on any JEV problem.
    """

    def __init__(
        self,
        service: GroundedGenerationService,
        candidates: tuple[str, ...] = DEFAULT_CANDIDATES,
        enabled: bool = True,
        use_jev: bool = False,
        jev_model: str = "typesafe/jev-1.13",
    ) -> None:
        self.service = service
        self.candidates = tuple(candidates)
        self.enabled = enabled
        self.use_jev = use_jev
        self.jev_model = jev_model

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def generate_with_prompts(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str = "",
        prompts: tuple[str, str] | None = None,
    ) -> Any:
        """Run candidate variants and return the best per guardrails.

        When ``prompts`` is supplied (explicit caller choice), use it directly
        and skip selection.  Otherwise run ``candidates`` + baseline and apply
        the guardrail selection rule.
        """
        if prompts is not None or not self.enabled:
            return self.service.generate_with_prompts(
                query=query,
                chunks=chunks,
                query_type=query_type,
                prompts=prompts,
            )

        if not chunks:
            return self.service.generate_with_prompts(
                query=query,
                chunks=chunks,
                query_type=query_type,
                prompts=prompts,
            )

        if self.use_jev:
            return self._generate_jev_guided(query, chunks, query_type)

        return self._generate_multi_candidate(query, chunks, query_type)

    # ------------------------------------------------------------------ #
    # Multi-candidate mode (existing behavior)
    # ------------------------------------------------------------------ #
    def _generate_multi_candidate(self, query: str, chunks: list[RetrievedChunk], query_type: str) -> Any:
        user_prompt = self._render_user_prompt(query, chunks)
        results: list[dict[str, Any]] = []
        for name in ("baseline",) + self.candidates:
            system = CANDIDATE_SYSTEM_PROMPTS[name]
            try:
                resp = self.service.generate_with_prompts(
                    query=query,
                    chunks=chunks,
                    query_type=query_type,
                    prompts=(system, user_prompt),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("variant %s failed: %s", name, exc)
                continue
            text = resp.answer or ""
            results.append({
                "name": name,
                "response": resp,
                "text": text,
                "length": len(text),
                "abstained": _is_abstention(text),
                "soft_proxy": self._soft_proxy(text, chunks),
                "content_proxy": self._content_proxy(text, chunks),
            })

        if not results:
            return self.service.generate_with_prompts(
                query=query,
                chunks=chunks,
                query_type=query_type,
                prompts=None,
            )

        return self._pick_best(results)

    # ------------------------------------------------------------------ #
    # JEV-guided mode
    # ------------------------------------------------------------------ #
    def _generate_jev_guided(self, query: str, chunks: list[RetrievedChunk], query_type: str) -> Any:
        user_prompt = self._render_user_prompt(query, chunks)
        started = time.monotonic()

        try:
            from app.shared.config import cfg
            from app.shared.llm_client import _increment_call_count, _log_llm_call, llm_enabled

            if not llm_enabled():
                raise RuntimeError("LLM calls are disabled by kill switch")
            key = (
                __import__("os").environ.get("AI_ASSISTANT_API_KEY")
                or __import__("os").environ.get("OPENROUTER_API_KEY")
                or ""
            )
            if not key:
                raise RuntimeError("OPENROUTER_API_KEY not configured")
            _increment_call_count()
            _log_llm_call(
                provider="openrouter",
                model=self.jev_model,
                call_site="variant_selector._generate_jev_guided",
            )

            import httpx

            headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
            variants = ("baseline",) + self.candidates
            candidates = [
                {
                    "index": i,
                    "variant": name,
                    "prompt": CANDIDATE_SYSTEM_PROMPTS[name][:400],
                }
                for i, name in enumerate(variants)
            ]
            state = {
                "query": query,
                "candidates": candidates,
                "context_snippet": chunks[0].text[:300] if chunks else "",
            }
            questions = {
                f"v{i}": {
                    "type": "noul",
                    "instructions": _JEV_VARIANT_INSTR.format(i=i),
                    "criteria": _JEV_VARIANT_CRITERIA,
                }
                for i in range(len(variants))
            }
            probs: dict[int, float] = {}
            for start in range(0, len(variants), _JEV_BATCH):
                batch_variants = variants[start : start + _JEV_BATCH]
                batch_candidates = candidates[start : start + _JEV_BATCH]
                batch_state = dict(state)
                batch_state["candidates"] = batch_candidates
                batch_questions = {f"v{i}": questions[f"v{i}"] for i in range(start, start + len(batch_variants))}
                body = {
                    "model": self.jev_model,
                    "state": batch_state,
                    "questions": batch_questions,
                }
                last: Exception | None = None
                data = None
                for attempt in range(_JEV_ATTEMPTS):
                    try:
                        with httpx.Client(timeout=cfg.decision_timeout) as client:
                            resp = client.post(_JEV_DECISIONS_URL, headers=headers, json=body)
                            data = resp.json()
                        if resp.status_code >= 400 or "error" in data:
                            raise RuntimeError(f"HTTP {resp.status_code}: {data.get('error')}")
                        break
                    except Exception as exc:
                        last = exc
                        if attempt < _JEV_ATTEMPTS - 1:
                            time.sleep(2**attempt)
                if data is None:
                    raise last or RuntimeError("jev decisions call failed")
                answers = data.get("answers") or {}
                for i in range(start, start + len(batch_variants)):
                    answer = answers.get(f"v{i}")
                    if not answer or "noul" not in answer:
                        raise ValueError(f"missing noul answer for variant {i}")
                    probs[i] = max(0.0, min(1.0, float(answer["noul"])))
        except Exception as exc:  # noqa: BLE001 — fail closed to multi-candidate
            logger.warning(
                "variant_selector: JEV failed (%s) — falling back to multi-candidate",
                exc,
            )
            return self._generate_multi_candidate(query, chunks, query_type)

        latency_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "variant_selector: JEV probabilities %s (%dms)",
            {variants[i]: round(p, 3) for i, p in probs.items()},
            latency_ms,
        )

        # Pick top-scored variant; generate one answer.
        best_index = max(probs, key=lambda i: probs[i])
        best_name = variants[best_index]
        best_system = CANDIDATE_SYSTEM_PROMPTS[best_name]
        try:
            resp = self.service.generate_with_prompts(
                query=query,
                chunks=chunks,
                query_type=query_type,
                prompts=(best_system, user_prompt),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "variant_selector: generation for %s failed (%s) — multi-candidate",
                best_name,
                exc,
            )
            return self._generate_multi_candidate(query, chunks, query_type)

        text = resp.answer or ""
        chosen_result = {
            "name": best_name,
            "response": resp,
            "text": text,
            "length": len(text),
            "abstained": _is_abstention(text),
            "soft_proxy": self._soft_proxy(text, chunks),
            "content_proxy": self._content_proxy(text, chunks),
        }

        # Sanity: if the chosen answer abstained but baseline would answer,
        # log a trace flag (advisory only; do not block serving).
        try:
            base_resp = self.service.generate_with_prompts(
                query=query,
                chunks=chunks,
                query_type=query_type,
                prompts=(CANDIDATE_SYSTEM_PROMPTS["baseline"], user_prompt),
            )
            base_text = base_resp.answer or ""
            if chosen_result["abstained"] and not _is_abstention(base_text):
                logger.info(
                    "variant_selector: JEV pick %s abstained but baseline answered (advisory trace)",
                    best_name,
                )
        except Exception:  # noqa: BLE001 — advisory only
            pass

        return chosen_result["response"]

    # ------------------------------------------------------------------ #
    # Selection logic (shared)
    # ------------------------------------------------------------------ #
    def _pick_best(self, results: list[dict[str, Any]]) -> Any:
        best_content = max(r["content_proxy"] for r in results)
        content_ok = [r for r in results if r["content_proxy"] >= best_content - CONTENT_EQUIVALENCE_BAND]
        non_abstain = [r for r in content_ok if not r["abstained"]]
        pool = non_abstain or content_ok
        chosen = min(pool, key=lambda r: r["length"])
        reason = (
            "shortest among content-equivalent answers"
            if non_abstain
            else "shortest among content-equivalent candidates"
        )

        logger.info(
            "variant_selector: chose %s (%s), len=%d, abstained=%s, content=%.3f",
            chosen["name"],
            reason,
            chosen["length"],
            chosen["abstained"],
            chosen["content_proxy"],
        )
        return chosen["response"]

    @staticmethod
    def _content_proxy(text: str, chunks: list[RetrievedChunk]) -> float:
        """Fraction of distinct source tokens retained by an answer."""
        if not text or not text.strip():
            return 0.0
        context_tokens: set[str] = set()
        for chunk in chunks:
            context_tokens.update(_TOKEN_RE.findall((chunk.text or "").lower()))
        if not context_tokens:
            return 0.0
        answer_tokens = set(_TOKEN_RE.findall(text.lower()))
        return len(answer_tokens & context_tokens) / len(context_tokens)

    @staticmethod
    def _soft_proxy(text: str, chunks: list[RetrievedChunk]) -> float:
        """Diagnostic citation coverage: how much of the evidence is cited.

        Kept for traceability and evaluation comparisons only.  Selection uses
        ``_content_proxy`` because citation breadth rewards verbosity while the
        brevity tiebreak rewards the opposite.
        """
        if not text or not text.strip():
            return 0.0
        cited = 0
        for i, _ in enumerate(chunks):
            marker = f"[{i + 1}]"
            if marker in text:
                cited += 1
        coverage = cited / max(len(chunks), 1)
        answer_bonus = 0.1 if not _is_abstention(text) else 0.0
        return min(coverage + answer_bonus, 1.0)

    @staticmethod
    def _render_user_prompt(query: str, context_chunks: list[RetrievedChunk]) -> str:
        ctx = "\n\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(context_chunks))
        return f"Relevant legal context:\n<legal_context>\n{ctx}\n</legal_context>\n\nQuestion: {query}\nAnswer:"
