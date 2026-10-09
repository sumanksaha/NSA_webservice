"""Tests for the workflow query understanding and improvement system."""

from app.rag.evaluation.workflow_evaluator import evaluate_workflow_answer
from app.rag.feedback.workflow_comparator import (
    WorkflowAnswerComparison,
    build_improvement_summary,
    find_citation_issues,
    find_extra_forms,
    find_extra_steps,
    find_factual_discrepancies,
    find_missing_forms,
    find_missing_steps,
)
from app.rag.feedback.workflow_improvement_engine import (
    WorkflowImprovementEngine,
    generate_improvement_recommendations,
    generate_training_entry,
)
from app.rag.query_understanding.workflow_query_recognizer import (
    _FORM_NAME_ALIASES,
    WORKFLOW_INTENTS,
    extract_workflow_fields,
    recognize_workflow_query,
)


class TestWorkflowQueryRecognizer:
    """Tests for workflow query recognition."""

    def test_seizure_form_lookup(self):
        rec = recognize_workflow_query("When a food safety officer seizes an item, which forms does he provide?")
        assert rec.is_workflow_query
        assert rec.intent == "seizure_form_lookup"

    def test_sampling_process(self):
        rec = recognize_workflow_query("What happens after the FSO takes a sample?")
        assert rec.is_workflow_query
        assert rec.intent == "sampling_process"

    def test_form_name_alias_notice(self):
        rec = recognize_workflow_query("Which form does the FSO give to the business operator as a notice?")
        assert rec.is_workflow_query
        assert rec.intent == "form_reference"
        assert rec.form_mentioned == "v"
        assert "form_name" in rec.matched_patterns

    def test_form_name_alias_appeal(self):
        rec = recognize_workflow_query("Which form can the FBO use to appeal to the designated officer?")
        assert rec.is_workflow_query
        assert rec.intent == "form_reference"
        assert rec.form_mentioned == "viii"

    def test_general_query(self):
        rec = recognize_workflow_query("What is the FSS Act?")
        assert not rec.is_workflow_query
        assert rec.intent == "general"

    def test_extract_workflow_fields(self):
        fields = extract_workflow_fields("Which form does the FSO give to the business operator as a notice?")
        assert fields["intent"] == "form_reference"
        assert fields["form_mentioned"] == "v"
        assert fields["is_workflow_query"] is True

    def test_workflow_intents_vocab(self):
        assert "seizure_form_lookup" in WORKFLOW_INTENTS
        assert "sampling_process" in WORKFLOW_INTENTS
        assert "form_reference" in WORKFLOW_INTENTS

    def test_form_name_aliases(self):
        assert _FORM_NAME_ALIASES["notice"] == "v"
        assert _FORM_NAME_ALIASES["analyst report"] == "vii"
        assert _FORM_NAME_ALIASES["appeal"] == "viii"


class TestWorkflowAnswerEvaluation:
    """Tests for workflow answer evaluation."""

    def test_evaluate_workflow_answer(self):
        query = "Which form does the FSO give to the business operator as a notice?"
        gold = "The FSO gives a notice to the business operator in Form V, Regulation 2.4.1.3."
        answer = "The FSO gives a notice to the business operator in Form V, Regulation 2.4.1.3."
        result = evaluate_workflow_answer(query, answer, gold)

        assert result.faithfulness.score >= 0.0
        assert result.completeness.score >= 0.0
        assert result.citation_quality.score >= 0.0
        assert result.structure.score >= 0.0
        assert result.overall.score >= 0.0

    def test_form_coverage(self):
        query = "Which form...?"
        gold = "Form V is used for notice."
        answer = "Form V is used."
        result = evaluate_workflow_answer(query, answer, gold)
        assert result.gold_form_coverage > 0.0


