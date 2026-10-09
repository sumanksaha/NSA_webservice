# Build test_workflow_improvement.py part 2a

path = r"C:/github/NSA_webservice/tests/_test_workflow_improvement.py"

parts = [
    "",
    "class TestWorkflowAnswerEvaluation:",
    '    """Tests for workflow answer evaluation."""',
    "    ",
    "    def test_evaluate_workflow_answer(self):",
    '        query = "Which form does the FSO give to the business operator as a notice?"',
    '        gold = "The FSO gives a notice to the business operator in Form V, Regulation 2.4.1.3."',
    '        answer = "The FSO gives a notice to the business operator in Form V, Regulation 2.4.1.3."',
    "        result = evaluate_workflow_answer(query, answer, gold)",
    "        ",
    "        assert result.faithfulness.score >= 0.0",
    "        assert result.completeness.score >= 0.0",
    "        assert result.citation_quality.score >= 0.0",
    "        assert result.structure.score >= 0.0",
    "        assert result.overall.score >= 0.0",
    "        ",
    "    def test_form_coverage(self):",
    '        query = "Which form...?"',
    '        gold = "Form V is used for notice."',
    '        answer = "Form V is used."',
    "        result = evaluate_workflow_answer(query, answer, gold)",
    "        assert result.gold_form_coverage > 0.0",
    "        ",
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
