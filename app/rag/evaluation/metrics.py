"""Evaluation metrics — coverage, confidence, and RAGAS-style reference scores."""

from __future__ import annotations

from app.rag.evaluation.textmatch import chunk_text, content_tokens, token_coverage
from app.rag.evidence_task import EvidenceTask


class EvalScore:
    """One evaluation metric's score and explanation."""

    def __init__(self, name: str, score: float, explanation: str, detail: dict | None = None) -> None:
        self.name = name
        self.score = score
        self.explanation = explanation
        self.detail = detail or {}


class SeparateConfidenceMetrics:
    """Tracks separate confidence metrics: R (retrieval), E (evidence coverage), C (citation correctness),
    G (claim groundedness), and A (answer completeness)."""

    def __init__(self) -> None:
        self.R = 0.0  # retrieval confidence
        self.E = 0.0  # evidence coverage
        self.C = 0.0  # citation correctness
        self.G = 0.0  # claim groundedness
        self.A = 0.0  # answer completeness

    def update(
        self,
        retrieval_confidence: float,
        evidence_coverage: float,
        citation_correctness: bool,
        claim_groundedness: float,
        answer_completeness: bool,
    ) -> EvalScore:
        """Update metrics and return a summary score."""
        self.R = retrieval_confidence
        self.E = evidence_coverage
        self.C = 1.0 if citation_correctness else 0.0
        self.G = claim_groundedness
        self.A = 1.0 if answer_completeness else 0.0

        # Weighted average (equal weights for simplicity)
        final_score = (self.R + self.E + self.C + self.G + self.A) / 5.0

        # Detailed explanation
        explanation = f"R={self.R:.2f}, E={self.E:.2f}, C={self.C:.2f}, G={self.G:.2f}, A={self.A:.2f}"

        return EvalScore(
            name="separate_confidence_metrics",
            score=final_score,
            explanation=explanation,
            detail={
                "R": self.R,
                "E": self.E,
                "C": self.C,
                "G": self.G,
                "A": self.A,
            },
        )




class CoverageMetrics:
    """Tracks requirement coverage across Evidence Tasks.

    Measures:
    - task_coverage: |tasks with evidence| / |total tasks|
    - requirement_coverage: |requirements covered| / |total requirements|
    - evidence_coverage: |evidence found| / |total evidence requirements|
    """

    #: A chunk counts as task evidence when at least this fraction of the
    #: task question's content tokens appear (fuzzily) in its text.
    EVIDENCE_MATCH_THRESHOLD = 0.5

    def __init__(self) -> None:
        self.total_tasks = 0
        self.tasks_with_evidence = 0
        self.total_requirements = 0
        self.requirements_covered = 0
        self.missing: list[str] = []

    def update(
        self,
        evidence_tasks: list | None = None,
        retrieval_plan: dict[str, list[str]] | None = None,
        chunks: list | None = None,
    ) -> CoverageMetrics:
        """Update coverage metrics based on Evidence Tasks and retrieved chunks.

        Args:
            evidence_tasks: List of EvidenceTask objects.
            retrieval_plan: Dict mapping task_id -> retrieval routes.
            chunks: List of retrieved chunk dicts.

        Returns:
            Self for chaining.
        """
        tasks = evidence_tasks or []
        self.total_tasks = len(tasks)

        # Requirement coverage from retrieval_plan (independent of whether
        # tasks are present — plan-only calls are still meaningful).
        if retrieval_plan:
            self.total_requirements = len(retrieval_plan)
            self.requirements_covered = sum(
                1 for routes in retrieval_plan.values() if routes
            )

        if not tasks:
            return self

        # Track which tasks have evidence (chunks retrieved for them)
        for task in tasks:
            if not isinstance(task, EvidenceTask):
                continue
            # Task has evidence when some chunk's *text* addresses the task
            # question (fuzzy token coverage).  The previous entity-metadata
            # overlap broke on chunks with missing/normalized-differently
            # metadata and ignored the actual question being asked.
            needles = content_tokens(task.question) or content_tokens(task.objective)
            has_evidence = any(
                token_coverage(needles, chunk_text(c)) >= self.EVIDENCE_MATCH_THRESHOLD
                for c in (chunks or [])
            )
            if has_evidence:
                self.tasks_with_evidence += 1
            else:
                self.missing.append(task.task_id)

        return self

    @property
    def task_coverage(self) -> float:
        """Fraction of tasks with evidence."""
        return round(self.tasks_with_evidence / self.total_tasks, 4) if self.total_tasks else 0.0

    @property
    def requirement_coverage(self) -> float:
        """Fraction of retrieval requirements with routes."""
        return (
            round(self.requirements_covered / self.total_requirements, 4)
            if self.total_requirements
            else 0.0
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_tasks": self.total_tasks,
            "tasks_with_evidence": self.tasks_with_evidence,
            "task_coverage": self.task_coverage,
            "total_requirements": self.total_requirements,
            "requirements_covered": self.requirements_covered,
            "requirement_coverage": self.requirement_coverage,
            "missing": self.missing,
        }
