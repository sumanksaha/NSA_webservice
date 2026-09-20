"""Tests for Phase 1 (Experiment D) — structured legal reasoner seam.

Seam S1: ``app.rag.generation.structured_reasoner`` — the
``StructuredLegalArgument`` IR (roadmap §32.2) plus ``StructuredReasoner``
which builds it via the injected LLM client.  Tests drive the public
interface with a fake LLM (same ``call()`` signature as
``GroundedLLMClient``); no network, no real model.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.rag.generation.structured_reasoner import (
    StructuredLegalArgument,
    StructuredReasoner,
    build_reasoning_prompt,
)


class FakeLLM:
    """Minimal fake at the GroundedLLMClient.call seam."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, str]] = []

    def call(self, system_prompt: str, user_prompt: str, **_: object) -> object:
        self.calls.append((system_prompt, user_prompt))
        from app.rag.generation.llm_client import GroundedLLMResponse

        return GroundedLLMResponse(text=self.text, model="fake")


VALID_ARGUMENT_JSON = """{
  "issue": "Does Section 31 require a licence for the stated food business?",
  "applicable_provisions": ["FSS_ACT::31"],
  "definitions_applied": {"food": "any article used as food"},
  "legal_rules": ["Food businesses must obtain a licence under Section 31."],
  "exceptions_considered": [{"rule": "Section 31", "exception": "none stated", "applies": false}],
  "condition_evaluations": [
    {"condition_id": "C1", "condition_text": "carries on food business",
     "fact_reference": "question states a retail shop", "status": "satisfied",
     "explanation": "retail sale is a food business"}
  ],
  "cross_references_followed": [],
  "conflicts_or_hierarchy": [],
  "derived_conclusion": "A licence is required under Section 31.",
  "supporting_citations": ["FSS_ACT::31"],
  "uncertainties": []
}"""


class TestStructuredLegalArgument:
    def test_valid_payload_parses(self):
        arg = StructuredLegalArgument.model_validate_json(VALID_ARGUMENT_JSON)
        assert arg.issue.startswith("Does Section 31")
        assert arg.condition_evaluations[0].status == "satisfied"
        assert arg.supporting_citations == ["FSS_ACT::31"]

    def test_condition_status_rejects_unknown_values(self):
        with pytest.raises(Exception):
            StructuredLegalArgument.model_validate_json(VALID_ARGUMENT_JSON.replace('"satisfied"', '"maybe"'))

    def test_empty_argument_is_valid_skeleton(self):
        arg = StructuredLegalArgument.empty("Q?")
        assert arg.issue == "Q?"
        assert arg.condition_evaluations == []
        assert arg.uncertainties != []


class TestBuildReasoningPrompt:
    def test_prompt_contains_question_and_evidence(self):
        system, user = build_reasoning_prompt("Is a licence needed?", "Section 31 text here.")
        assert "Is a licence needed?" in user
        assert "Section 31 text here." in user
        assert "JSON" in system


class TestStructuredReasoner:
    def test_reason_parses_llm_json(self):
        reasoner = StructuredReasoner(llm_client=FakeLLM(VALID_ARGUMENT_JSON))
        arg = reasoner.reason("Is a licence needed?", "Section 31 text.")
        assert isinstance(arg, StructuredLegalArgument)
        assert arg.derived_conclusion == "A licence is required under Section 31."

    def test_reason_falls_back_on_garbage(self):
        reasoner = StructuredReasoner(llm_client=FakeLLM("not json at all"))
        arg = reasoner.reason("Is a licence needed?", "Section 31 text.")
        assert isinstance(arg, StructuredLegalArgument)
        assert "Is a licence needed?" in arg.issue
        assert "unparseable" in " ".join(arg.uncertainties).lower()
