"""Core Evidence Task data structures for the RAG decomposition layer.

Defines the canonical EvidenceTask object that replaces SubQuestion as the
central unit of query decomposition. Each task is independently retrievable,
verifiable, and answerable, with explicit evidence requirements, dependencies,
and answer contracts.

See RAG_IMPROVEMENTS.md for the architecture rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvidenceRequirement(StrEnum):
    """Controlled vocabulary of evidence requirements for legal queries.

    Every decomposition unit should map to one of these types.
    """

    PROVISION = "provision"
    DEFINITION = "definition"
    SCOPE = "scope"
    ELEMENT = "element"
    EXCEPTION = "exception"
    CONDITION = "condition"
    PROHIBITION = "prohibition"
    DUTY = "duty"
    RIGHT = "right"
    PENALTY = "penalty"
    OFFENCE = "offence"
    PROCEDURE = "procedure"
    AUTHORITY = "authority"
    JURISDICTION = "jurisdiction"
    TIME_LIMIT = "time_limit"
    THRESHOLD = "threshold"
    STANDARD = "standard"
    CROSS_REFERENCE = "cross_reference"
    AMENDMENT = "amendment"
    REPEAL = "repeal"
    CASE_LAW = "case_law"
    INTERPRETATION = "interpretation"
    FACT_APPLICATION = "fact_application"


@dataclass
class RetrievalPlan:
    """Structured retrieval plan for an EvidenceTask (P3).

    Replaces loose ``retrieval: dict[str, bool]`` with explicit retrieval
    routes: lexical queries, semantic queries, identifier targets,
    metadata filters, required source types, cross-reference targets,
    and temporal constraints.
    """

    lexical_queries: list[str] = field(default_factory=list)
    semantic_queries: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    metadata_filters: dict[str, Any] = field(default_factory=dict)
    required_source_types: list[str] = field(default_factory=list)
    cross_reference_targets: list[str] = field(default_factory=list)
    temporal_constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnswerContract:
    """What constitutes a successful answer for this EvidenceTask.

    The answer generator uses this to automatically verify completeness:
    check that all required_fields are present in the generated answer.
    """

    required_fields: list[str]
    optional_fields: list[str] = field(default_factory=list)
    type_hint: str | None = None  # e.g. "penalty", "provision", "citation"


@dataclass
class EvidenceTask:
    """One independently answerable evidence task from query decomposition.

    Attributes:
        task_id: Unique identifier (e.g. "T1", "T2").
        objective: What this task accomplishes (human-readable).
        question: The question this task answers.
        evidence_requirement: One of the EvidenceRequirement taxonomy.
        entities: Entities this task operates on (for retrieval scoping).
        jurisdiction: Jurisdiction constraint (e.g. "India").
        temporal_scope: Optional time window (e.g. "2024", "before 2020").
        dependency: List of task_ids this task depends on (DAG edge).
        answer_type: Expected answer format (citation, numeric, boolean, etc.).
        must_be_explicit: Whether the answer must be explicitly stated (not inferred).
        answer_contract: What fields must be present for success.
        retrieval: How this task should be retrieved.
    """

    task_id: str
    objective: str
    question: str
    evidence_requirement: EvidenceRequirement
    entities: list[str] = field(default_factory=list)
    jurisdiction: str | None = None
    temporal_scope: str | None = None
    dependency: list[str] = field(default_factory=list)
    answer_type: str = "citation"
    must_be_explicit: bool = True
    answer_contract: AnswerContract | None = None
    retrieval: RetrievalPlan = field(default_factory=RetrievalPlan)

    def with_dependency(self, dep_id: str) -> EvidenceTask:
        """Return a new task with an added dependency."""
        new = EvidenceTask(
            task_id=self.task_id,
            objective=self.objective,
            question=self.question,
            evidence_requirement=self.evidence_requirement,
            entities=list(self.entities),
            jurisdiction=self.jurisdiction,
            temporal_scope=self.temporal_scope,
            dependency=[*list(self.dependency), dep_id],
            answer_type=self.answer_type,
            must_be_explicit=self.must_be_explicit,
            answer_contract=self.answer_contract,
            retrieval=self.retrieval,
        )
        return new

    def add_entity(self, entity: str) -> EvidenceTask:
        """Return a new task with an added entity."""
        new = EvidenceTask(
            task_id=self.task_id,
            objective=self.objective,
            question=self.question,
            evidence_requirement=self.evidence_requirement,
            entities=[*list(self.entities), entity],
            jurisdiction=self.jurisdiction,
            temporal_scope=self.temporal_scope,
            dependency=list(self.dependency),
            answer_type=self.answer_type,
            must_be_explicit=self.must_be_explicit,
            answer_contract=self.answer_contract,
            retrieval=self.retrieval,
        )
        return new

    def set_jurisdiction(self, jurisdiction: str) -> EvidenceTask:
        """Return a new task with a jurisdiction set."""
        new = EvidenceTask(
            task_id=self.task_id,
            objective=self.objective,
            question=self.question,
            evidence_requirement=self.evidence_requirement,
            entities=list(self.entities),
            jurisdiction=jurisdiction,
            temporal_scope=self.temporal_scope,
            dependency=list(self.dependency),
            answer_type=self.answer_type,
            must_be_explicit=self.must_be_explicit,
            answer_contract=self.answer_contract,
            retrieval=self.retrieval,
        )
        return new

    def set_temporal(self, temporal: str) -> EvidenceTask:
        """Return a new task with a temporal scope set."""
        new = EvidenceTask(
            task_id=self.task_id,
            objective=self.objective,
            question=self.question,
            evidence_requirement=self.evidence_requirement,
            entities=list(self.entities),
            jurisdiction=self.jurisdiction,
            temporal_scope=temporal,
            dependency=list(self.dependency),
            answer_type=self.answer_type,
            must_be_explicit=self.must_be_explicit,
            answer_contract=self.answer_contract,
            retrieval=self.retrieval,
        )
        return new

    def with_answer_contract(self, required_fields: list[str]) -> EvidenceTask:
        """Return a new task with an answer contract."""
        new = EvidenceTask(
            task_id=self.task_id,
            objective=self.objective,
            question=self.question,
            evidence_requirement=self.evidence_requirement,
            entities=list(self.entities),
            jurisdiction=self.jurisdiction,
            temporal_scope=self.temporal_scope,
            dependency=list(self.dependency),
            answer_type=self.answer_type,
            must_be_explicit=self.must_be_explicit,
            answer_contract=AnswerContract(required_fields=required_fields),
            retrieval=self.retrieval,
        )
        return new


@dataclass
class TaskDAG:
    """Directed Acyclic Graph of EvidenceTasks for dependency management.

    Tracks tasks and their dependencies, enabling topological sorting
    for ordered retrieval and answering.
    """

    tasks: dict[str, EvidenceTask] = field(default_factory=dict)

    def add_task(self, task: EvidenceTask) -> None:
        """Add a task to the DAG."""
        self.tasks[task.task_id] = task

    def get_task(self, task_id: str) -> EvidenceTask | None:
        """Get a task by ID."""
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> list[EvidenceTask]:
        """Get all tasks in the DAG."""
        return list(self.tasks.values())

    def get_ready_tasks(self) -> list[EvidenceTask]:
        """Get tasks whose dependencies are all satisfied.

        A task is "ready" when all its dependencies are already completed
        (or have no dependencies themselves).
        """
        completed: set[str] = set()
        ready: list[EvidenceTask] = []

        while True:
            # Find tasks ready to execute
            new_ready: list[EvidenceTask] = []
            for task in self.get_all_tasks():
                if task.task_id in completed:
                    continue
                if all(dep in completed for dep in task.dependency):
                    new_ready.append(task)

            if not new_ready:
                # Check if there are remaining tasks with unsatisfied deps
                remaining = [t for t in self.get_all_tasks() if t.task_id not in completed]
                if remaining:
                    # Remaining tasks have circular deps or unresolved deps
                    break
                else:
                    # All tasks are completed
                    break

            ready.extend(new_ready)

            # Mark ready tasks as completed (simulating execution)
            for task in new_ready:
                completed.add(task.task_id)

        return ready

    def topological_order(self) -> list[EvidenceTask]:
        """Return tasks in topological order (dependencies first)."""

        visited: set[str] = set()
        order: list[EvidenceTask] = []

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            visited.add(task_id)
            task = self.get_task(task_id)
            if task is None:
                return
            for dep in task.dependency:
                visit(dep)
            order.append(task)

        for task in self.get_all_tasks():
            visit(task.task_id)

        return order

    def has_cycle(self) -> bool:
        """Check if the DAG has any cycles (should not happen with valid data)."""

        visited: set[str] = set()
        rec_stack: set[str] = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)

            task = self.get_task(node)
            if task is None:
                return False

            for dep in task.dependency:
                if dep not in visited:
                    if dfs(dep):
                        return True
                elif dep in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        return any(task_id not in visited and dfs(task_id) for task_id in self.tasks)


@dataclass
class CoverageMatrix:
    """Tracks which user requirements are covered by which EvidenceTasks.

    Used for RAG evaluation: measures requirement coverage, evidence coverage,
    and answer completeness.
    """

    user_requirements: list[str]  # What the user asked for
    task_coverage: dict[str, list[str]]  # task_id -> list of user reqs it covers
    evidence_status: dict[str, bool]  # user_req -> whether evidence was found
    answer_status: dict[str, bool]  # user_req -> whether answer is complete
    missing: list[str]  # user requirements with no evidence
    coverage_ratio: float  # |found| / |total|

    def add_coverage(
        self,
        user_req: str,
        task_ids: list[str],
        evidence_found: bool = False,
    ) -> CoverageMatrix:
        """Add coverage information for a user requirement."""
        new_task_coverage = dict(self.task_coverage)
        if user_req not in new_task_coverage:
            new_task_coverage[user_req] = []
        for tid in task_ids:
            if tid not in new_task_coverage[user_req]:
                new_task_coverage[user_req].append(tid)

        new_evidence = dict(self.evidence_status)
        new_evidence[user_req] = evidence_found

        new_answer = dict(self.answer_status)
        # Answer is complete if evidence found AND all task answer contracts satisfied
        # Simplified: just track evidence found
        new_answer[user_req] = evidence_found

        new_missing = [req for req, found in new_evidence.items() if not found]

        total = len(self.user_requirements) if self.user_requirements else 1
        found = sum(1 for v in new_evidence.values() if v)
        ratio = found / total if total > 0 else 0.0

        return CoverageMatrix(
            user_requirements=self.user_requirements,
            task_coverage=new_task_coverage,
            evidence_status=new_evidence,
            answer_status=new_answer,
            missing=new_missing,
            coverage_ratio=ratio,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON/API use."""
        return {
            "user_requirements": self.user_requirements,
            "task_coverage": self.task_coverage,
            "evidence_status": self.evidence_status,
            "answer_status": self.answer_status,
            "missing": self.missing,
            "coverage_ratio": round(self.coverage_ratio, 4),
        }


