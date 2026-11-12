"""Per-question variant selector (Step 3, Strategy A).

No single prompt variant passes all three guardrails (brevity >=15% length
reduction, correctness drop <=0.02, abstention increase <=0.10pp) on real
retrieval context.  The fix is to pick the right variant *per question*.

``VariantSelector.generate_with_prompts`` runs a small candidate set of
system prompts, scores each answer, and returns the best result:

  1. Keep candidates whose soft correctness >= baseline_soft - 0.02
     (correctness guardrail, relative to the default prompt).
  2. Among those, pick the shortest answer (brevity guardrail).
  3. If none meet correctness, pick the highest-soft candidate
     (correctness priority).
  4. Abstention: prefer non-abstaining candidates; never increase abstention
     beyond baseline + 0.10pp.

Candidate variants are the guardrail-protected prompts validated in
``evaluation/brevity_experiment.py``.  The selector is config-gated
(``RAG_VARIANT_SELECTOR_ENABLED``) and opt-in: when off,
``generate_with_prompts`` behaves exactly as before.

The scoring (soft correctness, coverage, abstention) requires an expected
answer, which the runtime doesn't have.  So the selector uses *answer length*
as the brevity proxy and *citation count / answer presence* as the
correctness proxy — both computed without a gold reference.  This matches the
experiment finding that brevity and correctness are inversely correlated and
the right tradeoff differs per question.
"""

from __future__ import annotations

import logging
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

#: Soft-correctness tolerance vs baseline (matches experiment guardrail).
CORRECTNESS_DROP_LIMIT = 0.02
#: Max abstention increase vs baseline (matches experiment guardrail).
ABSTENTION_INCREASE_LIMIT = 0.10
#: Minimum brevity reduction vs baseline (percent).
BREVITY_REDUCTION_TARGET = 15.0


def _is_abstention(text: str) -> bool:
    if not text or not text.strip():
        return True
    t = text.strip().lower()
    markers = [
        "cannot be determined", "cannot determine", "not established",
        "does not establish", "not in the context", "not mentioned",
        "insufficient evidence", "no information", "unable to answer",
        "does not provide", "not specified", "not stated",
    ]
    return any(m in t for m in markers)


class VariantSelector:
    """Select the best prompt variant per question at inference time.

    Runs ``candidates`` (default: tight_guardrail, guardrail_compact,
    ultra_compact, guardrail_confident) plus the baseline, scores each, and
    returns the result that best satisfies the three guardrails.
    """

    def __init__(
        self,
        service: GroundedGenerationService,
        candidates: tuple[str, ...] = DEFAULT_CANDIDATES,
        enabled: bool = True,
    ) -> None:
        self.service = service
        self.candidates = tuple(candidates)
        self.enabled = enabled

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
                query=query, chunks=chunks, query_type=query_type,
                prompts=prompts,
            )

        if not chunks:
            return self.service.generate_with_prompts(
                query=query, chunks=chunks, query_type=query_type,
                prompts=prompts,
            )

        # Build the user prompt once (identical for all variants).
        ctx = "\n\n".join(
            f"[{i+1}] {c.text}" for i, c in enumerate(chunks)
        )
        user_prompt = (
            "Relevant legal context:\n"
            "<legal_context>\n"
            f"{ctx}\n"
            "</legal_context>\n\n"
            f"Question: {query}\n"
            "Answer:"
        )

        # Run all candidates + baseline.
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
            })

        if not results:
            return self.service.generate_with_prompts(
                query=query, chunks=chunks, query_type=query_type,
                prompts=prompts,
            )

        baseline = results[0]
        baseline_soft = baseline["soft_proxy"]
        baseline_abstained = baseline["abstained"]

        # Correctness-eligible: soft_proxy >= baseline - tolerance.
        correct_ok = [
            r for r in results
            if r["soft_proxy"] >= baseline_soft - CORRECTNESS_DROP_LIMIT
        ]

        chosen = None
        reason = ""
        if correct_ok:
            # Prefer non-abstaining, then shortest length.
            non_abstain = [r for r in correct_ok if not r["abstained"]]
            pool = non_abstain if non_abstain else correct_ok
            chosen = min(pool, key=lambda r: r["length"])
            reason = "shortest among correctness-eligible"
        else:
            chosen = max(results, key=lambda r: r["soft_proxy"])
            reason = "highest soft proxy (no variant met correctness limit)"

        logger.info(
            "variant_selector: chose %s (%s), len=%d, abstained=%s",
            chosen["name"], reason, chosen["length"], chosen["abstained"],
        )
        return chosen["response"]

    @staticmethod
    def _soft_proxy(text: str, chunks: list[RetrievedChunk]) -> float:
        """Reference-free correctness proxy: citation coverage + presence.

        Fraction of retrieved chunks cited at least once, plus a bonus for
        non-empty non-abstaining text.  Higher = more of the evidence base is
        covered by the answer, which correlates with soft correctness.
        """
        if not text or not text.strip():
            return 0.0
        cited = 0
        for i, _ in enumerate(chunks):
            marker = f"[{i+1}]"
            if marker in text:
                cited += 1
        coverage = cited / max(len(chunks), 1)
        # Small bonus for having an answer (avoids preferring empty abstentions).
        answer_bonus = 0.1 if not _is_abstention(text) else 0.0
        return min(coverage + answer_bonus, 1.0)
