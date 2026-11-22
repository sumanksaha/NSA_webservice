"""Structured legal reasoner — intermediate IR before answer generation (Phase 1).

Roadmap §32.2: instead of jumping from a large context to the final answer,
the pipeline first builds a :class:`StructuredLegalArgument`
(issue → provisions → definitions → rules → conditions → exceptions →
fact mapping → conclusion → citations).  The final answer is generated
*from* this object, so hidden reasoning at the generation stage shrinks.

``StructuredReasoner`` calls the injected LLM client (same ``call()``
seam as :class:`GroundedLLMClient`) and parses the JSON IR.  Unparseable
output degrades to :meth:`StructuredLegalArgument.empty` — never raises
on caller-controlled input.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Self

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: Prompt shapes must match :class:`StructuredLegalArgument` exactly: ``reason``
#: fail-closes to an empty skeleton on any validation error, so a key the model
#: renders in the wrong shape silently disables two-pass reasoning.  Both shapes
#: below were learned from live output (2026-10-14, ``poolside/laguna-s-2.1``):
#: the model emitted ``definitions_applied`` as an array of definitions and
#: ``exceptions_considered`` as an array of strings, which the ``dict``/``dict``
#: fields reject — while ``condition_evaluations``, whose per-entry keys this
#: prompt spells out, parsed fine.
REASONER_SYSTEM_PROMPT = (
    "You are a legal reasoning assistant specialised in Indian law. "
    "Given the question and the legal evidence below, produce a structured "
    "legal analysis as a single JSON object with exactly these keys: "
    "issue, applicable_provisions, definitions_applied, legal_rules, "
    "exceptions_considered, condition_evaluations, cross_references_followed, "
    "conflicts_or_hierarchy, derived_conclusion, supporting_citations, "
    "uncertainties. Shapes: issue and derived_conclusion are strings; "
    "applicable_provisions, legal_rules, cross_references_followed, "
    "conflicts_or_hierarchy, supporting_citations and uncertainties are arrays "
    "of strings; definitions_applied is an OBJECT mapping each defined term to "
    "its definition text, never an array; exceptions_considered is an array of "
    "OBJECTS with rule, exception and applies keys, never bare strings. "
    "Each condition_evaluations entry has condition_id, "
    "condition_text, fact_reference, status "
    "(one of satisfied, not_satisfied, unknown) and explanation. "
    "If a required condition has no supporting fact, mark it unknown — "
    "never assume it satisfied. Output JSON only."
)


#: Output budget for the reasoner call.  1024 (the pre-W3 default) truncated
#: the IR on every live call tried: a complete argument for a 4.5k-token
#: evidence prompt measures 1486 completion tokens, so the JSON was cut
#: mid-array, validation failed, and the pipeline silently shipped the
#: fail-closed skeleton instead of two-pass reasoning.  3072 fixed that small
#: prompt but still truncated on the benchmark's 35k-char packed contexts
#: (2026-10-20: Q073 returned ``finish_reason='length'`` at 3072, ``'stop'``
#: at 4096; the complete IR measured 3.6k completion tokens).  8192 keeps
#: headroom over the measured size while still bounding a runaway generation;
#: the model stops at the JSON's closing brace, so the budget is a ceiling,
#: not a spend.
DEFAULT_MAX_TOKENS = 8192


class ConditionEvaluation(BaseModel):
    """One fact-to-condition mapping (roadmap §9)."""

    condition_id: str
    condition_text: str
    fact_reference: str = Field(description="Facts from question/context bearing on this condition")
    status: Literal["satisfied", "not_satisfied", "unknown"]
    explanation: str


class StructuredLegalArgument(BaseModel):
    """Intermediate structured legal reasoning object (roadmap §32.2)."""

    issue: str = Field(description="The primary legal issue or question to be determined")
    applicable_provisions: list[str] = Field(default_factory=list)
    definitions_applied: dict[str, str] = Field(default_factory=dict)
    legal_rules: list[str] = Field(default_factory=list)
    exceptions_considered: list[dict[str, Any]] = Field(default_factory=list)
    condition_evaluations: list[ConditionEvaluation] = Field(default_factory=list)
    cross_references_followed: list[str] = Field(default_factory=list)
    conflicts_or_hierarchy: list[str] = Field(default_factory=list)
    derived_conclusion: str = ""
    supporting_citations: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @classmethod
    def empty(cls, issue: str) -> Self:
        """Fail-closed skeleton: no claims, explicit uncertainty."""
        return cls(issue=issue, uncertainties=["reasoning unavailable — argument not established"])


def build_reasoning_prompt(question: str, context: str, directive: str = "") -> tuple[str, str]:
    """Render the (system, user) prompt pair for the reasoning call.

    ``directive`` is the optional W4 query-type instruction.  When supplied it
    is appended to the evidence, before the JSON ask, so the IR covers the
    type-specific elements the final memo must carry (authority, penalty
    structure, procedure, etc.).  Empty keeps the prompt byte-identical to the
    pre-W4 behaviour.
    """
    user = f"Question:\n{question}\n\nLegal evidence:\n{context}\n"
    if directive:
        user += f"\n{directive}\n"
    user += "\nStructured analysis (JSON only):"
    return REASONER_SYSTEM_PROMPT, user


class StructuredReasoner:
    """Build a :class:`StructuredLegalArgument` via the LLM seam."""

    def __init__(
        self,
        llm_client: Any | None = None,
        temperature: float = 0.1,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if llm_client is None:
            from app.shared.llm_client import get_llm_client

            llm_client = get_llm_client()
        self.llm_client = llm_client
        self.temperature = temperature
        self.max_tokens = max_tokens

    def reason(self, question: str, context: str, directive: str = "") -> StructuredLegalArgument:
        """Reason over question + evidence; fall back to an empty skeleton."""
        system, user = build_reasoning_prompt(question, context, directive=directive)
        try:
            response = self.llm_client.call(system, user, temperature=self.temperature, max_tokens=self.max_tokens)
            text = (response.text or "").strip()
            if not text:
                raise ValueError("empty LLM response")
            return StructuredLegalArgument.model_validate_json(_extract_json(text))
        except Exception as exc:
            logger.warning("StructuredReasoner fallback: %s", exc)
            return StructuredLegalArgument(
                issue=question,
                uncertainties=[f"reasoning unparseable ({exc}) — argument not established"],
            )


def _extract_json(text: str) -> str:
    """Pull the first complete JSON object out of surrounding prose.

    Uses ``json.JSONDecoder.raw_decode`` so a trailing code fence, an extra
    sentence, or a second JSON blob after the object does not make
    ``model_validate_json`` fail with "trailing characters".  Falls back to
    the old brace-scan when the text is not valid JSON at all.
    """
    start = text.find("{")
    if start == -1:
        return text
    try:
        _, end = json.JSONDecoder().raw_decode(text, start)
        return text[start:end]
    except (ValueError, json.JSONDecodeError):
        last = text.rfind("}")
        if last != -1 and last > start:
            return text[start : last + 1]
        return text
