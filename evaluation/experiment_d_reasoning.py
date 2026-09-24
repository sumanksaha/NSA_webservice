"""Experiment D — Direct vs Structured vs Structured+Auditor (Phase 1).

Roadmap §21: the first decisive experiment.  Same questions, same evidence,
same model/temperature — only the reasoning architecture varies:

* ``A_direct`` — question + evidence → final answer
* ``B_structured`` — question + evidence → structured argument → final answer
* ``C_structured_audit`` — B + deterministic auditor + max 1 revision → answer

Correctness grading is pluggable (``grade_fn``); production runs wire
``evaluation.grading.grade_answer`` over the benchmark, stub runs carry no
verdicts.  Every record carries ``llm_calls`` (roadmap §26 cost tracking)
and the harness aborts past the call budget instead of drifting.

Usage:
    python -m evaluation.experiment_d_reasoning --stub
    python -m evaluation.experiment_d_reasoning --stub --out-dir evaluation/out/exp_d_stub
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.agent.nodes.auditor import audit_argument
from app.rag.generation.reasoning_path import (
    DEFAULT_MAX_REVISIONS,
    reasoning_user_content,
    revision_context,
    should_revise,
)
from app.rag.generation.structured_reasoner import StructuredReasoner

CONDITIONS = ("A_direct", "B_structured", "C_structured_audit")

#: 150 questions × (1 + 2 + 3) calls — the hard ceiling for a full run.
HARD_BUDGET = 900


class LLMBudgetExceeded(Exception):
    """Raised when a run would exceed its LLM call budget."""


class _CountingLLM:
    """Count delegated calls and accumulate usage/latency; fail fast past ``max_calls``."""

    def __init__(self, inner: Any, max_calls: int) -> None:
        self._inner = inner
        self.max_calls = max_calls
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.latency_ms = 0.0

    def call(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> Any:
        if self.calls >= self.max_calls:
            raise LLMBudgetExceeded(f"budget of {self.max_calls} LLM calls exceeded")
        self.calls += 1
        response = self._inner.call(system_prompt, user_prompt, **kwargs)
        usage = getattr(response, "usage", None) or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        self.latency_ms += float(getattr(response, "latency", 0.0) or 0.0) * 1000.0
        return response

    def usage(self) -> dict[str, int]:
        """Summed token usage across delegated calls (roadmap §26)."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }


def _direct_answer(question: str, context: str, llm: Any) -> str:
    from app.rag.generation.prompt_template import PromptTemplate

    system, user = PromptTemplate().render_default(question, context)
    response = llm.call(system, user)
    return response.text or ""


def _answer_from_argument(question: str, argument: dict[str, Any], context: str, llm: Any) -> str:
    """Render the final answer under the SAME system prompt as condition A.

    Roadmap §21 isolates the reasoning architecture: the only difference
    between conditions is the user content (structured argument + evidence
    vs raw evidence), never the system prompt.
    """
    from app.rag.generation.prompt_template import GROUND_QA_SYSTEM_PROMPT

    user = reasoning_user_content(question, json.dumps(argument), context)
    response = llm.call(GROUND_QA_SYSTEM_PROMPT, user)
    return response.text or ""


