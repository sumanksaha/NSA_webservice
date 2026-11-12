import json, random, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.benchmark import load_questions
from evaluation.brevity_experiment import retrieve_chunks
from app.rag.generation.grounded_service import GroundedGenerationService
from app.rag.generation.llm_client import GroundedLLMClient
from app.rag.generation.variant_selector import VariantSelector
from evaluation.answer_scoring import score_answer


def is_abst(text):
    if not text or not text.strip():
        return True
    tt = text.strip().lower()
    markers = [
        "cannot be determined", "cannot determine", "not established",
        "does not establish", "not in the context", "not mentioned",
        "insufficient evidence", "no information", "unable to answer",
        "does not provide", "not specified", "not stated",
    ]
    return any(m in tt for m in markers)


def main():
    random.seed(42)
    questions = load_questions()
    ans = [q for q in questions if not q.insufficient_evidence]
    ie = [q for q in questions if q.insufficient_evidence]
    sel_qs = random.sample(ans, 3) + random.sample(ie, 2)
    random.shuffle(sel_qs)

    llm = GroundedLLMClient()
    svc = GroundedGenerationService(llm_client=llm)
    sel = VariantSelector(service=svc, enabled=True)

    results = []
    for q in sel_qs:
        chunks = retrieve_chunks(q.question)
        br = svc.generate_with_prompts(query=q.question, chunks=chunks, query_type="")
        bt = br.answer or ""
        bs = score_answer(bt, q.acceptable_conclusion, q.insufficient_evidence)
        sr = sel.generate_with_prompts(query=q.question, chunks=chunks, query_type="")
        st = sr.answer or ""
        ss = score_answer(st, q.acceptable_conclusion, q.insufficient_evidence)
        results.append((q.question_id, q.insufficient_evidence, bt, bs, st, ss))
        print(f"{q.question_id} base_len={len(bt)} base_soft={bs['answer_correctness']:.3f} base_abst={is_abst(bt)} | sel_len={len(st)} sel_soft={ss['answer_correctness']:.3f} sel_abst={is_abst(st)}", flush=True)

    print("\n=== ANSWERABLE ONLY ===", flush=True)
    an = [r for r in results if not r[1]]
    if an:
        bl_l = statistics.mean(len(r[2]) for r in an)
        bl_s = statistics.mean(r[3]["answer_correctness"] for r in an)
        bl_a = sum(is_abst(r[2]) for r in an) / len(an)
        sl_l = statistics.mean(len(r[4]) for r in an)
        sl_s = statistics.mean(r[5]["answer_correctness"] for r in an)
        sl_a = sum(is_abst(r[4]) for r in an) / len(an)
        print(f"n={len(an)}", flush=True)
        print(f"baseline:   len={bl_l:.0f} soft={bl_s:.4f} abstain={bl_a:.1%}", flush=True)
        print(f"selector:   len={sl_l:.0f} soft={sl_s:.4f} abstain={sl_a:.1%}", flush=True)
        print(f"delta_len:  {(sl_l-bl_l)/bl_l*100:+.1f}%  (target <= -15%)", flush=True)
        print(f"delta_soft: {sl_s-bl_s:+.4f}  (target >= -0.02)", flush=True)
        print(f"delta_abst: {(sl_a-bl_a)*100:+.1f}pp  (target <= +0.10pp)", flush=True)


if __name__ == "__main__":
    main()
