# Build workflow_improvement_engine.py part 1

path = r"C:/github/NSA_webservice/app/rag/feedback/workflow_improvement_engine.py"

parts = [
    '"""Workflow improvement engine -- generate improvement recommendations and training data."""',
    "",
    "from __future__ import annotations",
    "from typing import Any",
    "",
    "from app.rag.evaluation.workflow_evaluator import evaluate_workflow_answer, WorkflowAnswerEvaluation",
    "from app.rag.feedback.workflow_comparator import compare_workflow_answers, find_missing_forms, find_extra_forms, find_missing_steps, find_extra_steps, build_improvement_summary",
    "",
    "__all__ = [",
    "    WorkflowImprovementResult,",
    "    ImprovementRecommendation,",
    "    WorkflowImprovementEngine,",
    "    generate_training_entry,",
    "    generate_improvement_recommendations,",
    "]",
]

with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(parts))