class TestWorkflowAnswerComparator:
    """Tests for workflow answer comparison."""

    def test_find_missing_forms(self):
        sys = "Form II and Form III are used during seizure."
        user = "Form II is used during seizure."
        missing = find_missing_forms(sys, user)
        assert "iii" in missing

    def test_find_extra_forms(self):
        sys = "Form V is used."
        user = "Form V and Form VIII are used."
        extra = find_extra_forms(sys, user)
        assert "viii" in extra

    def test_find_missing_steps(self):
        sys = "Step 1: Submit to lab. Step 2: Lab analyzes. Step 3: Report."
        user = "Step 1: Submit to lab."
        missing = find_missing_steps(sys, user)
        assert len(missing) >= 2

    def test_find_extra_steps(self):
        sys = "Step 1: Submit. Step 2: Analyze."
        user = "Step 1: Submit. Step 2: Analyze. Step 3: Report."
        extra = find_extra_steps(sys, user)
        assert len(extra) >= 1

    def test_find_citation_issues(self):
        sys = ""
        user = "The answer is [1] and [2]."
        issues = find_citation_issues(sys, user)
        assert isinstance(issues, list)

    def test_find_factual_discrepancies(self):
        sys = "Form V is for notice. Form VIII is for appeal."
        user = "Form V is for notice. Form VII is for appeal."
        issues = find_factual_discrepancies(sys, user)
        assert len(issues) > 0

    def test_build_improvement_summary(self):
        comp = WorkflowAnswerComparison(query="test", system_answer="Form II", user_answer="Form V")
        comp.missing_forms = ["viii"]
        comp.extra_forms = ["v"]
        summary = build_improvement_summary(comp, None)
        assert "MISSING FORMS" in summary
        assert "EXTRA FORMS" in summary

    def test_comparison_has_discrepancies(self):
        comp = WorkflowAnswerComparison(query="test", system_answer="Form II", user_answer="Form V")
        comp.missing_forms = ["viii"]
        assert comp.has_discrepancies

        comp2 = WorkflowAnswerComparison(query="test", system_answer="Form II", user_answer="Form II")
        comp2.missing_forms = []
        assert not comp2.has_discrepancies


class TestWorkflowImprovementEngine:
    """Tests for workflow improvement engine."""

    def test_generate_improvement_recommendations(self):
        from app.rag.evaluation.workflow_evaluator import EvalScore, WorkflowAnswerEvaluation
        from app.rag.feedback.workflow_comparator import WorkflowAnswerComparison

        comp = WorkflowAnswerComparison(query="test", system_answer="Form II", user_answer="Form V")
        comp.missing_forms = ["viii"]
        comp.extra_forms = ["v"]

        eval_res = WorkflowAnswerEvaluation(
            query="test",
            answer="Form V",
            gold_answer="Form V",
            faithfulness=EvalScore("faithfulness", 1.0, "", {}),
            completeness=EvalScore("completeness", 1.0, "", {}),
            citation_quality=EvalScore("citation_quality", 1.0, "", {}),
            structure=EvalScore("structure", 1.0, "", {}),
            overall=EvalScore("overall", 1.0, "", {}),
        )

        recs = generate_improvement_recommendations(comp, eval_res)
        assert len(recs) > 0
        assert any(r.category == "form_retrieval" for r in recs)
        assert any(r.category == "hallucination" for r in recs)

    def test_generate_training_entry(self):
        from app.rag.feedback.workflow_comparator import WorkflowAnswerComparison

        comp = WorkflowAnswerComparison(query="test", system_answer="Form II", user_answer="Form V")
        comp.missing_forms = ["viii"]
        comp.extra_forms = ["v"]

        entries = generate_training_entry("test", "Form II", "Form V", "Form V", comp, None)
        assert len(entries) >= 2
        assert any(e["type"] == "answer_comparison" for e in entries)
        assert any(e["type"] == "form_detection_failure" for e in entries)

    def test_engine_analyze_query(self):
        engine = WorkflowImprovementEngine()
        result = engine.analyze_query(
            query="Which form does the FSO give to the business operator as a notice?",
            system_answer="Form V is used for notices.",
            user_answer="Form V is used for notices. Form VIII is for appeals.",
            gold_answer="Form V is used for notices.",
        )

        assert result.query == "Which form does the FSO give to the business operator as a notice?"
        assert result.evaluation is not None
        assert result.comparison is not None
        assert result.recommendations is not None
        assert len(result.recommendations) >= 0
        assert result.training_entries is not None

        if result.comparison.missing_forms:
            assert len(result.recommendations) > 0

        assert len(result.training_entries) >= 1


def test_improvement_engine_integration():
    """Run full improvement analysis for a workflow QA pair."""
    engine = WorkflowImprovementEngine()

    query = "Which form does the FSO give to the business operator as a notice?"
    gold = "The FSO gives a notice to the business operator in Form V, Regulation 2.4.1.3."
    system = "The FSO gives a notice to the business operator in Form V, Regulation 2.4.1.3."
    user = "The FSO gives Form V to the business operator as a notice (Regulation 2.4.1.3). Form VIII is for appeals."

    result = engine.analyze_query(query, system, user, gold)

    assert result.evaluation.faithfulness.score >= 0.0
    assert result.evaluation.completeness.score >= 0.0
    assert result.comparison.has_discrepancies or not (result.comparison.missing_forms or result.comparison.extra_forms)
    assert len(result.training_entries) >= 1
    assert len(result.summary) > 0

    return result


if __name__ == "__main__":
    print("Running workflow improvement tests...")
    test_improvement_engine_integration()
    print("All tests passed!")
