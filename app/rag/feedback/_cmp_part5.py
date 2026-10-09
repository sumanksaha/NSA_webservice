# Build workflow_comparator.py part 5

path = r"C:/github/NSA_webservice/app/rag/feedback/workflow_comparator.py"

parts = [
    "",
    "# ---------------------------------------------------------------------------",
    "# Main comparison entry point",
    "# ---------------------------------------------------------------------------",
    "",
    "",
    "def compare_workflow_answers(query: str, system_answer: str, user_answer: str, gold_answer: str | None = None) -> WorkflowAnswerComparison:",
    '    """Compare system and user answers for the same query."""',
    "    missing_forms = find_missing_forms(system_answer, user_answer)",
    "    extra_forms = find_extra_forms(system_answer, user_answer)",
    "    missing_steps = find_missing_steps(system_answer, user_answer)",
    "    extra_steps = find_extra_steps(system_answer, user_answer)",
    "    citation_issues = find_citation_issues(system_answer, user_answer)",
    "    factual_discrepancies = find_factual_discrepancies(system_answer, user_answer)",
    "    total_issues = len(missing_forms) + len(extra_forms) + len(missing_steps) + len(extra_steps) + len(citation_issues) + len(factual_discrepancies)",
    "    if total_issues == 0:",
    '        rating = "exact_match"',
    "    elif total_issues <= 2:",
    '        rating = "minor_discrepancies"',
    "    else:",
    '        rating = "significant_discrepancies"',
    "    return WorkflowAnswerComparison(query=query, system_answer=system_answer, user_answer=user_answer, gold_answer=gold_answer, missing_forms=missing_forms, extra_forms=extra_forms, missing_steps=missing_steps, extra_steps=extra_steps, citation_issues=citation_issues, factual_discrepancies=factual_discrepancies, overall_rating=rating)",
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
