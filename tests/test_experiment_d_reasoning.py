"""Tests for Phase 1 (Experiment D) — reasoning-architecture harness seam.

Seam S3: ``evaluation.experiment_d_reasoning`` — paired conditions on the
*same* question + evidence (roadmap §21):

* A_direct — question + evidence → final answer
* B_structured — question + evidence → structured argument → final answer
* C_structured_audit — B + auditor + max 1 revision → final answer

Tests use a scripted fake LLM (pop-from-list); the budget guard, the
revision trigger and the analysis are the behavior under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from evaluation.experiment_d_reasoning import (
    CONDITIONS,
    LLMBudgetExceeded,
    analyze,
    run_condition,
)

ARG_CLEAN = """{
  "issue": "Is a licence needed?",
  "applicable_provisions": ["FSS_ACT::31"],
  "definitions_applied": {"food": "any article used as food"},
  "legal_rules": ["Licence required under Section 31."],
  "exceptions_considered": [{"rule": "Section 31", "exception": "petty-retailer exemption", "applies": false}],
  "condition_evaluations": [
    {"condition_id": "C1", "condition_text": "carries on food business",
     "fact_reference": "retail shop stated", "status": "satisfied", "explanation": "retail qualifies"}
  ],
  "cross_references_followed": [],
  "conflicts_or_hierarchy": [],
  "derived_conclusion": "A licence is required under Section 31.",
  "supporting_citations": ["FSS_ACT::31"],
  "uncertainties": []
}"""

ARG_NO_EXCEPTIONS = ARG_CLEAN.replace(
    '[{"rule": "Section 31", "exception": "petty-retailer exemption", "applies": false}]', "[]"
)

EVIDENCE_TEXTS = {
    "FSS_ACT::31": "Section 31 requires food businesses to obtain a licence. Provided that petty retailers are exempt.",
}
CONTEXT = "Section 31 requires food businesses to obtain a licence. Provided that petty retailers are exempt."


class ScriptedLLM:
    """Pop scripted responses; counts calls like the harness wrapper."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls = 0
        self.systems: list[str] = []
        self.users: list[str] = []

    def call(self, system_prompt: str, user_prompt: str, **_: object) -> object:
        from app.rag.generation.llm_client import GroundedLLMResponse

        self.calls += 1
        self.systems.append(system_prompt)
        self.users.append(user_prompt)
        return GroundedLLMResponse(text=self._texts.pop(0), model="fake")


def test_conditions_registered():
    assert CONDITIONS == ("A_direct", "B_structured", "C_structured_audit")


def test_condition_a_returns_answer():
    rec = run_condition("Q?", CONTEXT, EVIDENCE_TEXTS, ScriptedLLM(["Answer [1]."]), "A_direct")
    assert rec["answer"] == "Answer [1]."
    assert rec["llm_calls"] == 1


def test_condition_b_returns_argument_and_answer():
    rec = run_condition("Q?", CONTEXT, EVIDENCE_TEXTS, ScriptedLLM([ARG_CLEAN, "Final [1]."]), "B_structured")
    assert rec["answer"] == "Final [1]."
    assert rec["argument"]["derived_conclusion"].startswith("A licence is required")
    assert rec["llm_calls"] == 2
    assert rec["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert rec["latency_ms"] == 0.0


def test_final_answer_shares_system_prompt_across_conditions():
    """Roadmap §21: only the reasoning architecture varies — same system prompt."""
    from app.rag.generation.prompt_template import GROUND_QA_SYSTEM_PROMPT

    llm_a = ScriptedLLM(["Answer [1]."])
    run_condition("Q?", CONTEXT, EVIDENCE_TEXTS, llm_a, "A_direct")
    llm_b = ScriptedLLM([ARG_CLEAN, "Final [1]."])
    run_condition("Q?", CONTEXT, EVIDENCE_TEXTS, llm_b, "B_structured")
    assert llm_a.systems == [GROUND_QA_SYSTEM_PROMPT]
    assert llm_b.systems[-1] == GROUND_QA_SYSTEM_PROMPT


def test_condition_a_uses_grounded_qa_user_contract():
    """Roadmap §21: condition A keeps the citation/prompt contract — same
    grounded_qa user shape (tagged context, question last) as production."""
    llm = ScriptedLLM(["Answer [1]."])
    run_condition("Q?", CONTEXT, EVIDENCE_TEXTS, llm, "A_direct")
    assert "<legal_context>" in llm.users[0]
    assert llm.users[0].rindex("Q?") > llm.users[0].index("</legal_context>")


def test_condition_c_clean_argument_no_revision():
    rec = run_condition("Q?", CONTEXT, EVIDENCE_TEXTS, ScriptedLLM([ARG_CLEAN, "Final [1]."]), "C_structured_audit")
    assert rec["audit"]["status"] == "PASS"
    assert rec["revision_count"] == 0
    assert rec["llm_calls"] == 2


def test_condition_c_defect_triggers_single_revision():
    llm = ScriptedLLM([ARG_NO_EXCEPTIONS, ARG_CLEAN, "Final [1]."])
    rec = run_condition("Q?", CONTEXT, EVIDENCE_TEXTS, llm, "C_structured_audit")
    assert rec["revision_count"] == 1
    assert rec["audits"][0]["status"] == "FAIL"
    assert rec["audit"]["status"] == "PASS"
    assert rec["llm_calls"] == 3


def test_budget_guard():
    with pytest.raises(LLMBudgetExceeded):
        run_condition(
            "Q?", CONTEXT, EVIDENCE_TEXTS, ScriptedLLM([ARG_CLEAN, "Final [1]."]), "B_structured", max_calls=1
        )


def test_analyze_rates_and_transitions():
    records = [
        {"question_id": "q1", "condition": "A_direct", "correct": False, "llm_calls": 1},
        {"question_id": "q1", "condition": "B_structured", "correct": True, "llm_calls": 2},
        {"question_id": "q1", "condition": "C_structured_audit", "correct": True, "llm_calls": 2},
        {"question_id": "q2", "condition": "A_direct", "correct": False, "llm_calls": 1},
        {"question_id": "q2", "condition": "B_structured", "correct": False, "llm_calls": 2},
        {"question_id": "q2", "condition": "C_structured_audit", "correct": True, "llm_calls": 3},
    ]
    result = analyze(records)
    assert result["conditions"]["A_direct"]["correctness"] == 0.0
    assert result["conditions"]["B_structured"]["correctness"] == 0.5
    assert result["conditions"]["C_structured_audit"]["correctness"] == 1.0
    assert result["transitions"]["A_to_B"]["improved"] == 1
    assert result["conditions"]["C_structured_audit"]["mean_llm_calls"] == 2.5
