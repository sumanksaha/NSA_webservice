"""Answer contracts for Evidence Tasks.

Defines what constitutes a successful answer for each evidence requirement
type. Each task tells the answer generator what fields must be present.

Example:
    PENALTY → offence, penalty, maximum_or_fixed, legal_provision, citation

Phase 1 (2026-09-09): the contract table and the ``AnswerContract`` class
previously existed **twice** — here and in
:mod:`app.rag.evidence_task` — and had already drifted (8 vs 23 covered
requirement types). This module is now a thin re-export of the canonical
definitions in :mod:`app.rag.evidence_task`; import from either location,
but the single source of truth lives there.
"""

from __future__ import annotations

from typing import Any

from app.rag.evidence_task import (
    DEFAULT_ANSWER_CONTRACTS,
    AnswerContract,
    EvidenceRequirement,
    get_answer_contract,
)

# Backward-compatible aliases (pre-Phase-1 public surface).
DEFAULT_ANSWER_CONTRACTS = DEFAULT_ANSWER_CONTRACTS
answer_contract_for_task = get_answer_contract


def verify_answer_contract(
    contract: AnswerContract,
    answer: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Verify whether an answer satisfies a contract.

    Returns:
        Tuple of (is_satisfied, missing_fields).
    """
    missing = contract.missing_fields(answer)
    return not missing, missing


def answer_sufficiency(
    answer: dict[str, Any],
    contract: AnswerContract,
) -> dict[str, Any]:
    """Return structured sufficiency result."""
    missing = contract.missing_fields(answer)
    return {
        "satisfied": not missing,
        "missing_fields": missing,
        "required_fields": contract.required_fields,
        "type_hint": contract.type_hint,
    }


__all__ = [
    "DEFAULT_ANSWER_CONTRACTS",
    "AnswerContract",
    "EvidenceRequirement",
    "answer_contract_for_task",
    "answer_sufficiency",
    "get_answer_contract",
    "verify_answer_contract",
]
