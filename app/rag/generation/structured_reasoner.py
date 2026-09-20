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

import logging
from typing import Any, Literal, Self

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

REASONER_SYSTEM_PROMPT = (
    "You are a legal reasoning assistant specialised in Indian law. "
    "Given the question and the legal evidence below, produce a structured "
    "legal analysis as a single JSON object with exactly these keys: "
    "issue, applicable_provisions, definitions_applied, legal_rules, "
    "exceptions_considered, condition_evaluations, cross_references_followed, "
    "conflicts_or_hierarchy, derived_conclusion, supporting_citations, "
    "uncertainties. Each condition_evaluations entry has condition_id, "
    "condition_text, fact_reference, status "
    "(one of satisfied, not_satisfied, unknown) and explanation. "
    "If a required condition has no supporting fact, mark it unknown — "
    "never assume it satisfied. Output JSON only."
)


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


def build_reasoning_prompt(question: str, context: str) -> tuple[str, str]:
    """Render the (system, user) prompt pair for the reasoning call."""
    user = f"Question:\n{question}\n\nLegal evidence:\n{context}\n\nStructured analysis (JSON only):"
    return REASONER_SYSTEM_PROMPT, user


class StructuredReasoner:
    """Build a :class:`StructuredLegalArgument` via the LLM seam."""

    def __init__(
        self,
        llm_client: Any | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> None:
        if llm_client is None:
            from app.rag.generation.llm_client import GroundedLLMClient

            llm_client = GroundedLLMClient()
        self.llm_client = llm_client
        self.temperature = temperature
        self.max_tokens = max_tokens

    def reason(self, question: str, context: str) -> StructuredLegalArgument:
        """Reason over question + evidence; fall back to an empty skeleton."""
        system, user = build_reasoning_prompt(question, context)
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
    """Pull the first {...} block out of surrounding prose, if any."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text
