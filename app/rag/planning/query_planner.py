"""2.1 — Query Planning Layer (Intelligence Layer).

Produces structured EvidenceRequirements + subquestion graphs for compound
legal queries. Runs before retrieval; feeds the agent graph.

ponytail: minimal version — deterministic decomposition + typed evidence
requirements. Upgrade path: LLM-based intent extraction if accuracy falls short.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class EvidenceType(StrEnum):
    PROVISION = "provision"
    PENALTY_PROVISION = "penalty_provision"
    AUTHORITY_PROVISION = "authority_provision"
    DEFINITION = "definition"
    EXCEPTION = "exception"
    CROSS_REFERENCE = "cross_reference"
    CASE_LAW = "case_law"
    SCOPE_DEFINITION = "scope_definition"


class Intent(StrEnum):
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


@dataclass
class EvidenceRequirement:
    requirement_id: str
    question_part: str
    evidence_type: EvidenceType
    depends_on: list[str] = field(default_factory=list)


@dataclass
class SubQuestion:
    id: str
    question: str
    evidence_types: list[EvidenceType]
    depends_on: list[str] = field(default_factory=list)


@dataclass
class QueryPlan:
    intent: Intent
    entities: dict[str, str]
    subquestions: list[SubQuestion]
    evidence_requirements: list[EvidenceRequirement]
    retrieval_plan: list[str] = field(default_factory=list)  # ordered retrieval steps


class QueryPlanner:
    """Minimal deterministic planner — decomposes compound queries."""

    def __init__(self) -> None:
        pass

    def decompose(self, query: str) -> list[str]:
        # Delegate to SubQueryDecomposer for consistency
        from app.rag.retrieval.subquery_decomposer import SubQueryDecomposer

        return SubQueryDecomposer().decompose(query)

    def plan(self, query: str, query_type: str = "general") -> QueryPlan:
        sub_queries = self.decompose(query)
        subquestions = [
            SubQuestion(id=f"q{i + 1}", question=sq, evidence_types=[EvidenceType.PROVISION], depends_on=[])
            for i, sq in enumerate(sub_queries)
        ]
        # Build simple evidence requirements from subquestions
        evidence_reqs = [
            EvidenceRequirement(
                requirement_id=f"e{i + 1}",
                question_part=sq.question,
                evidence_type=EvidenceType.PROVISION,
            )
            for i, sq in enumerate(subquestions)
        ]
        return QueryPlan(
            intent=Intent(query_type) if query_type in [m.value for m in Intent] else Intent.LOOKUP,
            entities={"instrument": "FSS Act, 2006"},  # placeholder
            subquestions=subquestions,
            evidence_requirements=evidence_reqs,
            retrieval_plan=[f"retrieve_{sq.id}" for sq in subquestions],
        )
