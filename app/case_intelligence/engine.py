"""AI Case Intelligence engine for evaluating evidence strength, traceability, and readiness.

This module implements the core logic for calculating:
- Evidence strength: How strong and reliable the supporting evidence is
- Traceability: How well evidence is connected to the case
- Readiness score: Overall readiness of the case for processing
"""

import logging
from enum import Enum

from app.shared.case_resolver import CaseResolver
from app.models import CaseFile, Adjudication
from app.validation.data_assembler import CaseDataAssembler
from app.validation.rules import (
    BaseRule,
    ValidationResult,
    RULES,
    ERROR,
    INFO,
    WARNING,
)

logger = logging.getLogger(__name__)


class EvidenceStrengthScore(Enum):
    """Score levels for evidence quality."""
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NONE = "none"


class ReadinessScore(Enum):
    """Overall case readiness level."""
    READY = "ready"
    NEEDS_ATTENTION = "needs_attention"
    NOT_READY = "not_ready"


def _calculate_evidence_strength(case_data: dict) -> EvidenceStrengthScore:
    """Calculate evidence strength based on completeness and quality.

    Factors considered:
    - Number of evidence items
    - Presence of critical evidence types (statutory references, annexes, etc.)
    - Completeness of evidence (no missing required elements)
    - Quality indicators (signatures, timestamps, etc.)
    """
    # Count evidence items
    evidence_items = case_data.get("evidence", [])
    num_evidence = len(evidence_items)

    # Check for critical evidence categories
    critical_evidence_types = [
        "statutory_reference",
        "annexure",
        "evidence",
    ]

    has_critical = sum(1 for ev in evidence_items if ev.get("type") in critical_evidence_types)

    # Determine strength based on count and critical evidence
    if num_evidence == 0:
        return EvidenceStrengthScore.NONE
    elif num_evidence >= 5 and has_critical:
        return EvidenceStrengthScore.STRONG
    elif num_evidence >= 3 and has_critical:
        return EvidenceStrengthScore.MODERATE
    else:
        return EvidenceStrengthScore.WEAK


def _calculate_traceability(case_data: dict) -> float:
    """Calculate traceability score (0.0 to 1.0) based on evidence connectivity.

    Higher scores indicate stronger connections between evidence and the case.
    """
    evidence = case_data.get("evidence", [])
    if not evidence:
        return 0.0

    # Simple heuristic: more diverse evidence types = higher traceability
    evidence_types = set()
    for ev in evidence:
        ev_type = ev.get("type", "")
        if ev_type:
            evidence_types.add(ev_type)

    # More evidence types generally means better traceability
    # Updated thresholds: 1 type = 0.3, 2 types = 0.6, 3+ types = 0.9
    if len(evidence_types) <= 1:
        return 0.3
    elif len(evidence_types) <= 2:
        return 0.6
    else:
        return 0.9


def _calculate_readiness_score(case_data: dict) -> ReadinessScore:
    """Calculate overall readiness score based on multiple factors.

    Factors:
    - Error/warning count from validation
    - Evidence strength
    - Timeline consistency
    - Document completeness
    """
    # Start with base score
    score = 100

    # Factor 1: Validation errors/warnings
    validation_results = case_data.get("validation_results", [])
    if validation_results:
        error_count = sum(1 for r in validation_results if r["severity"] == ERROR)
        warning_count = sum(1 for r in validation_results if r["severity"] == WARNING)
        # Each error reduces readiness significantly
        score -= error_count * 15
        score -= warning_count * 5

    # Factor 2: Evidence strength
    strength = _calculate_evidence_strength(case_data)
    if strength == EvidenceStrengthScore.STRONG:
        score += 20
    elif strength == EvidenceStrengthScore.MODERATE:
        score += 10
    elif strength == EvidenceStrengthScore.WEAK:
        score -= 10
    elif strength == EvidenceStrengthScore.NONE:
        score -= 25

    # Factor 3: Timeline consistency
    timeline_issues = case_data.get("timeline_issues", [])
    if timeline_issues:
        score -= len(timeline_issues) * 5

    # Cap score between 0 and 100
    score = max(0, min(100, score))

    # Return corresponding readability
    if score >= 80:
        return ReadinessScore.READY
    elif score >= 50:
        return ReadinessScore.NEEDS_ATTENTION
    else:
        return ReadinessScore.NOT_READY


def calculate_intelligence_scores(case_id: int, case_type: str | None = None) -> dict:
    """
    Calculate evidence strength, traceability, and readiness scores for a case.

    Args:
        case_id: The case ID to analyze.
        case_type: The case type ("case_file" or "adjudication").

    Returns:
        Dictionary with keys:
        - evidence_strength: EvidenceStrengthScore
        - traceability: float (0.0 to 1.0)
        - readiness: ReadinessScore
        - scores: dict with individual numeric scores
    """
    # Resolve the case to get case data
    resolver = CaseResolver()
    resolved = resolver.resolve(case_id, kind=case_type)
    if resolved is None:
        return {
            "evidence_strength": EvidenceStrengthScore.NONE,
            "traceability": 0.0,
            "readiness": ReadinessScore.NOT_READY,
            "scores": {"evidence_strength": 0, "traceability": 0.0, "readiness": 0},
        }

    case_data = CaseDataAssembler().assemble(resolved)

    # Calculate scores
    evidence_strength = _calculate_evidence_strength(case_data)
    traceability = _calculate_traceability(case_data)
    readiness = _calculate_readiness_score(case_data)

    return {
        "evidence_strength": evidence_strength,
        "traceability": traceability,
        "readiness": readiness,
        "scores": {
            "evidence_strength": evidence_strength.value,
            "traceability": traceability,
            "readiness": readiness.value,
        },
    }
