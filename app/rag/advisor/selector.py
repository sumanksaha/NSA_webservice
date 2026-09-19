"""Pure, deterministic Act-selector implementing minimax + Talebian optionality.

Fail-closed: no grounded statutory anchor → ``fso_act=None`` with
``abstain_reason="insufficient_statutory_grounding"``. Never raises on
caller-controlled input; never calls an LLM.
"""

from __future__ import annotations

from typing import Any

from app.rag.advisor.explainer import game_theory_basis, talebian_basis
from app.rag.advisor.ladder import (
    ACTION_PROFILES,
    LAMBDA_FRAGILITY,
    OMEGA_OPTIONALITY,
    ActionProfile,
    EscalationLevel,
)
from app.rag.advisor.penalties import FSSAI_PENALTY_SCHEDULE, StatutoryAnchor

ABSTAIN_REASON = "insufficient_statutory_grounding"


class DeterministicActSelector:
    """Computes the optimal FSO Act purely over grounded evidence."""

    def __init__(
        self,
        lambda_fragility: float = LAMBDA_FRAGILITY,
        omega_optionality: float = OMEGA_OPTIONALITY,
    ) -> None:
        self.lambda_fragility = lambda_fragility
        self.omega_optionality = omega_optionality

    def optionality_score(self, profile: ActionProfile) -> float:
        """Talebian convexity: R·I − λ(1−R)."""
        return (profile.reversibility * profile.information_yield) - (
            self.lambda_fragility * (1.0 - profile.reversibility)
        )

    def select_act(
        self,
        retrieved_sections: list[str] | tuple[str, ...] | None,
        has_prior_violations: bool = False,
        lab_report_available: bool = False,
    ) -> dict[str, Any]:
        """Select the optimal Act for the grounded sections.

        Args:
            retrieved_sections: § numbers from grounded evidence (any
                surrounding "§"/"Section" decoration is stripped; unknown
                sections are ignored).
            has_prior_violations: previous convictions known (repeat offender).
            lab_report_available: statutory lab evidence on hand.

        Returns:
            ``{"fso_act": dict | None, "abstain_reason": str | None}``.
        """
        anchors = [
            FSSAI_PENALTY_SCHEDULE[sec]
            for sec in (_normalize_sections(retrieved_sections))
            if sec in FSSAI_PENALTY_SCHEDULE
        ]
        if not anchors:
            return {"fso_act": None, "abstain_reason": ABSTAIN_REASON}

        primary_anchor: StatutoryAnchor = max(anchors, key=lambda a: (a.imprisonment_months, a.max_fine_inr))

        if primary_anchor.section in ("63", "64") or has_prior_violations:
            min_level = EscalationLevel.PROSECUTION
        elif primary_anchor.requires_prior_notice and not lab_report_available:
            min_level = EscalationLevel.IMPROVEMENT_NOTICE
        elif not lab_report_available and primary_anchor.section in ("51", "52"):
            min_level = EscalationLevel.SAMPLE_LAB_TEST
        else:
            min_level = EscalationLevel.PENALTY_DIRECTION

        # Talebian filter (ADR-0003 + blueprint §2.D): among admissible Acts
        # (level >= min_level, the minimax floor), pick the highest
        # optionality score; escalate to irreversible Acts only when the
        # grounded penalty schedule demands it (via min_level). Note the
        # blueprint's U + ω·Opt argmax is NOT used: with the tuned payoffs
        # it would pick Improvement Notice over Sample & Lab-Test for §51,
        # contradicting the §6 spec (max-convexity first). Net utility is
        # still reported in the game-theoretic basis for audit.
        best_act: ActionProfile | None = None
        best_opt_score = float("-inf")
        for level, profile in ACTION_PROFILES.items():
            if level < min_level:
                continue
            opt_score = self.optionality_score(profile)
            if opt_score > best_opt_score:
                best_opt_score = opt_score
                best_act = profile

        if best_act is None:  # defensive: admissible set unexpectedly empty
            return {"fso_act": None, "abstain_reason": ABSTAIN_REASON}

        return {
            "fso_act": {
                "action": best_act.action_name,
                "escalation_level": best_act.level.name,
                "statutory_anchor": f"Section {primary_anchor.section} ({primary_anchor.title})",
                "game_theory_basis": game_theory_basis(best_act, primary_anchor),
                "talebian_basis": talebian_basis(best_act, best_opt_score),
                "confidence": 1.0,
                "citations": [f"Section {primary_anchor.section}"],
            },
            "abstain_reason": None,
        }


def compute_fso_advisory(
    retrieved_sections: list[str] | tuple[str, ...] | None,
    has_prior_violations: bool = False,
    lab_report_available: bool = False,
) -> dict[str, Any]:
    """Convenience wrapper with default-tuned selector (module seam)."""
    return DeterministicActSelector().select_act(
        retrieved_sections=retrieved_sections,
        has_prior_violations=has_prior_violations,
        lab_report_available=lab_report_available,
    )


def _normalize_sections(
    retrieved_sections: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Strip "§"/"Section" decoration → bare digits ("§ 51" → "51")."""
    import re

    out: list[str] = []
    for raw in retrieved_sections or []:
        text = str(raw or "").strip()
        if not text:
            continue
        m = re.search(r"\d{1,4}[A-Za-z]?", text)
        if m:
            out.append(re.match(r"\d{1,4}", m.group(0)).group(0))  # type: ignore[union-attr]
    # Dedupe, preserve order.
    seen: set[str] = set()
    deduped: list[str] = []
    for sec in out:
        if sec not in seen:
            seen.add(sec)
            deduped.append(sec)
    return deduped
