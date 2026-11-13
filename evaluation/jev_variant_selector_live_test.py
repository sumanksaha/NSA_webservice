"""Live 20-question evaluation of JEV variant selector vs baseline.

Runs both baseline GroundedGenerationService and VariantSelector (JEV-guided,
fail-closed to multi-candidate) over 20 random questions (10 answerable +
10 insufficient_evidence, seed 42) using real retrieval context.

Compares the three guardrails:
  1. Brevity: mean answer length reduction >= 15% vs baseline
  2. Correctness: soft correctness drop <= 0.02 vs baseline
  3. Abstention on answerable: increase <= 0.10 vs baseline

Writes per-question detail to evaluation/out/ceiling_v5/jev_variant_selector_live.json.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=True)

from evaluation.benchmark import load_questions
from evaluation.brevity_experiment import retrieve_chunks
from app.rag.generation.grounded_service import GroundedGenerationService
from app.rag.generation.llm_client import GroundedLLMClient
from app.rag.generation.variant_selector import VariantSelector
from evaluation.answer_scoring import score_answer


def is_abstention(text: str) -> bool:
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


def run_one(service, sel, query, chunks, expected, insufficient_evidence):
    """Run both baseline and selector; return both results."""
    base_resp = service.generate_with_prompts(query=query, chunks=chunks, query_type="")
    base_text = base_resp.answer or ""
    base_abst = is_abstention(base_text)
    base_scored = score_answer(base_text, expected, insufficient_evidence)

    sel_resp = sel.generate_with_prompts(query=query, chunks=chunks, query_type="")
    sel_text = sel_resp.answer or ""
    sel_abst = is_abstention(sel_text)
    sel_scored = score_answer(sel_text, expected, insufficient_evidence)

    return {
        "baseline": {
            "answer": base_text,
            "length": len(base_text),
            "soft": base_scored["answer_correctness"],
            "jaccard": base_scored["answer_jaccard"],
            "coverage": base_scored["answer_coverage"],
            "binary": base_scored["binary_correct"],
            "abstained": base_abst,
            "finish": base_resp.debug.get("finish_reason"),
            "error": base_resp.debug.get("error"),
        },
        "selector": {
            "answer": sel_text,
            "length": len(sel_text),
            "soft": sel_scored["answer_correctness"],
            "jaccard": sel_scored["answer_jaccard"],
            "coverage": sel_scored["answer_coverage"],
            "binary": sel_scored["binary_correct"],
            "abstained": sel_abst,
            "finish": sel_resp.debug.get("finish_reason"),
            "error": sel_resp.debug.get("error"),
        },
    }


def main() -> int:
    random.seed(42)
    questions = load_questions()
    answerable = [q for q in questions if not q.insufficient_evidence]
    ie = [q for q in questions if q.insufficient_evidence]
    selected = random.sample(answerable, 10) + random.sample(ie, 10)
    random.shuffle(selected)
    print(f"Loaded {len(questions)} questions; selected {len(selected)} "
          f"(10 answerable + 10 IE)", flush=True)

    llm_client = GroundedLLMClient()
    service = GroundedGenerationService(llm_client=llm_client)
    # JEV-guided mode; will fail-closed to multi-candidate since no JEV API key.
    sel = VariantSelector(service, enabled=True, use_jev=True)

    results = []
    out = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "jev_variant_selector_live.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Resume from prior results if present
    prior_qids = set()
    if out.exists():
        try:
            prior = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(prior, list):
                results = prior
                prior_qids = {r["qid"] for r in results if not r.get("error")}
                print(f"Resuming: {len(prior_qids)} questions already completed", flush=True)
        except Exception:
            print("  [resume: could not read prior results, starting fresh]", flush=True)

    for q in selected:
        if q.question_id in prior_qids:
            print(f"\n=== {q.question_id} [{'IE' if q.insufficient_evidence else 'ANS'}] (SKIP - done) ===", flush=True)
            continue
        print(f"\n=== {q.question_id} [{'IE' if q.insufficient_evidence else 'ANS'}] ===", flush=True)
        chunks = retrieve_chunks(q.question)
        print(f"  chunks={len(chunks)}", flush=True)
        try:
            r = run_one(service, sel, q.question, chunks,
                        q.acceptable_conclusion, q.insufficient_evidence)
            r["qid"] = q.question_id
            r["insufficient_evidence"] = q.insufficient_evidence
            r["question"] = q.question
            results.append(r)
            b, s = r["baseline"], r["selector"]
            print(f"  baseline:   len={b['length']:5d} soft={b['soft']:.3f} abstain={b['abstained']} finish={b['finish']}", flush=True)
            print(f"  selector:   len={s['length']:5d} soft={s['soft']:.3f} abstain={s['abstained']} finish={s['finish']}", flush=True)
        except Exception as exc:
            print(f"  ERROR: {exc}", flush=True)
            results.append({"qid": q.question_id, "error": str(exc)})
        # Write incrementally after each question so progress survives timeouts
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Aggregate on answerable only
    ans = [r for r in results if not r.get("error") and not r.get("insufficient_evidence")]
    if ans:
        bl_len = statistics.mean(r["baseline"]["length"] for r in ans)
        bl_soft = statistics.mean(r["baseline"]["soft"] for r in ans)
        bl_ab = sum(r["baseline"]["abstained"] for r in ans) / len(ans)
        sel_len = statistics.mean(r["selector"]["length"] for r in ans)
        sel_soft = statistics.mean(r["selector"]["soft"] for r in ans)
        sel_ab = sum(r["selector"]["abstained"] for r in ans) / len(ans)
        print("\n" + "=" * 60)
        print("LIVE EVALUATION: JEV VARIANT SELECTOR (answerable only)")
        print("=" * 60)
        print(f"  n={len(ans)}")
        print(f"  baseline:    len={bl_len:.0f} soft={bl_soft:.4f} abstain={bl_ab:.1%}")
        print(f"  selector:    len={sel_len:.0f} soft={sel_soft:.4f} abstain={sel_ab:.1%}")
        print(f"  delta_len:   {(sel_len-bl_len)/bl_len*100:+.1f}%  (target <= -15%)")
        print(f"  delta_soft:  {sel_soft-bl_soft:+.4f}  (target >= -0.02)")
        print(f"  delta_abst:  {(sel_ab-bl_ab)*100:+.1f}pp  (target <= +0.10pp)")
        b_ok = (sel_len-bl_len)/bl_len*100 <= -15
        c_ok = sel_soft-bl_soft >= -0.02
        a_ok = (sel_ab-bl_ab) <= 0.10
        print(f"  brevity:     {'PASS' if b_ok else 'FAIL'}")
        print(f"  correctness: {'PASS' if c_ok else 'FAIL'}")
        print(f"  abstention:  {'PASS' if a_ok else 'FAIL'}")
        print(f"  ALL THREE:   {'PASS' if (b_ok and c_ok and a_ok) else 'FAIL'}")
    print(f"\nResults written to: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
