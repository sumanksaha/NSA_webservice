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
    DEFAULT_MAX_TOKENS,
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

    def test_prompt_pins_every_field_shape(self):
        # reason() fail-closes to a skeleton on any validation error, so a
        # shape the prompt does not pin is a shape the model is free to get
        # wrong.  Both keys asserted here were emitted in the wrong shape by
        # the live model (2026-10-14) and silently disabled two-pass reasoning.
        system, _ = build_reasoning_prompt("Q", "ctx")
        assert "definitions_applied is an OBJECT mapping" in system
        assert "never an array" in system
        assert "exceptions_considered is an array of OBJECTS" in system
        assert "never bare strings" in system

    def test_prompt_still_pins_condition_entry_keys(self):
        system, _ = build_reasoning_prompt("Q", "ctx")
        for key in ("condition_id", "condition_text", "fact_reference", "status", "explanation"):
            assert key in system


class TestReasonerCallBudget:
    """The IR must fit the token budget, or the IR is never built."""

    def test_default_budget_covers_a_complete_ir(self):
        # Measured 2026-10-14: a complete argument for a 4.5k-token evidence
        # prompt is 1486 completion tokens, which the old 1024 default cut
        # mid-JSON on every call (fallback skeleton, always).
        assert DEFAULT_MAX_TOKENS >= 2048
        assert StructuredReasoner(llm_client=FakeLLM(VALID_ARGUMENT_JSON)).max_tokens == DEFAULT_MAX_TOKENS

    def test_reason_passes_budget_and_temperature_to_the_client(self):
        seen: dict = {}

        class _Recorder(FakeLLM):
            def call(self, system_prompt: str, user_prompt: str, **kwargs: object) -> object:
                seen.update(kwargs)
                return super().call(system_prompt, user_prompt, **kwargs)

        StructuredReasoner(llm_client=_Recorder(VALID_ARGUMENT_JSON)).reason("Q?", "ctx")
        assert seen["max_tokens"] == DEFAULT_MAX_TOKENS
        assert seen["temperature"] == 0.1


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
