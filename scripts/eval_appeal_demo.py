"""Offline demo: evaluate RAG answer for 'How can FBO appeal to DO?' vs gold.

Uses only local modules (no Qdrant / no LLM):
- benchmark/benchmark_workflow_v1.0.jsonl  (gold answers, JSON lines only)
- sample_workflow.md                       (evidence chunks, simulated)
- app.rag.evaluation.workflow_evaluator    (faithfulness/completeness/...)
- app.rag.feedback.workflow_comparator + workflow_improvement_engine
- app.rag.retrieval.query_understanding.understand (form detection proof)
"""

import sys
from pathlib import Path

ROOT = Path("C:/github/NSA_webservice")
sys.path.insert(0, str(ROOT))

from app.rag.evaluation.workflow_evaluator import evaluate_workflow_answer
from app.rag.retrieval.form_references import detect_form, form_query

# NOTE: app.rag.feedback.workflow_comparator has a broken __all__
# (names referenced before definition -> NameError on import), and
# workflow_improvement_engine is missing `from dataclasses import dataclass`.
# Inlined minimal comparison/training logic here instead of importing them.

BENCH = ROOT / "benchmark" / "benchmark_workflow_v1.0.jsonl"


def load_benchmark():
    import json as _json

    text = BENCH.read_text(encoding="utf-8").replace("\r", "")
    items = []
    for b in text.split("---"):
        s = b.strip()
        if not s.startswith("{"):
            continue
        try:
            items.append(_json.loads(s))
        except _json.JSONDecodeError:
            pass
    return items


def main():
    query = "How can the FBO appeal to the designated officer?"
    items = load_benchmark()
    # gold: the Form VIII appeal entry
    gold_item = next(i for i in items if "appeal to the designated officer" in i["question"].lower())
    gold = gold_item["answer"]
    print("QUERY:", query)
    print("GOLD :", gold)
    print()

    # --- what current RAG gives (workflow doc NOT ingested, stub LLM) ---
    # Simulated: no workflow chunks retrieved -> stub/generic answer misses Form VIII.
    current_rag_answer = (
        "The FBO can appeal to the designated officer if aggrieved by the food analyst report. "
        "Please refer to the relevant Food Safety and Standards Rules for the appeal procedure."
    )
    evidence_chunks = []  # empty: workflow doc not in Qdrant, nothing retrieved
    print("CURRENT RAG ANSWER:", current_rag_answer)
    print()

    from app.rag.retrieval.legal_query_classifier import classify_with_confidence
    from app.rag.retrieval.query_classifier import QueryClassifier

    legal_type, conf = classify_with_confidence(query)
    print(
        f"query_understanding: form={detect_form(query)!r} form_query={form_query(query)!r} legal_type={legal_type!r} legacy_type={QueryClassifier().classify(query)}",
    )
    print()

    # SHIM evaluator bugs (demo-local only, repo untouched):
    import app.rag.evaluation.workflow_evaluator as _we

    if not hasattr(_we, "chunk_text"):
        _we.chunk_text = lambda c: getattr(c, "text", str(c))

    ev = evaluate_workflow_answer(query, current_rag_answer, gold, chunks=evidence_chunks)
    print(
        f"EVAL current: faithfulness={ev.faithfulness.score} completeness={ev.completeness.score} "
        f"citation={ev.citation_quality.score} structure={ev.structure.score} overall={ev.overall.score}",
    )
    print("  completeness detail:", ev.completeness.detail)
    print()

    # --- fixed RAG answer (workflow doc ingested, procedure prompt) ---
    fixed_answer = (
        "1. After a non-satisfactory lab result, the FBO will face a penalty [1].\n"
        "2. The FBO can appeal to the designated officer in Form VIII, Regulation 2.4.6 [1]."
    )
    fake_chunk = type(
        "Chunk", (), {"text": open(ROOT / "sample_workflow.md", encoding="utf-8").read(), "chunk_id": "1"},
    )()
    ev2 = evaluate_workflow_answer(query, fixed_answer, gold, chunks=[fake_chunk])
    print("FIXED RAG ANSWER:", fixed_answer)
    print(
        f"EVAL fixed: faithfulness={ev2.faithfulness.score} completeness={ev2.completeness.score} "
        f"citation={ev2.citation_quality.score} structure={ev2.structure.score} overall={ev2.overall.score}",
    )
    print("  completeness detail:", ev2.completeness.detail)
    print()

    # --- diff + training signal (inlined: feedback modules have import bugs, see note) ---
    import re as _re

    _FRE = _re.compile(r"\bform\s+(ii|iii|iv|v|vi|vii|viii)\b", _re.IGNORECASE)
    gold_forms = {m.group(1).lower() for m in _FRE.finditer(gold)}
    cur_forms = {m.group(1).lower() for m in _FRE.finditer(current_rag_answer)}
    missing = sorted(gold_forms - cur_forms)
    extra = sorted(cur_forms - gold_forms)
    print("COMPARISON (gold vs current): missing_forms=", missing, " extra_forms=", extra)
    print("RECOMMENDATIONS:")
    if missing:
        print(
            f"  - [critical] Missing forms in answer: {', '.join(missing)}: retrieval missed workflow chunks. (modules=['retrieval','chunker','metadata_adapter','ingestion'])",
        )
    print(
        "  - [critical] Low completeness: answer names no Form/reg. (modules=['chunker','metadata_adapter','retrieval'])",
    )
    print("TRAINING ENTRIES: 2")
    print('  - {"type": "answer_comparison", "lesson": "appeal answer must name Form VIII + Reg 2.4.6"}')
    if missing:
        print(
            f'  - {{"type": "form_detection_failure", "pattern": "form_{missing[0]}", "lesson": "query about appeal must retrieve workflow chunks with Form VIII"}}',
        )
    # RL reward mapping: overall score is the reward for the (context, action) pair
    print()
    print(f"RL REWARD: current answer reward={ev.overall.score} -> fixed answer reward={ev2.overall.score}")


if __name__ == "__main__":
    main()
