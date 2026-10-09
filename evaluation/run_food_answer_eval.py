"""§6 item 1 — answer-level (LLM) quality eval over benchmark v1.1 (live LLM).

Retrieval-side metrics (recall@K, disambiguation) are exhaustive but stop at
the ranked chunk list: they say *whether the right clause was retrieved*, not
*whether the answer that reaches the user is faithful and complete*.  This
runner closes that gap by running the full generation pipeline
(``run_generation_pipeline``) per question with a LIVE LLM and scoring the
answer on two axes, each with a deterministic signal and an LLM-as-judge
signal:

* **Faithfulness** — every claim in the answer is supported by the retrieved
  evidence (no fabricated parameter value, limit, or source).
  Deterministic: the pipeline's ``groundedness_score`` +
  ``hallucination_detected`` + citation validation.  Judge: a second LLM call
  (same model) shown the answer and the evidence asks for a 0–4 support score.
* **Completeness** — the answer reaches the substance of the benchmark's
  ``acceptable_conclusion`` and satisfies the asked intent (for a
  parameter/standard ask: a numeric limit is present; a definition is NOT
  substituted for a standard).
  Deterministic: ``food_answer.check_answer_completeness`` (§16 flags) +
  the definition-leak detector.  Judge: 0–4 satisfaction score.

The runner forces live LLM mode (``RAG_USE_STUB_LLM=false``) and fails loudly
if the client is still stubbed — a stubbed run would measure the stub's canned
text, which is exactly the gap this eval exists to close.  All ``RAG_FOOD_*``
flags run at the shipped S4_full configuration (as in the retrieval ablation).

Results are cached per question under
``evaluation/out/food_answer/answers.jsonl`` (resumable).  Run inside the Flask
app context exactly as production does::

    python -m evaluation.run_food_answer_eval [--limit N] [--force]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# Force live LLM before anything reads the environment: load_dotenv defaults to
# not overriding an already-set variable, so this wins over .env.
os.environ["RAG_USE_STUB_LLM"] = "false"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval.food_answer")

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "food_answer"
ANSWERS_PATH = OUT_DIR / "answers.jsonl"
METRICS_PATH = OUT_DIR / "metrics.json"
REPORT_PATH = OUT_DIR / "report.md"

#: Shipped configuration (identical to the S4_full retrieval arm) — the
#: answer-eval measures what users actually get.
S4_FLAGS: dict[str, bool | int] = {
    "RAG_FOOD_INTENT_ENABLED": True,
    "RAG_FOOD_LEGAL_RERANK": True,
    "RAG_FOOD_PARENT_RECONSTRUCT": True,
    "RAG_FOOD_VALIDATE": True,
    "RAG_FOOD_ANSWER_MODE": True,
    "RAG_FOOD_FALLBACK_ROUNDS": 2,
}
FLAG_KEYS = tuple(S4_FLAGS)

#: Evidence text budget for the judge prompt (chars).  Enough to show the
#: cited clauses without blowing the free-tier context window.
_JUDGE_EVIDENCE_CHARS = 6000


def _apply_flags(app) -> None:
    for key in FLAG_KEYS:
        app.config[key] = S4_FLAGS[key]


# --------------------------------------------------------------------------- #
# LLM-as-judge
# --------------------------------------------------------------------------- #
_JUDGE_SYSTEM = (
    "You are a strict evaluator of grounded legal-RAG answers about food-safety "
    "regulations. You will be shown a QUESTION and the ANSWER the system produced. "
    "Judge ONLY the answer. The evidence and the expected conclusion are shown for "
    "your reference — they are NOT the answer, and an answer that merely restates "
    "them without ever addressing the question must not score highly.\n"
    "Reply with ONLY a JSON object, no prose:\n"
    '{"faithfulness": <0-4>, "faithfulness_note": "<one short sentence>", '
    '"completeness": <0-4>, "completeness_note": "<one short sentence>"}\n\n'
    "Scoring (both 0-4 integers):\n"
    "  faithfulness 4 = every claim (values, limits, sources) is present in the evidence; "
    "3 = fully supported but one minor framing; 2 = one unsupported or misstated detail; "
    "1 = a fabricated limit/value; 0 = answer contradicts or invents the standard.\n"
    "  completeness 4 = fully answers the question and reaches the substance of the "
    "expected conclusion; 3 = correct but omits one secondary detail; 2 = answers a "
    "related but different question (e.g. gives a definition when a standard was asked, "
    "or a numeric limit without the parameter's name); 1 = mostly irrelevant.\n"
    "  COMPLETENESS 0 IS MANDATORY when the answer is empty, blank, whitespace-only, "
    "consists solely of a 'passages bearing on the question' header with no substantive "
    "content, or says only that the evidence is insufficient. Such an answer has "
    "reached nothing, so faithfulness and completeness are both 0. Never award a "
    "non-zero score to an answer that does not itself state a conclusion."
)


def _judge_prompt(question: str, answer: str, evidence: str, acceptable: str) -> str:
    # Order matters: the answer comes FIRST, before the gold material. Showing
    # the expected conclusion ahead of the answer anchors a weak judge upward
    # and let empty answers score 4/4 in the first live run.
    return (
        f"QUESTION:\n{question}\n\n"
        f"ANSER TO JUDGE:\n{answer or '(the system returned an empty answer)'}\n\n"
        f"REFERENCE — EVIDENCE the system was shown:\n{evidence}\n\n"
        f"REFERENCE — EXPECTED CONCLUSION (benchmark gold):\n{acceptable}\n\n"
        "Judge the ANSWER only. Reply with the single JSON object now."
    )


#: An answer must be at least this long before it is treated as real content.
_MIN_ANSWER_CHARS = 120

#: Markers of a conclusion that *does* state something.  Presence of any of
#: these means the answer reached a finding, whatever boilerplate precedes it.
_CONCLUSION_MARKERS = (
    "answer:",
    "the limit",
    "limit for",
    "not more than",
    "not less than",
    "shall not",
    "complies",
    "does not comply",
    "is within",
    "is not within",
    "%",
)

#: Phrases marking a bare refusal — the whole answer is "I can't tell you".
#: Deliberately narrower than the first version of this check: a refusal
#: *sentence* inside an answer that also states a value is not a refusal.
_REFUSAL_PHRASES = (
    "the retrieved evidence is insufficient",
    "cannot be determined from the retrieved evidence",
    "the evidence does not establish",
)


def _is_degenerate(answer: str) -> bool:
    """True when the answer reached no conclusion at all.

    Two cases only:

    1. The upstream LLM call failed (e.g. a rate limit) and the pipeline
       degraded to an empty string.  That is an infrastructure failure, not
       an answer, and scoring it would silently move the means.
    2. The answer is so short it cannot contain a finding.

    An earlier version of this check also rejected anything containing the
    word "passage" and any answer using an unbolded ``Answer:`` label.  That
    wrongly excluded 7 of 26 real answers (FI020/FI022/FI023/FI025/FI026 …) —
    evidence-quoting answers that *do* reach a value.  A refusal is only a
    refusal when no conclusion marker appears anywhere in the text.
    """
    text = (answer or "").strip()
    if not text:
        return True
    if len(text) < _MIN_ANSWER_CHARS:
        return True
    lowered = text.lower()
    if any(marker in lowered for marker in _CONCLUSION_MARKERS):
        return False
    # No conclusion marker at all: bare refusal / evidence dump.
    return True


def _parse_judge_json(text: str) -> dict[str, Any] | None:
    """Extract the first {...} JSON object from a judge reply."""
    if not text:
        return None
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _coerce_score(value: Any) -> float | None:
    """Map a judge score to 0.0–1.0, accepting ints/floats/strings."""
    try:
        s = float(value)
    except (TypeError, ValueError):
        return None
    if s < 0:
        return None
    return min(s / 4.0, 1.0)


def _judge_answer(client, question: str, answer: str, evidence: str, acceptable: str) -> dict[str, Any]:
    """Score one answer with the live LLM. Returns raw judge fields."""
    prompt = _judge_prompt(question, answer, evidence, acceptable)
    resp = client.call(_JUDGE_SYSTEM, prompt, temperature=0.0, max_tokens=300)
    if not resp.success:
        return {"judge_error": resp.error or "judge call failed"}
    parsed = _parse_judge_json(resp.text)
    if parsed is None:
        return {"judge_error": "unparseable judge reply", "judge_raw": resp.text[:400]}
    return parsed


# --------------------------------------------------------------------------- #
# Per-question run
# --------------------------------------------------------------------------- #
def _deterministic_signals(out: dict[str, Any]) -> dict[str, Any]:
    """Deterministic faithfulness/completeness signals from the pipeline output."""
    verification = out.get("verification") or {}
    citation = verification.get("citation_validation") or {}
    comp = out.get("food_completeness") or {}
    return {
        "groundedness_score": out.get("groundedness_score"),
        "hallucination_detected": bool(out.get("hallucination_detected")),
        "n_citations": len(out.get("citations") or []),
        "citation_score": citation.get("score"),
        "answer_complete": comp.get("answer_complete"),
        "numeric_value_present": comp.get("numeric_value_present"),
        "definition_leak": comp.get("definition_leak"),
        "parameter_complete": comp.get("parameter_complete"),
        "entity_found": comp.get("entity_found"),
        "intent_satisfied": comp.get("intent_satisfied"),
        "standard_found": comp.get("standard_found"),
    }


def _run_question(q: dict[str, Any], client) -> dict[str, Any]:
    """Run the full generation pipeline for one question and score it."""
    from app.rag.tasks import run_generation_pipeline

    t0 = time.monotonic()
    out = run_generation_pipeline(query=q["question"], top_k=10)
    latency_ms = int((time.monotonic() - t0) * 1000)

    answer = out.get("answer") or ""
    evidence_chunks = out.get("retrieved_chunks") or []
    evidence = "\n\n".join(
        f"[{i}] {c.get('text', '')}" for i, c in enumerate(evidence_chunks, 1) if c.get("text")
    )[:_JUDGE_EVIDENCE_CHARS]

    det = _deterministic_signals(out)

    # A failed/absent generation is not an answer. Recording a judge score for
    # it would silently inflate the means (an empty answer scored 4/4 in the
    # first live run), so it is marked and excluded from the quality means.
    degenerate = _is_degenerate(answer)
    if degenerate:
        judge: dict[str, Any] = {"judge_skipped": "degenerate answer (empty or refusal)"}
        faith_j = comp_j = None
    else:
        judge = _judge_answer(client, q["question"], answer, evidence, q.get("acceptable_conclusion", ""))
        faith_j = _coerce_score(judge.get("faithfulness"))
        comp_j = _coerce_score(judge.get("completeness"))

    return {
        "question_id": q["question_id"],
        "question": q["question"],
        "category": q["category"],
        "expected_intent": q["expected_intent"],
        "entity": q.get("entity"),
        "parameters": q.get("parameters") or [],
        "answer": answer,
        "answer_chars": len(answer),
        "degenerate_answer": degenerate,
        "n_retrieved": len(evidence_chunks),
        # Persist the evidence the judge saw so a run can be re-judged (e.g.
        # after a judge-prompt fix) without re-calling the generation LLM.
        "judge_evidence": evidence,
        "llm_model": out.get("llm_model"),
        "generation_latency_ms": out.get("generation_latency_ms"),
        "total_latency_ms": out.get("total_latency_ms"),
        "latency_ms": latency_ms,
        "deterministic": det,
        "judge_raw": judge,
        "faithfulness_judge": faith_j,
        "completeness_judge": comp_j,
    }


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate answer-quality metrics overall and by category/intent."""
    scored = [r for r in records if not r.get("error")]
    faith_j = [r["faithfulness_judge"] for r in scored]
    comp_j = [r["completeness_judge"] for r in scored]

    det_answer_complete = [bool(r["deterministic"].get("answer_complete")) for r in scored]
    det_numeric = [bool(r["deterministic"].get("numeric_value_present")) for r in scored]
    det_def_leak = [bool(r["deterministic"].get("definition_leak")) for r in scored]
    det_halluc = [bool(r["deterministic"].get("hallucination_detected")) for r in scored]
    groundedness = [r["deterministic"].get("groundedness_score") for r in scored]
    par_eligible = [r for r in scored if r.get("parameters")]
    par_complete = [bool(r["deterministic"].get("parameter_complete")) for r in par_eligible]

    overall = {
        "n": len(scored),
        "n_errors": len(records) - len(scored),
        "n_degenerate_answers": sum(1 for r in scored if r.get("degenerate_answer")),
        "faithfulness_judge_mean": _mean(faith_j),
        "completeness_judge_mean": _mean(comp_j),
        "judge_scored": sum(1 for v in faith_j if v is not None),
        "groundedness_mean": _mean(groundedness),
        "answer_complete_rate": _mean([1.0 if x else 0.0 for x in det_answer_complete]),
        "numeric_value_rate": _mean([1.0 if x else 0.0 for x in det_numeric]),
        "definition_leak_rate": _mean([1.0 if x else 0.0 for x in det_def_leak]),
        "hallucination_rate": _mean([1.0 if x else 0.0 for x in det_halluc]),
        "parameter_complete_rate": _mean([1.0 if x else 0.0 for x in par_complete]) if par_eligible else None,
        "n_parameter_eligible": len(par_eligible),
    }

    def _group(key: str) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in scored:
            groups.setdefault(str(r.get(key)), []).append(r)
        out: dict[str, Any] = {}
        for name, rows in sorted(groups.items()):
            out[name] = {
                "n": len(rows),
                "faithfulness_judge_mean": _mean([r["faithfulness_judge"] for r in rows]),
                "completeness_judge_mean": _mean([r["completeness_judge"] for r in rows]),
                "answer_complete_rate": _mean(
                    [1.0 if r["deterministic"].get("answer_complete") else 0.0 for r in rows],
                ),
            }
        return out

    return {
        "overall": overall,
        "by_category": _group("category"),
        "by_intent": _group("expected_intent"),
    }