# ---------------------------------------------------------------------------
# Pre-constructed answer contracts for common evidence types
# ---------------------------------------------------------------------------

# Maps EvidenceRequirement → default AnswerContract
_DEFAULT_ANSWER_CONTRACTS: dict[EvidenceRequirement, AnswerContract] = {
    EvidenceRequirement.PROVISION: AnswerContract(
        required_fields=["provision", "section", "act", "citation"],
        type_hint="provision",
    ),
    EvidenceRequirement.DEFINITION: AnswerContract(
        required_fields=["term", "definition", "source_provision", "citation"],
        type_hint="definition",
    ),
    EvidenceRequirement.PENALTY: AnswerContract(
        required_fields=[
            "offence",
            "penalty",
            "maximum_or_fixed",
            "legal_provision",
            "citation",
        ],
        type_hint="penalty",
    ),
    EvidenceRequirement.EXCEPTION: AnswerContract(
        required_fields=[
            "exception_type",
            "condition",
            "modified_penalty",
            "legal_provision",
            "citation",
        ],
        type_hint="exception",
    ),
    EvidenceRequirement.JURISDICTION: AnswerContract(
        required_fields=["jurisdiction", "authority", "act", "citation"],
        type_hint="jurisdiction",
    ),
    EvidenceRequirement.SCOPE: AnswerContract(
        required_fields=["scope", "applies_to", "limitations", "citation"],
        type_hint="scope",
    ),
    EvidenceRequirement.CROSS_REFERENCE: AnswerContract(
        required_fields=["source_section", "target_section", "relationship", "citation"],
        type_hint="cross_reference",
    ),
    EvidenceRequirement.FACT_APPLICATION: AnswerContract(
        required_fields=["scenario", "conditions_met", "legal_conclusion", "citation"],
        type_hint="fact_application",
    ),
}


def get_answer_contract(requirement: EvidenceRequirement) -> AnswerContract:
    """Get the default answer contract for an evidence requirement."""
    return _DEFAULT_ANSWER_CONTRACTS.get(requirement, AnswerContract(required_fields=["detail", "citation"]))
