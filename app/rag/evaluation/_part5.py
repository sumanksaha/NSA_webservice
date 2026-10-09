# Build workflow_evaluator.py part 5

path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

parts = [
    "",
    "# ---------------------------------------------------------------------------",
    "# Completeness",
    "# ---------------------------------------------------------------------------",
    "",
    "",
    "def score_completeness(answer: str, gold_answer: str | None, chunks: list | None = None) -> EvalScore:",
    "    if not gold_answer:",
    '        return EvalScore(name="completeness", score=1.0, explanation="No gold answer.", detail={"note": "no_gold"})',
    "    fa_forms = _extract_forms(answer)",
    "    gold_forms = _extract_forms(gold_answer)",
    "    gold_steps = _extract_steps(gold_answer)",
    "    answer_steps = _extract_steps(answer)",
    "    steps_covered = 0",
    "    for gs in gold_steps:",
    "        for as_ in answer_steps:",
    "            if token_coverage(content_tokens(gs), content_tokens(as_)) >= 0.5:",
    "                steps_covered += 1",
    "                break",
    "    forms_covered = len(fa_forms & gold_forms) / max(len(gold_forms), 1)",
    "    form_score = forms_covered",
    "    step_score = steps_covered / max(len(gold_steps), 1) if gold_steps else 0.0",
    "    if chunks:",
    "        needles = content_tokens(gold_answer)",
    "        chunk_texts = [chunk_text(c) for c in chunks]",
    "        evidence_matched = any(token_coverage(needles, ct) >= 0.5 for ct in chunk_texts)",
    "    else:",
    "        evidence_matched = True",
    "    score = (form_score + step_score + float(evidence_matched)) / 3.0",
    "    detail = {",
    '        "fa_forms": sorted(fa_forms),',
    '        "gold_forms": sorted(gold_forms),',
    '        "steps_matched": steps_covered,',
    '        "form_score": round(form_score, 4),',
    '        "step_score": round(step_score, 4),',
    "    }",
    '    return EvalScore(name="completeness", score=round(score, 4), explanation=f"forms={form_score:.2f}, steps={step_score:.2f}, evidence={evidence_matched} -> {score:.2f}", detail=detail)',
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