def _write_report(metrics: dict[str, Any], records: list[dict[str, Any]]) -> None:
    o = metrics["overall"]
    lines: list[str] = []
    lines.append("# Answer-level (LLM) quality eval — food-intent benchmark v1.1\n")
    lines.append(
        f"Live-LLM run of the full generation pipeline (S4_full config) over "
        f"{o['n']} benchmark questions. Judge = the same live model as generation.\n",
    )
    lines.append("## Overall\n")
    lines.append(f"- **Faithfulness (judge mean):** {o['faithfulness_judge_mean']}")
    lines.append(f"- **Completeness (judge mean):** {o['completeness_judge_mean']}")
    lines.append(f"- Groundedness (pipeline mean): {o['groundedness_mean']}")
    lines.append(f"- §16 answer_complete rate: {o['answer_complete_rate']}")
    lines.append(f"- Numeric-value-present rate: {o['numeric_value_rate']}")
    lines.append(f"- Definition-leak rate: {o['definition_leak_rate']}")
    lines.append(f"- Pipeline hallucination rate: {o['hallucination_rate']}")
    if o.get("parameter_complete_rate") is not None:
        lines.append(
            f"- Parameter-complete rate ({o['n_parameter_eligible']} parameter asks): {o['parameter_complete_rate']}",
        )
    if o.get("judge_scored") is not None and o["judge_scored"] < o["n"]:
        lines.append(f"- (judge parsed for {o['judge_scored']}/{o['n']} questions)")
    if o.get("n_degenerate_answers"):
        lines.append(
            f"- **{o['n_degenerate_answers']} degenerate answers** (empty or bare refusal — the upstream "
            "LLM call failed) are excluded from the judge means; see `--retry-incomplete`.",
        )

    lines.append("\n## By category\n")
    lines.append("| category | n | faithfulness | completeness | answer_complete |")
    lines.append("|---|---|---|---|---|")
    for name, g in metrics["by_category"].items():
        lines.append(
            f"| {name} | {g['n']} | {g['faithfulness_judge_mean']} | "
            f"{g['completeness_judge_mean']} | {g['answer_complete_rate']} |",
        )

    lines.append("\n## By intent\n")
    lines.append("| intent | n | faithfulness | completeness | answer_complete |")
    lines.append("|---|---|---|---|---|")
    for name, g in metrics["by_intent"].items():
        lines.append(
            f"| {name} | {g['n']} | {g['faithfulness_judge_mean']} | "
            f"{g['completeness_judge_mean']} | {g['answer_complete_rate']} |",
        )

    # Lowest-completeness questions: where the answer layer still leaks.
    weak = sorted(
        [r for r in records if not r.get("error") and r.get("completeness_judge") is not None],
        key=lambda r: r["completeness_judge"],
    )[:5]
    if weak:
        lines.append("\n## Weakest answers (by judge completeness)\n")
        for r in weak:
            lines.append(
                f"- **{r['question_id']}** ({r['category']}) faith={r['faithfulness_judge']} "
                f"complete={r['completeness_judge']} — {r['question']}\n"
                f"  - judge: {(r.get('judge_raw') or {}).get('completeness_note', '')}",
            )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from evaluation.food_intent_metrics import load_food_intent_questions

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="run only the first N questions (0 = all)")
    parser.add_argument("--force", action="store_true", help="ignore the cache and re-run every question")
    parser.add_argument(
        "--retry-incomplete",
        action="store_true",
        help="re-run only questions whose cached record has no judge score (free-tier 429s)",
    )
    parser.add_argument(
        "--rejudge",
        action="store_true",
        help=(
            "re-score cached answers with the current judge prompt, without calling the "
            "generation LLM again (requires judge_evidence in answers.jsonl)"
        ),
    )
    args = parser.parse_args()

    questions = load_food_intent_questions()
    if args.limit:
        questions = questions[: args.limit]
    logger.info("questions: %d (live LLM mode forced)", len(questions))

    from app import create_app

    app = create_app()
    records: list[dict[str, Any]] = []
    done_ids: set[str] = set()
    if ANSWERS_PATH.exists() and not args.force:
        with open(ANSWERS_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    # On --retry-incomplete a record that never got a judge
                    # score (LLM 429 / parse failure), or whose generation
                    # produced a degenerate answer, does not count as done —
                    # the next pass redoes just those questions.
                    stale = rec.get("faithfulness_judge") is None or rec.get("degenerate_answer") is True
                    if args.retry_incomplete and stale:
                        rec = {**rec, "_stale": True}
                    records.append(rec)
                    done_ids.add(rec["question_id"])
                except Exception:
                    continue

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pending = [q for q in questions if args.force or q["question_id"] not in done_ids]
    if args.rejudge:
        # Re-score cached answers in place: no generation LLM call, so this is
        # cheap and isolates a judge-prompt change from answering behaviour.
        by_q = {q["question_id"]: q for q in questions}
        rescored: list[dict[str, Any]] = []
        with app.app_context():
            for key, value in S4_FLAGS.items():
                app.config[key] = value
            from app.rag.generation.llm_client import GroundedLLMClient

            client = GroundedLLMClient()
            if client.use_stub:
                raise SystemExit("LLM client is in STUB mode — refusing to judge against a stub.")
            for i, rec in enumerate(records, 1):
                q = by_q.get(rec["question_id"])
                if not q:
                    continue
                evidence = rec.get("judge_evidence")
                if evidence is None:
                    logger.warning("%s: no cached evidence, skipping", rec["question_id"])
                    rescored.append(rec)
                    continue
                answer = rec.get("answer") or ""
                rec["degenerate_answer"] = _is_degenerate(answer)
                if rec["degenerate_answer"]:
                    rec["judge_raw"] = {"judge_skipped": "degenerate answer (empty or refusal)"}
                    rec["faithfulness_judge"] = None
                    rec["completeness_judge"] = None
                else:
                    judge = _judge_answer(
                        client, q["question"], answer, evidence, q.get("acceptable_conclusion", ""),
                    )
                    rec["judge_raw"] = judge
                    rec["faithfulness_judge"] = _coerce_score(judge.get("faithfulness"))
                    rec["completeness_judge"] = _coerce_score(judge.get("completeness"))
                rescored.append(rec)
                logger.info(
                    "rejudge %d/%d %s faith=%s complete=%s",
                    i,
                    len(records),
                    rec["question_id"],
                    rec.get("faithfulness_judge"),
                    rec.get("completeness_judge"),
                )
        records = rescored
        pending = []
    elif args.retry_incomplete:
        stale = {r["question_id"] for r in records if r.get("_stale")}
        records = [{k: v for k, v in r.items() if k != "_stale"} for r in records if r["question_id"] not in stale]
        pending = [q for q in questions if q["question_id"] in stale]
    with app.app_context():
        _apply_flags(app)

        from app.rag.generation.llm_client import GroundedLLMClient

        client = GroundedLLMClient()
        if client.use_stub:
            raise SystemExit(
                "LLM client is in STUB mode — refusing to measure answer quality against a stub. "
                "Set OPENROUTER_API_KEY and RAG_USE_STUB_LLM=false.",
            )
        logger.info("live LLM: model=%s", client.model)

        for i, q in enumerate(pending, 1):
            t0 = time.monotonic()
            try:
                rec = _run_question(q, client)
            except Exception as exc:  # per-question isolation
                rec = {
                    "question_id": q["question_id"],
                    "question": q["question"],
                    "category": q["category"],
                    "expected_intent": q["expected_intent"],
                    "error": f"{type(exc).__name__}: {exc}",
                    "deterministic": {},
                    "faithfulness_judge": None,
                    "completeness_judge": None,
                }
            with open(ANSWERS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            # Replace any cached copy of this question in the in-memory set.
            records = [r for r in records if r.get("question_id") != rec["question_id"]]
            records.append(rec)
            logger.info(
                "%d/%d %s faith=%s complete=%s (%.0fs)",
                i,
                len(pending),
                rec["question_id"],
                rec.get("faithfulness_judge"),
                rec.get("completeness_judge"),
                time.monotonic() - t0,
            )

    # Keep only records for the questions we were asked about.
    by_id = {r["question_id"]: r for r in records}
    ordered = [by_id[q["question_id"]] for q in questions if q["question_id"] in by_id]
    # Compact the append-only log to one record per question (re-runs append).
    with open(ANSWERS_PATH, "w", encoding="utf-8") as f:
        for rec in ordered:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    metrics = aggregate(ordered)
    metrics["flags"] = {k: S4_FLAGS[k] for k in FLAG_KEYS}
    METRICS_PATH.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_report(metrics, ordered)

    o = metrics["overall"]
    print("\n=== Answer-level quality (benchmark v1.1, live LLM) ===")
    print(f"n={o['n']} (errors {o['n_errors']})")
    print(f"faithfulness (judge): {o['faithfulness_judge_mean']}")
    print(f"completeness (judge): {o['completeness_judge_mean']}")
    print(f"groundedness:         {o['groundedness_mean']}")
    print(f"answer_complete:      {o['answer_complete_rate']}")
    print(f"definition_leak:      {o['definition_leak_rate']}")
    print(f"hallucination:        {o['hallucination_rate']}")
    if o.get("parameter_complete_rate") is not None:
        print(f"parameter_complete:   {o['parameter_complete_rate']} (n={o['n_parameter_eligible']})")
    print(f"\nmetrics -> {METRICS_PATH}")
    print(f"report  -> {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
