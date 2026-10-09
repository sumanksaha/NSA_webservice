# Build test_workflow_improvement.py part 2c

path = r"C:/github/NSA_webservice/tests/_test_workflow_improvement.py"

parts = [
    "",
    "def test_improvement_engine_integration():",
    '    """Run full improvement analysis for a workflow QA pair."""',
    "    engine = WorkflowImprovementEngine()",
    "    ",
    '    query = "Which form does the FSO give to the business operator as a notice?"',
    '    gold = "The FSO gives a notice to the business operator in Form V, Regulation 2.4.1.3."',
    '    system = "The FSO gives a notice to the business operator in Form V, Regulation 2.4.1.3."',
    '    user = "The FSO gives Form V to the business operator as a notice (Regulation 2.4.1.3). Form VIII is for appeals."',
    "    ",
    "    result = engine.analyze_query(query, system, user, gold)",
    "    ",
    "    assert result.evaluation.faithfulness.score >= 0.0",
    "    assert result.evaluation.completeness.score >= 0.0",
    "    assert result.comparison.has_discrepancies or not (result.comparison.missing_forms or result.comparison.extra_forms)",
    "    assert len(result.training_entries) >= 1",
    "    assert len(result.summary) > 0",
    "    ",
    "    return result",
    "",
    "",
    'if __name__ == "__main__":',
    '    print("Running workflow improvement tests...")',
    "    test_improvement_engine_integration()",
    '    print("All tests passed!")',
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
