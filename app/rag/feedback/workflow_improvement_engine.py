"""Workflow improvement engine -- generate improvement recommendations and training data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag.evaluation.workflow_evaluator import WorkflowAnswerEvaluation, evaluate_workflow_answer
from app.rag.feedback.workflow_comparator import (
    WorkflowAnswerComparison,
    compare_workflow_answers,
)

__all__ = [
    "ImprovementRecommendation",
    "WorkflowImprovementEngine",
    "WorkflowImprovementResult",
    "generate_improvement_recommendations",
    "generate_training_entry",
]


@dataclass(frozen=True)
class ImprovementRecommendation:
    """Single improvement recommendation."""

    category: str
    severity: str  # "critical", "moderate", "advisory"
    title: str
    description: str
    related_tests: list[str] = None
    affected_modules: list[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "related_tests": self.related_tests or [],
            "affected_modules": self.affected_modules or [],
        }


@dataclass(frozen=True)
class WorkflowImprovementResult:
    """Complete improvement analysis result."""

    query: str
    system_answer: str
    user_answer: str
    gold_answer: str | None = None
    evaluation: WorkflowAnswerEvaluation | None = None
    comparison: WorkflowAnswerComparison | None = None
    recommendations: list[ImprovementRecommendation] = None
    training_entries: list[dict[str, Any]] = None
    summary: str = ""

    @property
    def has_recommendations(self) -> bool:
        return bool(self.recommendations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "evaluation": self.evaluation.to_dict() if self.evaluation else None,
            "comparison": self.comparison.to_dict() if self.comparison else None,
            "recommendations": [r.to_dict() for r in self.recommendations] if self.recommendations else [],
            "training_entries": self.training_entries or [],
            "summary": self.summary,
        }


class WorkflowImprovementEngine:
    """Complete workflow RAG improvement loop orchestrator."""

    def __init__(self):
        self.recommendation_count = 0
        self.training_entry_count = 0

    def analyze_query(
        self, query: str, system_answer: str, user_answer: str, gold_answer: str | None = None,
    ) -> WorkflowImprovementResult:
        """Run the full improvement analysis for one query."""
        eval_result = evaluate_workflow_answer(query, system_answer, gold_answer)
        comparison = compare_workflow_answers(query, system_answer, user_answer, gold_answer)
        recommendations = generate_improvement_recommendations(comparison, eval_result)
        training_entries = generate_training_entry(
            query, system_answer, user_answer, gold_answer, comparison, eval_result,
        )
        summary = build_improvement_summary(comparison, eval_result)
        self.recommendation_count += len(recommendations)
        self.training_entry_count += len(training_entries)
        return WorkflowImprovementResult(
            query=query,
            system_answer=system_answer,
            user_answer=user_answer,
            gold_answer=gold_answer,
            evaluation=eval_result,
            comparison=comparison,
            recommendations=recommendations,
            training_entries=training_entries,
            summary=summary,
        )

    def analyze_batch(self, results: list[dict[str, Any]]) -> list[WorkflowImprovementResult]:
        """Analyze multiple queries in batch."""
        return [
            self.analyze_query(r["query"], r["system_answer"], r["user_answer"], r.get("gold_answer")) for r in results
        ]


def generate_improvement_recommendations(
    comparison: WorkflowAnswerComparison, evaluation: WorkflowAnswerEvaluation | None,
) -> list[ImprovementRecommendation]:
    """Generate improvement recommendations from comparison and evaluation."""
    recommendations = []

    if comparison.missing_forms:
        recommendations.append(
            ImprovementRecommendation(
                category="form_retrieval",
                severity="critical",
                title="Missing forms in answer: " + ", ".join(comparison.missing_forms),
                description="The system answer did not include Form(s) which are present in the expected answer. The retrieval step may be missing workflow document chunks.",
                related_tests=["test_document_loader", "test_ingestion_pipeline"],
                affected_modules=["retrieval", "chunker", "metadata_adapter", "ingestion"],
            ),
        )

    if comparison.extra_forms:
        recommendations.append(
            ImprovementRecommendation(
                category="hallucination",
                severity="critical",
                title="Extraneous forms in answer: " + ", ".join(comparison.extra_forms),
                description="The system answer included Form(s) which are not in the expected answer. The LLM generation step may be fabricating information.",
                related_tests=["test_document_loader", "benchmark_workflow_v1.0"],
                affected_modules=["generation", "prompt_template", "food_answer"],
            ),
        )

    if comparison.missing_steps:
        recommendations.append(
            ImprovementRecommendation(
                category="step_coverage",
                severity="moderate",
                title="Missing steps in answer: " + str(len(comparison.missing_steps)) + " step(s)",
                description="The system answer omitted procedural step(s). The workflow document may not be retrieved with sufficient context.",
                related_tests=["test_ingestion_pipeline"],
                affected_modules=["retrieval", "generation", "prompt_template"],
            ),
        )

    if evaluation:
        if evaluation.faithfulness.score < 0.5:
            recommendations.append(
                ImprovementRecommendation(
                    category="faithfulness",
                    severity="critical",
                    title="Low faithfulness: " + str(round(evaluation.faithfulness.score, 2)),
                    description="Only of claims are supported by evidence. The retrieval system is not fetching relevant chunks.",
                    related_tests=["benchmark_workflow_v1.0"],
                    affected_modules=["retrieval", "indexer", "dense_retriever", "sparse_retriever"],
                ),
            )
        if evaluation.completeness.score < 0.5:
            recommendations.append(
                ImprovementRecommendation(
                    category="completeness",
                    severity="critical",
                    title="Low completeness: " + str(round(evaluation.completeness.score, 2)),
                    description="Answer covers only partial requirements. The chunker may be splitting workflow sections too aggressively.",
                    related_tests=["benchmark_workflow_v1.0"],
                    affected_modules=["chunker", "metadata_adapter", "retrieval"],
                ),
            )

    return recommendations


def generate_training_entry(
    query: str,
    system_answer: str,
    user_answer: str,
    gold_answer: str | None,
    comparison: WorkflowAnswerComparison,
    evaluation: WorkflowAnswerEvaluation | None,
) -> list[dict[str, Any]]:
    """Generate training data entries from the comparison."""
    entries = []

    entries.append({
        "query": query,
        "system_answer": system_answer,
        "user_answer": user_answer,
        "gold_answer": gold_answer,
        "intent": "workflow_seizure_sampling",
        "type": "answer_comparison",
        "discrepancies": {
            "missing_forms": comparison.missing_forms or [],
            "extra_forms": comparison.extra_forms or [],
            "missing_steps": comparison.missing_steps or [],
            "extra_steps": comparison.extra_steps or [],
            "citation_issues": comparison.citation_issues or [],
            "factual_discrepancies": comparison.factual_discrepancies or [],
        },
        "evaluation": {
            "faithfulness": evaluation.faithfulness.score if evaluation else None,
            "completeness": evaluation.completeness.score if evaluation else None,
            "citation_quality": evaluation.citation_quality.score if evaluation else None,
            "structure": evaluation.structure.score if evaluation else None,
            "overall": evaluation.overall.score if evaluation else None,
        },
        "created_at": "2026-10-07",
    })

    if comparison.missing_forms or comparison.extra_forms:
        for form in set((comparison.missing_forms or []) + (comparison.extra_forms or [])):
            entries.append({
                "query": query,
                "pattern": "form_" + form,
                "type": "form_detection_failure",
                "error": {
                    "missing_forms": comparison.missing_forms or [],
                    "extra_forms": comparison.extra_forms or [],
                },
                "lesson": "Query containing Form should retrieve workflow chunks with form definitions.",
                "created_at": "2026-10-07",
            })

    if comparison.missing_steps:
        entries.append({
            "query": query,
            "pattern": "step_coverage",
            "type": "step_coverage_gap",
            "error": {
                "missing_steps": comparison.missing_steps,
            },
            "lesson": "Workflow procedure answers must include all expected steps in order.",
            "created_at": "2026-10-07",
        })

    return entries


def build_improvement_summary(comparison: WorkflowAnswerComparison, evaluation: WorkflowAnswerEvaluation | None) -> str:
    """Build a human-readable improvement summary."""
    parts = []

    if comparison.missing_forms:
        parts.append("MISSING FORMS: " + ", ".join(comparison.missing_forms))
    if comparison.extra_forms:
        parts.append("EXTRA FORMS (hallucination): " + ", ".join(comparison.extra_forms))
    if comparison.missing_steps:
        parts.append("MISSING STEPS: " + str(len(comparison.missing_steps)) + " step(s)")
    if evaluation:
        parts.append("FAITHFULNESS: " + str(round(evaluation.faithfulness.score, 2)))
        parts.append("COMPLETENESS: " + str(round(evaluation.completeness.score, 2)))

    if not parts:
        return "No discrepancies. Answer matches system output."

    return chr(10).join(parts)
