# Build workflow_evaluator.py part 7

path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

parts = [
    "",
    "# ---------------------------------------------------------------------------",
    "# Structure",
    "# ---------------------------------------------------------------------------",
    "",
    "",
    'def score_structure(answer: str, query: str = "") -> EvalScore:',
    '    text = (answer or "").strip()',
    "    if not text:",
    '        return EvalScore(name="structure", score=0.0, explanation="Empty.", detail={"note": "empty"})',
    "    step_count = len(_extract_steps(text))",
    "    if step_count == 0:",
    '        step_count = 1 if re.search(r"\\d+\\.", text) else (2 if re.search(r"\\n", text) else 0)',
    "    if step_count == 0:",
    "        score = 0.3",
    '        explanation = "Narrative only."',
    "    elif step_count <= 3:",
    "        score = 0.7",
    '        explanation = f"{step_count} steps."',
    "    elif step_count <= 7:",
    "        score = 0.85",
    '        explanation = f"{step_count} steps; good coverage."',
    "    else:",
    "        score = 0.95",
    '        explanation = f"{step_count} steps; comprehensive."',
    '    return EvalScore(name="structure", score=round(score, 4), explanation=explanation, detail={"steps_found": step_count})',
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
