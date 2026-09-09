"""2.1 — Query Planning Layer (Intelligence Layer).

Produces structured Evidence Tasks from user queries. Runs before retrieval;
feeds the agent graph and retrieval pipeline.

Architecture: Query → Intent & Requirement Parse → Requirement Extraction →
Minimum Sufficient Task Decomposer → Evidence DAG

The decomposer follows the "minimum sufficient decomposition" principle:
what is the minimum number of evidence tasks required to construct a
defensible answer?
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from app.rag.evidence_task import (
    EvidenceRequirement,
    EvidenceTask,
    TaskDAG,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence


class Intent(StrEnum):
    """Query intent classification."""

    IDENTIFICATION = "identification"
    LOOKUP = "lookup"
    DEFINITION = "definition"
    PROHIBITION = "prohibition"
    DUTY = "duty"
    RIGHT = "right"
    POWER = "power"
    PENALTY = "penalty"
    EXCEPTION = "exception"
    PROCEDURE = "procedure"
    APPLICABILITY = "applicability"
    COMPARISON = "comparison"
    TEMPORAL = "temporal"
    JURISDICTION = "jurisdiction"
    CROSS_REFERENCE = "cross_reference"
    CASE_LAW = "case_law"
    MULTI_HOP = "multi_hop"
    FACT_PATTERN = "fact_pattern"
    COMPLIANCE_ASSESSMENT = "compliance_assessment"


class ComplexityLevel(StrEnum):
    """Complexity gate determines decomposition strategy."""

    SIMPLE = "simple"       # One task
    MULTI_PART = "multi_part"  # Parallel tasks
    MULTI_HOP = "multi_hop"     # DAG with dependencies


@dataclass
class Requirement:
    """A single evidence requirement extracted from the query."""

    requirement_id: str
    evidence_type: EvidenceRequirement
    subject: str
    conditions: list[str] = field(default_factory=list)
    negation: bool = False
    jurisdiction: str | None = None
    temporal_scope: str | None = None
    entities: list[str] = field(default_factory=list)


@dataclass
class DecompositionResult:
    """Output of the query decomposition process."""

    complexity: ComplexityLevel
    intent: Intent
    entities: dict[str, str]
    jurisdiction: str | None
    temporal_scope: str | None
    tasks: list[EvidenceTask]
    dag: TaskDAG
    coverage_matrix: dict[str, list[str]]  # user_requirement -> task_ids
    total_tasks: int
    evidence_requirements: list[EvidenceRequirement]


# ---------------------------------------------------------------------------
# Intent and entity extraction patterns
# ---------------------------------------------------------------------------

_JURISDICTION_PATTERNS = re.compile(
    r"\b(India|Indian|Bharat|FSSAI|Food Safety and Standards Authority)"
    r"|\b(Maharashtra|Gujarat|Karnataka|Kerala|Tamil Nadu|Delhi|West Bengal)"
    r"|\b(Central Government|State Government|Union Territory)",
    re.IGNORECASE,
)

_TEMPORAL_PATTERNS = re.compile(
    r"\b(20\d{2}|before|after|since|until|from \d{4}|in \d{4}|during \d{4})",
    re.IGNORECASE,
)

_NEGATION_PATTERNS = re.compile(
    r"\b(not|without|neither|nor|no\b|except|unless|barring|excluding)\b",
    re.IGNORECASE,
)


# Map evidence types to their typical question patterns
_EVIDENCE_TYPE_KEYWORDS: dict[EvidenceRequirement, list[str]] = {
    EvidenceRequirement.PROVISION: ["provision", "section", "act", "rule", "governs", "applies"],
    EvidenceRequirement.DEFINITION: ["define", "definition", "means", "refers to", "includes"],
    EvidenceRequirement.PENALTY: ["penalty", "fine", "punishment", "imprisonment", "maximum penalty"],
    EvidenceRequirement.EXCEPTION: ["exception", "unless", "except", "notwithstanding", "does not apply"],
    EvidenceRequirement.JURISDICTION: ["jurisdiction", "authority", "court", "which court"],
    EvidenceRequirement.SCOPE: ["scope", "applicability", "applies to", "range", "covers"],
    EvidenceRequirement.CROSS_REFERENCE: ["cross-reference", "read with", "referred to", "see also"],
    EvidenceRequirement.FACT_APPLICATION: ["can", "could", "be penalized", "does it apply", "whether"],
}


def _extract_intent(query: str) -> Intent:
    """Determine the primary intent from the query text."""
    q = query.lower()

    for intent_name, keywords in _EVIDENCE_TYPE_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return Intent(intent_name.value)

    # Section-specific detection
    if re.search(r"\bsection\s+\d", q):
        return Intent.LOOKUP

    if "penalty" in q or "punishment" in q:
        return Intent.PENALTY

    if "exception" in q or "unless" in q:
        return Intent.EXCEPTION

    return Intent.LOOKUP


def _extract_entities(query: str) -> dict[str, str]:
    """Extract entities and their types from the query."""
    entities: dict[str, str] = {}

    # Act detection
    act_match = re.search(
        r"(Food Safety and Standards Act|FSS Act|FSSAI Act|FSSA)",
        query,
        re.IGNORECASE,
    )
    if act_match:
        entities["instrument"] = act_match.group(1)

    # Section detection
    section_match = re.search(r"\bsection\s+(\d{1,4})", query, re.IGNORECASE)
    if section_match:
        entities["section"] = section_match.group(1)

    # Jurisdiction detection
    jur_match = _JURISDICTION_PATTERNS.search(query)
    if jur_match:
        entities["jurisdiction"] = jur_match.group(1)

    return entities


def _extract_jurisdiction(query: str) -> str | None:
    """Extract jurisdiction from query."""
    match = _JURISDICTION_PATTERNS.search(query)
    return match.group(1) if match else None


def _extract_temporal_scope(query: str) -> str | None:
    """Extract temporal scope from query."""
    match = _TEMPORAL_PATTERNS.search(query)
    return match.group(1) if match else None


def _has_negation(query: str) -> bool:
    """Check if query contains negation."""
    return bool(_NEGATION_PATTERNS.search(query))


# ---------------------------------------------------------------------------
# Complexity Assessment
# ---------------------------------------------------------------------------

def _assess_complexity(query: str) -> ComplexityLevel:
    """Determine query complexity to guide decomposition strategy.

    Simple: single fact lookup (one section, one act, simple question)
    Multi-part: multiple independent evidence types
    Multi-hop: chain of reasoning required
    """
    q = query.lower().strip()

    # Count conjunctions and section references
    conjunction_count = len(re.findall(r"\b(?:and|or|but|however|whereas|while)\b", q))
    section_refs = len(re.findall(r"\bsection\s+\d", q, re.IGNORECASE))

    # Check for multi-hop indicators
    multi_hop_indicators = [
        "if", "then", "when", "provided that", "subject to",
        "in case", "where", "whenever",
    ]
    multi_hop_hits = sum(1 for indicator in multi_hop_indicators if indicator in q)

    # Check for multiple evidence types
    evidence_type_hits = sum(
        1 for keywords in _EVIDENCE_TYPE_KEYWORDS.values()
        if any(kw in q for kw in keywords)
    )

    if conjunction_count >= 2 or section_refs >= 2 or multi_hop_hits >= 2:
        return ComplexityLevel.MULTI_HOP
    elif conjunction_count >= 1 or section_refs >= 1 or evidence_type_hits >= 2:
        return ComplexityLevel.MULTI_PART
    else:
        return ComplexityLevel.SIMPLE


# ---------------------------------------------------------------------------
# Requirement Extraction
# ---------------------------------------------------------------------------

def _extract_requirements(query: str) -> list[Requirement]:
    """Extract structured evidence requirements from the query.

    Maps user intent → evidence requirements. Each requirement becomes
    a potential task in the decomposition.
    """
    requirements: list[Requirement] = []
    req_id = 0

    q = query.lower()
    intent = _extract_intent(query)
    entities = _extract_entities(query)
    jurisdiction = _extract_jurisdiction(query)
    temporal_scope = _extract_temporal_scope(query)
    has_negation = _has_negation(query)

    # Map intent to evidence requirements
    intent_to_requirement: dict[Intent, EvidenceRequirement] = {
        Intent.PENALTY: EvidenceRequirement.PENALTY,
        Intent.EXCEPTION: EvidenceRequirement.EXCEPTION,
        Intent.DEFINITION: EvidenceRequirement.DEFINITION,
        Intent.CROSS_REFERENCE: EvidenceRequirement.CROSS_REFERENCE,
        Intent.JURISDICTION: EvidenceRequirement.JURISDICTION,
        Intent.SCOPE: EvidenceRequirement.SCOPE,
        Intent.APPLICABILITY: EvidenceRequirement.SCOPE,
        Intent.FACT_PATTERN: EvidenceRequirement.FACT_APPLICATION,
        Intent.COMPLIANCE_ASSESSMENT: EvidenceRequirement.FACT_APPLICATION,
    }

    req_id += 1
    evidence_type = intent_to_requirement.get(intent, EvidenceRequirement.PROVISION)
    subject = entities.get("instrument", query[:50])

    # Build condition list from entities and negation
    conditions = []
    if entities.get("section"):
        conditions.append(f"section {entities['section']}")
    if has_negation:
        conditions.append("negation")

    requirements.append(Requirement(
        requirement_id=f"r{req_id}",
        evidence_type=evidence_type,
        subject=subject,
        conditions=conditions,
        negation=has_negation,
        jurisdiction=jurisdiction,
        temporal_scope=temporal_scope,
        entities=list(entities.values()),
    ))

    # Detect additional requirements from keywords
    # Check for penalty mentions
    if "penalty" in q or "fine" in q or "punishment" in q:
        if evidence_type != EvidenceRequirement.PENALTY:
            req_id += 1
            requirements.append(Requirement(
                requirement_id=f"r{req_id}",
                evidence_type=EvidenceRequirement.PENALTY,
                subject=subject,
                conditions=conditions,
                negation=has_negation,
                jurisdiction=jurisdiction,
                temporal_scope=temporal_scope,
                entities=list(entities.values()),
            ))

    # Check for exception mentions
    if any(kw in q for kw in ["exception", "unless", "except", "notwithstanding"]):
        if not any(r.evidence_type == EvidenceRequirement.EXCEPTION for r in requirements):
            req_id += 1
            requirements.append(Requirement(
                requirement_id=f"r{req_id}",
                evidence_type=EvidenceRequirement.EXCEPTION,
                subject=subject,
                conditions=conditions,
                negation=has_negation,
                jurisdiction=jurisdiction,
                temporal_scope=temporal_scope,
                entities=list(entities.values()),
            ))

    # Check for cross-references
    if any(kw in q for kw in ["read with", "referred to", "cross-reference", "see also"]):
        req_id += 1
        requirements.append(Requirement(
            requirement_id=f"r{req_id}",
            evidence_type=EvidenceRequirement.CROSS_REFERENCE,
            subject=subject,
            conditions=conditions,
            negation=has_negation,
            jurisdiction=jurisdiction,
            temporal_scope=temporal_scope,
            entities=list(entities.values()),
        ))

    # Check for definitions
    if any(kw in q for kw in ["define", "definition", "means", "refers to", "includes"]):
        req_id += 1
        requirements.append(Requirement(
            requirement_id=f"r{req_id}",
            evidence_type=EvidenceRequirement.DEFINITION,
            subject=subject,
            conditions=conditions,
            negation=has_negation,
            jurisdiction=jurisdiction,
            temporal_scope=temporal_scope,
            entities=list(entities.values()),
        ))

    return requirements


# ---------------------------------------------------------------------------
# Task Construction (Stage 2)
# ---------------------------------------------------------------------------

def _construct_tasks(
    requirements: list[Requirement],
    complexity: ComplexityLevel,
    query: str,
) -> list[EvidenceTask]:
    """Build EvidenceTasks from extracted requirements.

    Uses the complexity level to determine:
    - SIMPLE: 1 task
    - MULTI_PART: parallel tasks
    - MULTI_HOP: DAG with dependencies
    """
    tasks: list[EvidenceTask] = []

    if complexity == ComplexityLevel.SIMPLE:
        # Single task covers all requirements
        req = requirements[0]
        task = _build_task(
            task_id="T1",
            objective=_objective_for_requirement(req),
            question=_question_for_requirement(req, query),
            requirement=req,
            dependency=[],
        )
        tasks.append(task)
        return tasks

    # Build tasks from requirements with dependencies
    for i, req in enumerate(requirements):
        task_id = f"T{i + 1}"
        objective = _objective_for_requirement(req)
        question = _question_for_requirement(req, query)

        # Dependencies: each task depends on the previous one
        # EXCEPT for the first task which has no dependency
        dependency = [f"T{j}" for j in range(1, i + 1)] if i > 0 else []

        task = _build_task(
            task_id=task_id,
            objective=objective,
            question=question,
            requirement=req,
            dependency=dependency,
        )
        tasks.append(task)

    # For multi-hop, ensure minimum sufficient decomposition
    tasks = _apply_minimum_sufficient(tasks, complexity)

    return tasks


def _build_task(
    task_id: str,
    objective: str,
    question: str,
    requirement: Requirement,
    dependency: list[str],
) -> EvidenceTask:
    """Build a single EvidenceTask from a Requirement."""
    task = EvidenceTask(
        task_id=task_id,
        objective=objective,
        question=question,
        evidence_requirement=requirement.evidence_type,
        entities=requirement.entities,
        jurisdiction=requirement.jurisdiction,
        temporal_scope=requirement.temporal_scope,
        dependency=dependency,
        answer_type=_answer_type_for_requirement(requirement.evidence_type),
        must_be_explicit=True,
    )

    # Add answer contract
    from app.rag.evidence_task import get_answer_contract
    contract = get_answer_contract(requirement.evidence_type)
    task = task.with_answer_contract(contract.required_fields)

    # Handle negation
    if requirement.negation:
        task = task.add_entity("negation_condition")

    return task


def _objective_for_requirement(req: Requirement) -> str:
    """Map an evidence requirement type to an objective."""
    type_to_objective: dict[EvidenceRequirement, str] = {
        EvidenceRequirement.PROVISION: "identify_applicable_provision",
        EvidenceRequirement.DEFINITION: "obtain_definition",
        EvidenceRequirement.PENALTY: "determine_penalty",
        EvidenceRequirement.EXCEPTION: "identify_exception",
        EvidenceRequirement.JURISDICTION: "determine_jurisdiction",
        EvidenceRequirement.SCOPE: "identify_scope",
        EvidenceRequirement.CROSS_REFERENCE: "verify_cross_reference",
        EvidenceRequirement.FACT_APPLICATION: "apply_rule_to_facts",
        EvidenceRequirement.CONDITION: "identify_condition",
        EvidenceRequirement.PROHIBITION: "identify_prohibition",
        EvidenceRequirement.DUTY: "identify_duty",
        EvidenceRequirement.RIGHT: "identify_right",
        EvidenceRequirement.AUTHORITY: "identify_authority",
        EvidenceRequirement.TIME_LIMIT: "identify_time_limit",
        EvidenceRequirement.THRESHOLD: "identify_threshold",
        EvidenceRequirement.STANDARD: "identify_standard",
        EvidenceRequirement.AMENDMENT: "check_amendment",
        EvidenceRequirement.REPEAL: "check_repeal",
        EvidenceRequirement.CASE_LAW: "find_case_law",
        EvidenceRequirement.INTERPRETATION: "find_interpretation",
    }
    return type_to_objective.get(req.evidence_type, "identify_evidence")


def _question_for_requirement(req: Requirement, query: str) -> str:
    """Generate a question for a requirement based on the query."""
    evidence_type = req.evidence_type
    subject = req.subject

    # Use evidence type to craft the question
    type_to_question: dict[EvidenceRequirement, str] = {
        EvidenceRequirement.PROVISION: f"Which provision governs {subject}?",
        EvidenceRequirement.DEFINITION: f"What is the definition of {subject}?",
        EvidenceRequirement.PENALTY: f"What penalty applies to {subject}?",
        EvidenceRequirement.EXCEPTION: f"Are there exceptions to {subject}?",
        EvidenceRequirement.JURISDICTION: f"What jurisdiction applies to {subject}?",
        EvidenceRequirement.SCOPE: f"What is the scope of {subject}?",
        EvidenceRequirement.CROSS_REFERENCE: f"Are there cross-references for {subject}?",
        EvidenceRequirement.FACT_APPLICATION: f"Does {subject} apply to the scenario?",
        EvidenceRequirement.CONDITION: f"What conditions apply to {subject}?",
        EvidenceRequirement.PROHIBITION: f"Are there prohibitions on {subject}?",
        EvidenceRequirement.DUTY: f"What duty applies to {subject}?",
        EvidenceRequirement.RIGHT: f"What right applies to {subject}?",
        EvidenceRequirement.AUTHORITY: f"Who has authority over {subject}?",
    }

    base_question = type_to_question.get(
        evidence_type,
        f"Determine evidence for {subject}",
    )

    # Add negation clause if applicable
    if req.negation:
        base_question += " (without negation conditions)"

    return base_question


def _answer_type_for_requirement(evidence_type: EvidenceRequirement) -> str:
    """Map evidence requirement to expected answer type."""
    type_to_answer: dict[EvidenceRequirement, str] = {
        EvidenceRequirement.PROVISION: "citation",
        EvidenceRequirement.DEFINITION: "text",
        EvidenceRequirement.PENALTY: "numeric_or_rule",
        EvidenceRequirement.EXCEPTION: "text",
        EvidenceRequirement.JURISDICTION: "citation",
        EvidenceRequirement.SCOPE: "text",
        EvidenceRequirement.CROSS_REFERENCE: "citation",
        EvidenceRequirement.FACT_APPLICATION: "boolean",
        EvidenceRequirement.CONDITION: "text",
        EvidenceRequirement.PROHIBITION: "text",
        EvidenceRequirement.DUTY: "text",
        EvidenceRequirement.RIGHT: "text",
        EvidenceRequirement.AUTHORITY: "citation",
    }
    return type_to_answer.get(evidence_type, "text")


def _apply_minimum_sufficient(
    tasks: list[EvidenceTask],
    complexity: ComplexityLevel,
) -> list[EvidenceTask]:
    """Apply minimum sufficient decomposition principle.

    Don't decompose more than necessary. For SIMPLE queries, keep 1 task.
    For MULTI_PART, keep parallel tasks. For MULTI_HOP, keep necessary DAG.
    """
    if complexity == ComplexityLevel.SIMPLE and len(tasks) > 1:
        # Collapse to single task
        merged = tasks[0]
        for task in tasks[1:]:
            merged = merged.with_dependency(task.task_id)
        return [merged]

    # Remove redundant tasks: if a task's evidence is subsumed by another
    # with the same evidence type, keep only the more specific one
    unique_evidence_types: dict[EvidenceRequirement, EvidenceTask] = {}
    for task in tasks:
        req = task.evidence_requirement
        if req not in unique_evidence_types:
            unique_evidence_types[req] = task
        else:
            # Keep the more specific task (more entities = more specific)
            existing = unique_evidence_types[req]
            if len(task.entities) > len(existing.entities):
                unique_evidence_types[req] = task

    return list(unique_evidence_types.values())


# ---------------------------------------------------------------------------
# Main Planner Class
# ---------------------------------------------------------------------------

class QueryPlanner:
    """Produces Evidence Tasks from user queries with DAG support.

    Architecture: Query → Intent & Requirement Parse → Requirement Extraction →
    Minimum Sufficient Task Decomposer → Evidence DAG

    The complexity gate prevents over-decomposition:
    - Simple queries → 1 task
    - Multi-part → parallel tasks
    - Multi-hop → DAG with dependencies
    """

    def __init__(self) -> None:
        pass

    def decompose(self, query: str) -> list[EvidenceTask]:
        """Decompose query into Evidence Tasks.

        Args:
            query: The user's legal query.

        Returns:
            List of Evidence Tasks with dependencies.
        """
        result = self.plan(query)
        return result.tasks

    def plan(self, query: str) -> DecompositionResult:
        """Full decomposition pipeline: intent → requirements → tasks → DAG.

        Args:
            query: The user's legal query.

        Returns:
            DecompositionResult containing tasks, DAG, and coverage info.
        """
        if not query or not query.strip():
            return DecompositionResult(
                complexity=ComplexityLevel.SIMPLE,
                intent=Intent.LOOKUP,
                entities={},
                jurisdiction=None,
                temporal_scope=None,
                tasks=[],
                dag=TaskDAG(),
                coverage_matrix={},
                total_tasks=0,
                evidence_requirements=[],
            )

        # Stage 1: Complexity Assessment
        complexity = _assess_complexity(query)

        # Stage 2: Intent & Entity Extraction
        intent = _extract_intent(query)
        entities = _extract_entities(query)
        jurisdiction = _extract_jurisdiction(query)
        temporal_scope = _extract_temporal_scope(query)

        # Stage 3: Requirement Extraction
        requirements = _extract_requirements(query)
        evidence_reqs = [r.evidence_type for r in requirements]

        # Stage 4: Task Construction
        tasks = _construct_tasks(requirements, complexity, query)

        # Stage 5: Build DAG
        dag = TaskDAG()
        for task in tasks:
            dag.add_task(task)

        # Validate DAG (no cycles)
        if dag.has_cycle():
            logger.warning("QueryPlanner: DAG has cycles — topological sort may be incorrect")
            # Fall back to flat ordering
            tasks = dag.topological_order()

        # Stage 6: Build coverage matrix
        coverage_matrix = self._build_coverage_matrix(tasks, query)

        return DecompositionResult(
            complexity=complexity,
            intent=intent,
            entities=entities,
            jurisdiction=jurisdiction,
            temporal_scope=temporal_scope,
            tasks=tasks,
            dag=dag,
            coverage_matrix=coverage_matrix,
            total_tasks=len(tasks),
            evidence_requirements=evidence_reqs,
        )

    def _build_coverage_matrix(
        self, tasks: list[EvidenceTask], query: str
    ) -> dict[str, list[str]] {
        """Build mapping from user requirements to task IDs."""
        matrix: dict[str, list[str]] = {}
        for task in tasks:
            key = f"{task.evidence_requirement.value}:{task.objective}"
            matrix[key] = [task.task_id]
        return matrix

    def get_complexity(self, query: str) -> ComplexityLevel {
        """Return the complexity assessment for a query."""
        return _assess_complexity(query)

    def get_retrieval_strategy(self, tasks: list[EvidenceTask]) -> dict[str, list[str]] {
        """Map evidence tasks to retrieval strategies.

        Returns a dict mapping task_id → list of retrieval routes.
        """
        strategy_map: dict[str, list[str]] = {}
        for task in tasks:
            routes = []
            retrieval = task.retrieval
            if retrieval.get("identifier"):
                routes.append("identifier")
            if retrieval.get("lexical"):
                routes.append("lexical")
            if retrieval.get("dense"):
                routes.append("dense")
            if retrieval.get("knowledge_graph"):
                routes.append("knowledge_graph")
            strategy_map[task.task_id] = routes
        return strategy_map


# ---------------------------------------------------------------------------
# Backward compatibility: keep existing plan_node interface working
# ---------------------------------------------------------------------------

def _legacy_plan(query: str, query_type: str = "general") -> dict[str, Any]:
    """Compatibility wrapper for existing plan_node integration."""
    planner = QueryPlanner()
    result = planner.plan(query)
    return {
        "intent": result.intent.value,
        "complexity": result.complexity.value,
        "tasks": [
            {
                "task_id": t.task_id,
                "objective": t.objective,
                "question": t.question,
                "evidence_requirement": t.evidence_requirement.value,
                "dependency": t.dependency,
                "answer_type": t.answer_type,
                "answer_contract": (
                    t.answer_contract.required_fields if t.answer_contract else []
                ),
            }
            for t in result.tasks
        ],
        "total_tasks": result.total_tasks,
        "dag_valid": not result.dag.has_cycle(),
    }