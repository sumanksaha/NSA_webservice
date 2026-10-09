# Build workflow_improvement_engine.py part 6

path = r"C:/github/NSA_webservice/app/rag/feedback/workflow_improvement_engine.py"

parts = [
    "",
    "def build_improvement_summary(comparison: WorkflowAnswerComparison, evaluation: WorkflowAnswerEvaluation | None) -> str:",
    '    """Build a human-readable improvement summary."""',
    "    parts = []",
    "    ",
    "    if comparison.missing_forms:",
    '        parts.append("MISSING FORMS: " + ", ".join(comparison.missing_forms))',
    "    if comparison.extra_forms:",
    '        parts.append("EXTRA FORMS (hallucination): " + ", ".join(comparison.extra_forms))',
    "    if comparison.missing_steps:",
    '        parts.append("MISSING STEPS: " + str(len(comparison.missing_steps)) + " step(s)")',
    "    if evaluation:",
    '        parts.append("FAITHFULNESS: " + str(round(evaluation.faithfulness.score, 2)))',
    '        parts.append("COMPLETENESS: " + str(round(evaluation.completeness.score, 2)))',
    "    ",
    "    if not parts:",
    '        return "No discrepancies. Answer matches system output."',
    "    ",
    "    return chr(10).join(parts)",
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
