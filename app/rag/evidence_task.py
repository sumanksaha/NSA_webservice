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


class ClaimVerificationStatus(StrEnum):
    """Status of a single claim against retrieved evidence.

    Replaces one overall groundedness score with per-claim status so the
    answer can represent which parts are supported, which are partial, and
    which are contradicted or unsupported.
    """

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"


class ClaimVerification:
    """One claim verified against retrieved evidence.

    Attributes:
        claim_id: Stable claim identifier used in the answer contract.
        text: The claim sentence text.
        status: Verdict from :class:`ClaimVerificationStatus`.
        confidence: 0.0–1.0 confidence in the verdict.
        evidence: Chunk ids supporting or relating to the claim.
        authority_score: Best authority weight among supporting evidence.
        temporal_valid: Whether the supporting evidence is temporally consistent.
        contradictions: Contradictions raised against this claim, if any.
    """

    def __init__(
        self,
        *,
        claim_id: str,
        text: str,
        status: ClaimVerificationStatus,
        confidence: float = 0.0,
        evidence: list[str] | None = None,
        authority_score: float = 0.0,
        temporal_valid: bool = True,
        contradictions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.claim_id = claim_id
        self.text = text
        self.status = status
        self.confidence = confidence
        self.evidence = list(evidence or [])
        self.authority_score = authority_score
        self.temporal_valid = temporal_valid
        self.contradictions = list(contradictions or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "status": self.status.value,
            "confidence": round(self.confidence, 3),
            "evidence": list(self.evidence),
            "authority_score": round(self.authority_score, 3),
            "temporal_valid": bool(self.temporal_valid),
            "contradictions": list(self.contradictions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaimVerification:
        try:
            status = ClaimVerificationStatus(str(data.get("status", ClaimVerificationStatus.UNSUPPORTED.value)))
        except ValueError:
            status = ClaimVerificationStatus.UNSUPPORTED
        return cls(
            claim_id=str(data.get("claim_id", "")),
            text=str(data.get("text", "")),
            status=status,
            confidence=float(data.get("confidence", 0.0)),
            evidence=[str(e) for e in (data.get("evidence") or [])],
            authority_score=float(data.get("authority_score", 0.0)),
            temporal_valid=bool(data.get("temporal_valid", True)),
            contradictions=[dict(c) for c in (data.get("contradictions") or [])],
        )


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict (LangGraph state / checkpointing)."""
        return {
            "lexical_queries": list(self.lexical_queries),
            "semantic_queries": list(self.semantic_queries),
            "identifiers": list(self.identifiers),
            "metadata_filters": dict(self.metadata_filters),
            "required_source_types": list(self.required_source_types),
            "cross_reference_targets": list(self.cross_reference_targets),
            "temporal_constraints": dict(self.temporal_constraints),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RetrievalPlan:
        """Rebuild from :meth:`to_dict` output (lenient to missing keys)."""
        data = data or {}
        return cls(
            lexical_queries=[str(q) for q in (data.get("lexical_queries") or [])],
            semantic_queries=[str(q) for q in (data.get("semantic_queries") or [])],
            identifiers=[str(i) for i in (data.get("identifiers") or [])],
            metadata_filters=dict(data.get("metadata_filters") or {}),
            required_source_types=[str(s) for s in (data.get("required_source_types") or [])],
            cross_reference_targets=[str(t) for t in (data.get("cross_reference_targets") or [])],
            temporal_constraints=dict(data.get("temporal_constraints") or {}),
        )


@dataclass
class AnswerContract:
    """What constitutes a successful answer for this EvidenceTask.

    The answer generator uses this to automatically verify completeness:
    check that all required_fields are present in the generated answer.
    """

    required_fields: list[str]
    optional_fields: list[str] = field(default_factory=list)
    type_hint: str | None = None  # e.g. "penalty", "provision", "citation"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "type_hint": self.type_hint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AnswerContract:
        """Rebuild from :meth:`to_dict` output (lenient to missing keys)."""
        data = data or {}
        return cls(
            required_fields=[str(f) for f in (data.get("required_fields") or [])],
            optional_fields=[str(f) for f in (data.get("optional_fields") or [])],
            type_hint=data.get("type_hint"),
        )

    def is_satisfied(self, answer: dict[str, Any]) -> bool:
        """True when every required field is present and non-empty."""
        return all(answer.get(f) is not None and answer.get(f) != "" for f in self.required_fields)

    def missing_fields(self, answer: dict[str, Any]) -> list[str]:
        """Return the required fields missing from *answer*."""
        return [f for f in self.required_fields if answer.get(f) is None or answer.get(f) == ""]


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict (LangGraph state / checkpointing).

        The graph state carries tasks as plain dicts so they survive the
        JSON round-trip required by checkpointer-based resume (M5).
        """
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "question": self.question,
            "evidence_requirement": self.evidence_requirement.value,
            "entities": list(self.entities),
            "jurisdiction": self.jurisdiction,
            "temporal_scope": self.temporal_scope,
            "dependency": list(self.dependency),
            "answer_type": self.answer_type,
            "must_be_explicit": self.must_be_explicit,
            "answer_contract": self.answer_contract.to_dict() if self.answer_contract else None,
            "retrieval": self.retrieval.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceTask:
        """Rebuild from :meth:`to_dict` output.

        Lenient by design: missing optional keys take defaults, and an
        unknown ``evidence_requirement`` value falls back to PROVISION so a
        stale serialized plan cannot crash the graph.

        Raises:
            ValueError: when *data* is not a dict or carries no ``task_id``.
        """
        if not isinstance(data, dict) or not data.get("task_id"):
            raise ValueError("EvidenceTask dict requires a 'task_id'")
        try:
            requirement = EvidenceRequirement(str(data.get("evidence_requirement", "provision")))
        except ValueError:
            requirement = EvidenceRequirement.PROVISION
        contract_data = data.get("answer_contract")
        contract = AnswerContract.from_dict(contract_data) if isinstance(contract_data, dict) else None
        return cls(
            task_id=str(data["task_id"]),
            objective=str(data.get("objective", "")),
            question=str(data.get("question", "")),
            evidence_requirement=requirement,
            entities=[str(e) for e in (data.get("entities") or [])],
            jurisdiction=data.get("jurisdiction"),
            temporal_scope=data.get("temporal_scope"),
            dependency=[str(d) for d in (data.get("dependency") or [])],
            answer_type=str(data.get("answer_type", "citation")),
            must_be_explicit=bool(data.get("must_be_explicit", True)),
            answer_contract=contract,
            retrieval=RetrievalPlan.from_dict(data.get("retrieval") or {}),
        )

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
class AnswerRequirement:
    """One independently verifiable answer requirement from query decomposition.

    This is the *first-class* unit the reviewer's architecture points at:
    decompose the question into answer requirements, then derive retrieval
    questions / EvidenceTasks from those requirements.

    Attributes:
        id: Requirement identifier (e.g. "R1", "R2").
        type: Evidence requirement taxonomy entry.
        subject: What the requirement is about.
        question: The retrieval question derived from this requirement.
        answer_type: Expected answer shape for this requirement.
        evidence_required: Evidence types / source signals this requirement needs.
        mandatory: Whether failing this requirement should block/abstain.
        conditions: Extra conditions shaping retrieval/answer.
        jurisdiction: Optional jurisdiction constraint.
        temporal_scope: Optional time window.
    """

    id: str
    type: EvidenceRequirement
    subject: str
    question: str
    answer_type: str = "text"
    evidence_required: list[str] = field(default_factory=list)
    mandatory: bool = True
    conditions: list[str] = field(default_factory=list)
    jurisdiction: str | None = None
    temporal_scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "subject": self.subject,
            "question": self.question,
            "answer_type": self.answer_type,
            "evidence_required": list(self.evidence_required),
            "mandatory": bool(self.mandatory),
            "conditions": list(self.conditions),
            "jurisdiction": self.jurisdiction,
            "temporal_scope": self.temporal_scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnswerRequirement:
        try:
            req_type = EvidenceRequirement(str(data.get("type", "provision")))
        except ValueError:
            req_type = EvidenceRequirement.PROVISION
        return cls(
            id=str(data.get("id", "")),
            type=req_type,
            subject=str(data.get("subject", "")),
            question=str(data.get("question", "")),
            answer_type=str(data.get("answer_type", "text")),
            evidence_required=[str(e) for e in (data.get("evidence_required") or [])],
            mandatory=bool(data.get("mandatory", True)),
            conditions=[str(c) for c in (data.get("conditions") or [])],
            jurisdiction=data.get("jurisdiction"),
            temporal_scope=data.get("temporal_scope"),
        )


@dataclass
class AnswerRequirementGraph:
    """Structured answer-requirement graph for one user query.

    This is the proposed primary decomposition output: a set of requirements
    with explicit dependencies, plus a helper to derive the EvidenceTask DAG
    so existing executor/synthesis code keeps working.

    The graph distinguishes *mandatory* requirements (must be answered for a
    defensible answer) from optional ones (nice-to-have context).
    """

    query: str
    requirements: list[AnswerRequirement]
    dependencies: list[tuple[str, str]]  # (depends_on, requirement_id)
    derived_tasks: list[EvidenceTask] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "requirements": [r.to_dict() for r in self.requirements],
            "dependencies": [list(d) for d in self.dependencies],
            "derived_tasks": [t.to_dict() for t in self.derived_tasks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnswerRequirementGraph:
        reqs = [AnswerRequirement.from_dict(r) for r in (data.get("requirements") or [])]
        deps = [tuple(d) for d in (data.get("dependencies") or [])]
        tasks = [EvidenceTask.from_dict(t) for t in (data.get("derived_tasks") or [])]
        return cls(
            query=str(data.get("query", "")),
            requirements=reqs,
            dependencies=deps,
            derived_tasks=tasks,
        )

    def requirement_ids(self) -> list[str]:
        return [r.id for r in self.requirements]

    def mandatory_ids(self) -> list[str]:
        return [r.id for r in self.requirements if r.mandatory]

    def dependency_set(self) -> set[tuple[str, str]]:
        return set(self.dependencies)

    def requirement_by_id(self, req_id: str) -> AnswerRequirement | None:
        for r in self.requirements:
            if r.id == req_id:
                return r
        return None

    def add_derived_tasks(self, tasks: list[EvidenceTask]) -> AnswerRequirementGraph:
        return AnswerRequirementGraph(
            query=self.query,
            requirements=self.requirements,
            dependencies=self.dependencies,
            derived_tasks=list(tasks),
        )


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


def requirement_graph_from_tasks(
    query: str,
    tasks: list[EvidenceTask],
    *,
    make_mandatory: bool = True,
) -> AnswerRequirementGraph:
    """Build an AnswerRequirementGraph from existing EvidenceTasks.

    This is the backward-compatible bridge: existing decomposition paths can
    keep producing EvidenceTasks, and this converts them into the new
    requirement-first representation without rewriting the planner yet.

    Each task becomes one requirement whose question/task_id/answer_type are
    carried over, and each task dependency becomes a requirement dependency.
    """
    by_id = {t.task_id: t for t in tasks}
    requirements: list[AnswerRequirement] = []
    for task in tasks:
        requirements.append(
            AnswerRequirement(
                id=task.task_id,
                type=task.evidence_requirement,
                subject=task.question or task.objective,
                question=task.question or task.objective,
                answer_type=task.answer_type or requirement_to_answer_type(task.evidence_requirement),
                evidence_required=[task.evidence_requirement.value],
                mandatory=make_mandatory,
                conditions=list(task.retrieval.lexical_queries or []),
                jurisdiction=task.jurisdiction,
                temporal_scope=task.temporal_scope,
            )
        )

    dependencies: list[tuple[str, str]] = []
    for task in tasks:
        for dep_id in task.dependency or []:
            if dep_id in by_id:
                dependencies.append((dep_id, task.task_id))

    return AnswerRequirementGraph(
        query=query,
        requirements=requirements,
        dependencies=dependencies,
        derived_tasks=list(tasks),
    )


# ---------------------------------------------------------------------------
# Pre-constructed answer contracts for common evidence types
# ---------------------------------------------------------------------------

#: Maps EvidenceRequirement → default AnswerContract.
#: Canonical, complete table — ``app.rag.evidence_contract`` re-exports this
#: (the two tables previously drifted; keep ONE source of truth).
_ANSWER_REQUIREMENT_DOC_TYPE_HINTS: dict[EvidenceRequirement, list[str]] = {
    EvidenceRequirement.PROVISION: ["section", "provision", "act"],
    EvidenceRequirement.DEFINITION: ["means", "definition", "includes"],
    EvidenceRequirement.SCOPE: ["scope", "applies", "applicability"],
    EvidenceRequirement.PENALTY: ["penalty", "fine", "imprisonment"],
    EvidenceRequirement.EXCEPTION: ["exception", "unless", "notwithstanding"],
    EvidenceRequirement.AUTHORITY: ["authority", "power", "may"],
    EvidenceRequirement.JURISDICTION: ["jurisdiction", "court", "authority"],
    EvidenceRequirement.CROSS_REFERENCE: ["section", "read with", "referred to"],
    EvidenceRequirement.FACT_APPLICATION: ["shall", "may", "contravention"],
}


def requirement_evidence_hints(requirement: EvidenceRequirement) -> list[str]:
    """Evidence signals a requirement should look for in retrieved chunks.

    Lightweight counterpart to the answer contract: informs retrieval
    shaping and sufficiency reading without requiring a full contract.
    """
    return _ANSWER_REQUIREMENT_DOC_TYPE_HINTS.get(
        requirement,
        ["section", "provision"],
    )


DEFAULT_ANSWER_CONTRACTS: dict[EvidenceRequirement, AnswerContract] = {
    EvidenceRequirement.PROVISION: AnswerContract(
        required_fields=["provision", "section", "act", "citation"],
        type_hint="provision",
    ),
    EvidenceRequirement.DEFINITION: AnswerContract(
        required_fields=["term", "definition", "source_provision", "citation"],
        type_hint="definition",
    ),
    EvidenceRequirement.SCOPE: AnswerContract(
        required_fields=["scope", "applies_to", "limitations", "citation"],
        type_hint="scope",
    ),
    EvidenceRequirement.ELEMENT: AnswerContract(
        required_fields=["elements", "required_conditions", "citation"],
        type_hint="element",
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
    EvidenceRequirement.CONDITION: AnswerContract(
        required_fields=["condition", "trigger", "citation"],
        type_hint="condition",
    ),
    EvidenceRequirement.PROHIBITION: AnswerContract(
        required_fields=["prohibited_action", "scope", "citation"],
        type_hint="prohibition",
    ),
    EvidenceRequirement.DUTY: AnswerContract(
        required_fields=["duty", "obligated_party", "citation"],
        type_hint="duty",
    ),
    EvidenceRequirement.RIGHT: AnswerContract(
        required_fields=["right", "beneficiary", "citation"],
        type_hint="right",
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
    EvidenceRequirement.OFFENCE: AnswerContract(
        required_fields=["offence", "elements", "citation"],
        type_hint="offence",
    ),
    EvidenceRequirement.PROCEDURE: AnswerContract(
        required_fields=["procedure", "steps", "authority", "citation"],
        type_hint="procedure",
    ),
    EvidenceRequirement.AUTHORITY: AnswerContract(
        required_fields=["authority", "power", "legal_provision", "citation"],
        type_hint="authority",
    ),
    EvidenceRequirement.JURISDICTION: AnswerContract(
        required_fields=["jurisdiction", "authority", "act", "citation"],
        type_hint="jurisdiction",
    ),
    EvidenceRequirement.TIME_LIMIT: AnswerContract(
        required_fields=["time_limit", "period", "citation"],
        type_hint="time_limit",
    ),
    EvidenceRequirement.THRESHOLD: AnswerContract(
        required_fields=["threshold", "value", "citation"],
        type_hint="threshold",
    ),
    EvidenceRequirement.STANDARD: AnswerContract(
        required_fields=["standard", "criteria", "citation"],
        type_hint="standard",
    ),
    EvidenceRequirement.CROSS_REFERENCE: AnswerContract(
        required_fields=["source_section", "target_section", "relationship", "citation"],
        type_hint="cross_reference",
    ),
    EvidenceRequirement.AMENDMENT: AnswerContract(
        required_fields=["amendment", "effective_date", "citation"],
        type_hint="amendment",
    ),
    EvidenceRequirement.REPEAL: AnswerContract(
        required_fields=["repealed_provision", "repeal_date", "citation"],
        type_hint="repeal",
    ),
    EvidenceRequirement.CASE_LAW: AnswerContract(
        required_fields=["case_name", "holding", "court", "citation"],
        type_hint="case_law",
    ),
    EvidenceRequirement.INTERPRETATION: AnswerContract(
        required_fields=["interpretation", "authority", "citation"],
        type_hint="interpretation",
    ),
    EvidenceRequirement.FACT_APPLICATION: AnswerContract(
        required_fields=["scenario", "conditions_met", "legal_conclusion", "citation"],
        type_hint="fact_application",
    ),
}

#: Backward-compatible alias for the pre-Phase-1 private name.
_DEFAULT_ANSWER_CONTRACTS = DEFAULT_ANSWER_CONTRACTS


def get_answer_contract(requirement: EvidenceRequirement) -> AnswerContract:
    """Get the default answer contract for an evidence requirement."""
    return DEFAULT_ANSWER_CONTRACTS.get(requirement, AnswerContract(required_fields=["detail", "citation"]))


def build_claim_verification(
    claims: list[dict[str, Any]],
    verifications: list[dict[str, Any]],
    authority_values: list[float] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
    temporal_conflict: bool = False,
) -> list[ClaimVerification]:
    """Build per-claim :class:`ClaimVerification` records for the response.

    This is the first-class upgrade to the existing claim path: instead of
    returning only ``verified`` + ``confidence``, the agent can return a
    richer claim matrix with status, evidence, authority and contradiction
    signals.

    Status mapping (backward-compatible with the existing binary path):

    - existing ``verified=True`` + authority strong + no contradiction → SUPPORTED
    - existing ``verified=True`` but weak authority or partial evidence → PARTIALLY_SUPPORTED
    - existing ``verified=False`` and contradicted → CONTRADICTED
    - existing ``verified=False`` otherwise → UNSUPPORTED
    """
    contradictions = list(contradictions or [])
    contradiction_chunk_ids = {c.get("chunk_a") for c in contradictions} | {c.get("chunk_b") for c in contradictions}
    authority_values = list(authority_values or [])
    out: list[ClaimVerification] = []
    for idx, claim in enumerate(claims):
        v = verifications[idx] if idx < len(verifications) else {}
        verified = bool(v.get("verified", False))
        confidence = float(v.get("confidence", 0.0))
        evidence = list(v.get("supporting_chunks") or [])
        status = ClaimVerificationStatus.UNSUPPORTED
        if verified:
            if contradiction_chunk_ids & set(evidence):
                status = ClaimVerificationStatus.CONTRADICTED
            elif temporal_conflict and not _temporal_consistent(evidence):
                status = ClaimVerificationStatus.PARTIALLY_SUPPORTED
            elif authority_values:
                best_authority = max(authority_values)
                if best_authority >= 0.8 and confidence >= 0.7:
                    status = ClaimVerificationStatus.SUPPORTED
                else:
                    status = ClaimVerificationStatus.PARTIALLY_SUPPORTED
            elif confidence >= 0.7:
                status = ClaimVerificationStatus.SUPPORTED
            else:
                status = ClaimVerificationStatus.PARTIALLY_SUPPORTED
        else:
            if contradiction_chunk_ids & set(evidence):
                status = ClaimVerificationStatus.CONTRADICTED
        out.append(
            ClaimVerification(
                claim_id=str(claim.get("claim_id", f"C{idx + 1}")),
                text=str(claim.get("text", "")),
                status=status,
                confidence=confidence,
                evidence=evidence,
                authority_score=max(authority_values) if authority_values else 0.0,
                temporal_valid=not temporal_conflict,
                contradictions=[
                    dict(c) for c in contradictions if c.get("chunk_a") in evidence or c.get("chunk_b") in evidence
                ],
            )
        )
    return out


def _temporal_consistent(chunk_ids: list[str]) -> bool:
    """Stub temporal-consistency check for claim status.

    A real implementation would inspect each chunk's temporal metadata
    against the requirement's temporal scope. For now this preserves the
    existing behavior (no temporal reject unless the sufficiency layer has
    already flagged a temporal conflict on the task).
    """
    return True


def requirement_to_answer_type(requirement: EvidenceRequirement) -> str:
    """Default answer type for an evidence requirement.

    Keeps the mapping in one place so retrieval/rerank/sufficiency code can
    ask "what kind of answer should this requirement produce?" without
    re-deriving it from the task objective text.
    """
    mapping: dict[EvidenceRequirement, str] = {
        EvidenceRequirement.PROVISION: "citation",
        EvidenceRequirement.DEFINITION: "text",
        EvidenceRequirement.SCOPE: "text",
        EvidenceRequirement.ELEMENT: "text",
        EvidenceRequirement.EXCEPTION: "text",
        EvidenceRequirement.CONDITION: "text",
        EvidenceRequirement.PROHIBITION: "text",
        EvidenceRequirement.DUTY: "text",
        EvidenceRequirement.RIGHT: "text",
        EvidenceRequirement.PENALTY: "numeric_or_rule",
        EvidenceRequirement.OFFENCE: "text",
        EvidenceRequirement.PROCEDURE: "text",
        EvidenceRequirement.AUTHORITY: "citation",
        EvidenceRequirement.JURISDICTION: "citation",
        EvidenceRequirement.TIME_LIMIT: "numeric_or_rule",
        EvidenceRequirement.THRESHOLD: "numeric_or_rule",
        EvidenceRequirement.STANDARD: "text",
        EvidenceRequirement.CROSS_REFERENCE: "citation",
        EvidenceRequirement.AMENDMENT: "yes_no_or_rule",
        EvidenceRequirement.REPEAL: "yes_no_or_rule",
        EvidenceRequirement.CASE_LAW: "citation",
        EvidenceRequirement.INTERPRETATION: "text",
        EvidenceRequirement.FACT_APPLICATION: "boolean",
    }
    return mapping.get(requirement, "text")
