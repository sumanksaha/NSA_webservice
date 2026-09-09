"""Answer contracts for Evidence Tasks.

Defines what constitutes a successful answer for each evidence requirement
type. Each task tells the answer generator what fields must be present.

Example:
    PENALTY → offence, penalty, maximum_or_fixed, legal_provision, citation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.rag.evidence_task import EvidenceRequirement


@dataclass
class AnswerContract:
    """Contract specifying required and optional answer fields."""

    required_fields: list[str]
    optional_fields: list[str] = field(default_factory=list)
    type_hint: str | None = None

    def is_satisfied(self, answer: dict[str, Any]) -> bool:
        """Check if the answer satisfies the contract.

        Args:
            answer: Dict of answer fields (may be None values).

        Returns:
            True if all required fields are present and non-empty.
        """
        for field_name in self.required_fields:
            value = answer.get(field_name)
            if value is None or value == "":
                return False
        return True

    def missing_fields(self, answer: dict[str, Any]) -> list[str]:
        """Return list of required fields missing from the answer."""
        return [
            field_name
            for field_name in self.required_fields
            if answer.get(field_name) is None or answer.get(field_name) == ""
        ]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "required_fields": self.required_fields,
            "optional_fields": self.optional_fields,
            "type_hint": self.type_hint,
        }


# Default answer contracts per evidence requirement
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


def get_answer_contract(requirement: EvidenceRequirement) -> AnswerContract:
    """Get the default answer contract for an evidence requirement."""
    return DEFAULT_ANSWER_CONTRACTS.get(
        requirement,
        AnswerContract(required_fields=["detail", "citation"], type_hint="general"),
    )


def answer_contract_for_task(requirement: EvidenceRequirement) -> AnswerContract:
    """Alias for get_answer_contract."""
    return get_answer_contract(requirement)


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