def run_condition(
    question: str,
    context: str,
    evidence_texts: dict[str, str],
    llm: Any,
    condition: str,
    *,
    max_calls: int = 10,
    max_revisions: int = DEFAULT_MAX_REVISIONS,
    grade_fn: Callable[[str, str], bool | None] | None = None,
) -> dict[str, Any]:
    """Run one (question, evidence, condition) cell.  Raises ``LLMBudgetExceeded``."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r} (expected one of {CONDITIONS})")
    counting = _CountingLLM(llm, max_calls)
    record: dict[str, Any] = {"condition": condition}

    if condition == "A_direct":
        record["answer"] = _direct_answer(question, context, counting)
    else:
        reasoner = StructuredReasoner(llm_client=counting)
        argument = reasoner.reason(question, context)
        audits: list[dict[str, Any]] = []
        revision_count = 0
        if condition == "C_structured_audit":
            audit = audit_argument(argument, evidence_texts)
            audits.append(audit.model_dump())
            while should_revise(audit, revision_count, max_revisions):
                revision_count += 1
                argument = reasoner.reason(question, revision_context(context, audit.defects))
                audit = audit_argument(argument, evidence_texts)
                audits.append(audit.model_dump())
            record["audits"] = audits
            record["audit"] = audits[-1]
            record["revision_count"] = revision_count
        record["argument"] = argument.model_dump()
        record["answer"] = _answer_from_argument(question, argument.model_dump(), context, counting)

    record["llm_calls"] = counting.calls
    record["usage"] = counting.usage()
    record["latency_ms"] = round(counting.latency_ms, 1)

    if grade_fn is not None:
        record["correct"] = grade_fn(question, record["answer"])
    return record


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-condition correctness + A→B→C transitions + mean LLM cost."""
    conditions: dict[str, Any] = {}
    for cond in CONDITIONS:
        rows = [r for r in records if r.get("condition") == cond]
        verdicts = [r["correct"] for r in rows if r.get("correct") is not None]
        calls = [r["llm_calls"] for r in rows if isinstance(r.get("llm_calls"), (int, float))]
        conditions[cond] = {
            "n": len(rows),
            "correctness": round(sum(1 for v in verdicts if v) / len(verdicts), 4) if verdicts else None,
            "mean_llm_calls": round(sum(calls) / len(calls), 2) if calls else None,
        }
    by_q: dict[str, dict[str, Any]] = {}
    for r in records:
        by_q.setdefault(str(r.get("question_id", "?")), {})[r.get("condition", "?")] = r.get("correct")
    transitions: dict[str, dict[str, int]] = {}
    for pair, (c1, c2) in {
        "A_to_B": ("A_direct", "B_structured"),
        "B_to_C": ("B_structured", "C_structured_audit"),
    }.items():
        counts: Counter = Counter()
        for conds in by_q.values():
            v1, v2 = conds.get(c1), conds.get(c2)
            if v1 is None or v2 is None:
                continue
            counts["improved" if (not v1) and v2 else "unchanged" if v1 == v2 else "worsened"] += 1
        transitions[pair] = dict(counts)
    return {"conditions": conditions, "transitions": transitions, "n_records": len(records)}


STUB_QUESTIONS: list[dict[str, Any]] = [
    {
        "question_id": "stub-q1",
        "question": "Does Section 31 require a licence for a retail food shop?",
        "context": "Section 31 requires food businesses to obtain a licence. Provided that petty retailers are exempt.",
        "evidence_texts": {
            "FSS_ACT::31": "Section 31 requires food businesses to obtain a licence. Provided that petty retailers are exempt."
        },
    },
    {
        "question_id": "stub-q2",
        "question": "What does 'food' mean under the Act?",
        "context": '"Food" means any article used as food for human consumption.',
        "evidence_texts": {"FSS_ACT::3-def": '"Food" means any article used as food for human consumption.'},
    },
]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (also importable for tests)."""
    parser = argparse.ArgumentParser(description="Experiment D stub validation (Phase 1, zero real LLM calls).")
    parser.add_argument("--stub", action="store_true", help="Run the synthetic stub demo.")
    parser.add_argument("--out-dir", default=None, help="Write checkpoint JSONL + aggregate JSON here.")
    args = parser.parse_args(argv)

    if not args.stub:
        parser.print_help()
        return 2

    from app.rag.generation.llm_client import GroundedLLMClient

    llm: Any = GroundedLLMClient()
    if not llm.use_stub:
        print("refusing: real LLM configured — stub validation must not spend budget", file=sys.stderr)
        return 2

    records: list[dict[str, Any]] = []
    spent = 0
    for item in STUB_QUESTIONS:
        for cond in CONDITIONS:
            rec = run_condition(
                item["question"], item["context"], item["evidence_texts"], llm, cond, max_calls=HARD_BUDGET - spent
            )
            spent += rec["llm_calls"]
            rec["question_id"] = item["question_id"]
            records.append(rec)
    result = analyze(records)

    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "experiment_D_checkpoint.jsonl").write_text(
            "\n".join(json.dumps(r, default=str) for r in records), encoding="utf-8"
        )
        (out / "experiment_D_aggregate.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"wrote {out / 'experiment_D_checkpoint.jsonl'} + {out / 'experiment_D_aggregate.json'}")

    for cond in CONDITIONS:
        c = result["conditions"][cond]
        print(f"{cond}: n={c['n']} correctness={c['correctness']} mean_llm_calls={c['mean_llm_calls']}")
    print(f"total LLM calls: {spent} (stub — budget untouched)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
