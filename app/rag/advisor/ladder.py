"""FSO statutory escalation ladder and action profiles.

Least-to-most-irreversible order per CONTEXT.md ("FSO escalation ladder").
Tuning lives here (single point): ``LAMBDA_FRAGILITY`` / ``OMEGA_OPTIONALITY``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

#: Fragility penalty weight in Opt(a) = R·I − λ(1−R).
LAMBDA_FRAGILITY = 0.5

#: Optionality bonus weight in U(a) = deterrence − cost + ω·Opt(a).
OMEGA_OPTIONALITY = 2.0


class EscalationLevel(IntEnum):
    """Ordered FSO statutory acts, least (1) to most (5) irreversible."""

    INSPECT_WARN = 1
    SAMPLE_LAB_TEST = 2
    IMPROVEMENT_NOTICE = 3
    PENALTY_DIRECTION = 4
    PROSECUTION = 5


@dataclass(frozen=True)
class ActionProfile:
    """One rung of the escalation ladder with game + Talebian parameters."""

    level: EscalationLevel
    action_name: str
    reversibility: float  # [0.0, 1.0]
    information_yield: float  # [0.0, 1.0]
    fso_cost: float  # Relative enforcement burden [1.0, 10.0]
    deterrence_power: float  # Impact score [1.0, 10.0]


ACTION_PROFILES: dict[EscalationLevel, ActionProfile] = {
    EscalationLevel.INSPECT_WARN: ActionProfile(EscalationLevel.INSPECT_WARN, "Inspect & Warn", 1.0, 0.2, 1.0, 1.0),
    EscalationLevel.SAMPLE_LAB_TEST: ActionProfile(
        EscalationLevel.SAMPLE_LAB_TEST, "Sample & Lab-Test", 0.9, 1.0, 3.0, 4.0
    ),
    EscalationLevel.IMPROVEMENT_NOTICE: ActionProfile(
        EscalationLevel.IMPROVEMENT_NOTICE, "Issue Improvement Notice u/s 32", 0.8, 0.7, 2.0, 6.0
    ),
    EscalationLevel.PENALTY_DIRECTION: ActionProfile(
        EscalationLevel.PENALTY_DIRECTION, "Show-Cause / Penalty Direction u/s 55", 0.5, 0.4, 4.0, 8.0
    ),
    EscalationLevel.PROSECUTION: ActionProfile(
        EscalationLevel.PROSECUTION, "Prosecution / Licence Action u/s 63/64", 0.0, 0.1, 10.0, 10.0
    ),
}
