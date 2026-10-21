"""Answer-channel guardrail and delivery-flag tests.

Regression cover for MODEL_SWAP_FREE_PLAN §11 and §13 (P0/P1): a reasoning
model that spends its whole budget on the trace returns ``content=null``.  The
client used to serve the trace as the answer, so a run that never wrote an
answer scored like an ordinary weak answer.  These tests pin the new rule:

* ``content=null`` is an error (``llm_empty_content``) unless the fallback is
  explicitly enabled;
* the completion/thinking budgets come from the config seam;
* cost and reasoning tokens survive into ``usage``;
* a plan is not a conclusion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.generation import llm_client as gen
from app.rag.generation.llm_client import (
    EMPTY_CONTENT_ERROR,
    GroundedLLMClient,
    _usage_dict,
    answer_text,
)
from app.rag.generation.sanitizer import ResponseSanitizer, _has_conclusion
from app.shared import llm_client as shared
from app.shared.config import cfg


def _choice(content, reasoning=None, finish="stop"):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    return {"message": message, "finish_reason": finish}


# --------------------------------------------------------------------------- #
# The channel rule
# --------------------------------------------------------------------------- #


def test_content_none_is_a_visible_error(monkeypatch):
    monkeypatch.delenv("RAG_LLM_REASONING_FALLBACK", raising=False)
    text, error = answer_text(_choice(None, "We need answer legal. Need parse question."))
    assert text == ""
    assert error.startswith(EMPTY_CONTENT_ERROR)
    assert "finish_reason='stop'" in error


def test_truncated_trace_is_not_served(monkeypatch):
    monkeypatch.setattr(cfg, "llm_reasoning_fallback", False)
    text, error = answer_text(_choice(None, "We need answer...", finish="length"))
    assert text == ""
    assert "finish_reason='length'" in error


def test_empty_string_content_is_an_error(monkeypatch):
    monkeypatch.setattr(cfg, "llm_reasoning_fallback", False)
    text, error = answer_text(_choice("   "))
    assert text == ""
    assert error.startswith(EMPTY_CONTENT_ERROR)


def test_reasoning_fallback_is_opt_in(monkeypatch):
    monkeypatch.setattr(cfg, "llm_reasoning_fallback", True)
    text, error = answer_text(_choice(None, "the trace", finish="length"))
    assert text == "the trace"
    assert error is None


def test_fallback_never_shadows_real_content(monkeypatch):
    monkeypatch.setattr(cfg, "llm_reasoning_fallback", True)
    text, error = answer_text(_choice("Section 31 applies [1].", "plan"))
    assert text == "Section 31 applies [1]."
    assert error is None


def test_usage_keeps_cost_and_reasoning_tokens():
    usage = _usage_dict(
        {
            "prompt_tokens": 3821,
            "completion_tokens": 1024,
            "total_tokens": 4845,
            "cost": 0.0,
            "completion_tokens_details": {"reasoning_tokens": 1024},
        }
    )
    assert usage == {
        "prompt_tokens": 3821,
        "completion_tokens": 1024,
        "total_tokens": 4845,
        "reasoning_tokens": 1024,
        "cost": 0.0,
    }


# --------------------------------------------------------------------------- #
# Budget wiring: call() resolves the settings once, every caller inherits
# --------------------------------------------------------------------------- #


@pytest.fixture
def captured(monkeypatch):
    """Capture the transport kwargs that call() hands to _real_call."""
    monkeypatch.setattr(gen.GroundedLLMClient, "use_stub", property(lambda self: False))
    monkeypatch.setattr(shared, "_increment_call_count", lambda: None)
    monkeypatch.setattr(shared, "_log_llm_call", lambda **kw: None)
    seen: dict = {}

    def fake(self, system_prompt, user_prompt, *, temperature, max_tokens, **extra):
        seen.update(max_tokens=max_tokens, temperature=temperature, extra=dict(extra))
        return gen.GroundedLLMResponse(text="ok", model=self.model)

    monkeypatch.setattr(gen.GroundedLLMClient, "_real_call", fake)
    return seen


def _client() -> GroundedLLMClient:
    return GroundedLLMClient(api_key="test-key")


def test_budgets_come_from_the_config_seam(monkeypatch, captured):
    monkeypatch.setenv("RAG_LLM_MAX_TOKENS", "2048")
    monkeypatch.setenv("RAG_LLM_REASONING_MAX_TOKENS", "512")
    _client().call("s", "u")
    assert captured["max_tokens"] == 2048
    assert captured["extra"]["reasoning"] == {"max_tokens": 512}


def test_default_budget_leaves_room_for_the_answer(captured):
    defaults = {s.key: s.default for s in cfg.table()}
    assert defaults["RAG_LLM_MAX_TOKENS"] == 4096
    assert defaults["RAG_LLM_REASONING_MAX_TOKENS"] == 1024
    _client().call("s", "u")
    assert captured["max_tokens"] == cfg.llm_max_tokens
    assert captured["extra"]["reasoning"]["max_tokens"] == cfg.llm_reasoning_max_tokens


def test_zero_thinking_cap_sends_no_reasoning_field(monkeypatch, captured):
    monkeypatch.setattr(cfg, "llm_reasoning_max_tokens", 0)
    _client().call("s", "u")
    assert "reasoning" not in captured["extra"]


def test_explicit_max_tokens_wins(captured):
    """Judge/summary calls that pass a small budget keep it."""
    _client().call("s", "u", max_tokens=300)
    assert captured["max_tokens"] == 300


def test_caller_reasoning_override_wins(captured):
    _client().call("s", "u", reasoning={"effort": "low"})
    assert captured["extra"]["reasoning"] == {"effort": "low"}


# --------------------------------------------------------------------------- #
# A plan is not a conclusion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", False),
        ("   ", False),
        # the shipped minimax leak: first-person planning, no answer step
        ("We need answer legal. Need parse question. " * 4, False),
        # "step 2" mentioned inside the plan is not an answer heading
        (
            "We need answer structured step 1 quote passages, step 2 concise answer. "
            "Need go through every numbered source and note bearing ones.",
            False,
        ),
        ("Step 1 — Quotes: 'Section 31 [1].'\nStep 2 — Answer: A licence is required [1].", True),
        ("**Step 2 — Answer:** A licence is required [1].", True),
        ("Section 31 requires a licence before carrying on the business [1].", True),
        ("The context does not establish the penalty. No provision covers this.", True),
    ],
)
def test_has_conclusion(text, expected):
    assert _has_conclusion(text) is expected


def test_sanitizer_flags_a_plan_with_valid_citations():
    """A trace can cite real chunks and still not be an answer."""
    sanitized = ResponseSanitizer().sanitize(
        "We need answer legal. Need parse question and quote [1]. " * 3, [], []
    )
    assert sanitized.has_conclusion is False


def test_sanitizer_accepts_an_answer_body():
    sanitized = ResponseSanitizer().sanitize(
        "Section 31 requires a licence before carrying on a food business [1].", [], []
    )
    assert sanitized.has_conclusion is True


# --------------------------------------------------------------------------- #
# Prompt contract
# --------------------------------------------------------------------------- #


def test_prompt_demands_the_literal_step1_heading():
    from app.rag.generation.prompt_template import GROUND_QA_SYSTEM_PROMPT

    assert "Begin your reply with the literal heading" in GROUND_QA_SYSTEM_PROMPT
    assert "Do not describe your approach" in GROUND_QA_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Regression fixture: a recorded response from the default model under the
# shipped config (4096 completion, 1024 thinking).  This is the assertion the
# 260-test suite did not have when every answer was a truncated trace.
# --------------------------------------------------------------------------- #

_FIXTURE = Path(__file__).parent / "fixtures" / "llm" / "minimax_grounded_q052.json"


def test_default_model_fixture_has_content_and_a_conclusion():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    choice = payload["choices"][0]
    text, error = answer_text(choice)

    assert error is None
    assert text.strip(), "content must not be null/empty under the shipped budget"
    assert _has_conclusion(text), "the delivered text must contain an answer, not a plan"
    assert choice["finish_reason"] == "stop", "the budget must let the answer finish"
    # The answer is the content field, never the thinking trace.
    assert text != (choice["message"].get("reasoning") or "")



# --------------------------------------------------------------------------- #
# The flags reach the response the API serves
# --------------------------------------------------------------------------- #


def _chunks():
    from app.rag.retrieval.result import RetrievedChunk

    return [
        RetrievedChunk(
            chunk_id=f"c{i}",
            score=0.9 - i * 0.1,
            text=(
                f"Section {i + 1} of the FSS Act, 2006. The licensing authority "
                "may impose a penalty or fine; enforcement powers rest with the "
                "authority."
            ),
            section_number=str(i + 1),
            document_title="FSS Act 2006",
            document_type="act",
            authority="FSSAI",
        )
        for i in range(3)
    ]


class _FakeClient:
    def __init__(self, text):
        self.text = text

    def call(self, system_prompt, user_prompt, **kwargs):
        from app.rag.generation.llm_client import GroundedLLMResponse

        return GroundedLLMResponse(
            text=self.text,
            model="fake",
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "reasoning_tokens": 7,
                "cost": 0.00042,
            },
            latency=0.01,
            finish_reason="stop",
        )


def test_response_carries_delivery_and_cost_flags(monkeypatch):
    from app.rag.generation import GroundedGenerationService

    monkeypatch.setattr(cfg, "structured_reasoner", False)
    answer = "Step 1 — Quotes: 'x [1]'\nStep 2 — Answer: A licence is required [1]."
    resp = GroundedGenerationService(llm_client=_FakeClient(answer)).generate("q?", _chunks())
    assert resp.has_conclusion is True
    assert resp.debug["has_conclusion"] is True
    assert resp.debug["finish_reason"] == "stop"
    assert resp.debug["reasoning_tokens"] == 7
    assert resp.debug["cost_usd"] == 0.00042
    assert resp.token_usage["cost"] == 0.00042
    assert resp.token_usage["reasoning"] == 7


def test_response_is_flagged_degraded_when_the_answer_is_only_a_plan(monkeypatch):
    from app.rag.generation import GroundedGenerationService

    monkeypatch.setattr(cfg, "structured_reasoner", False)
    plan = "We need answer legal. Need parse question and quote [1]. " * 3
    resp = GroundedGenerationService(llm_client=_FakeClient(plan)).generate("q?", _chunks())
    assert resp.has_conclusion is False
    assert resp.debug["has_conclusion"] is False


def test_empty_content_produces_an_empty_answer_and_an_error(monkeypatch):
    """The P0 contract: no content → empty answer + visible error, never a trace."""
    from app.rag.generation import GroundedGenerationService
    from app.rag.generation.llm_client import GroundedLLMResponse

    monkeypatch.setattr(cfg, "structured_reasoner", False)
    monkeypatch.setattr(cfg, "llm_reasoning_fallback", False)

    class _NullContent:
        def call(self, *a, **kw):
            return GroundedLLMResponse(
                text="",
                model="fake",
                error=f"{EMPTY_CONTENT_ERROR}: no answer content (finish_reason='length')",
                finish_reason="length",
            )

    resp = GroundedGenerationService(llm_client=_NullContent()).generate("q?", _chunks())
    assert resp.answer == ""
    assert resp.has_conclusion is False
    assert resp.debug["error"].startswith(EMPTY_CONTENT_ERROR)
    assert resp.debug["finish_reason"] == "length"

