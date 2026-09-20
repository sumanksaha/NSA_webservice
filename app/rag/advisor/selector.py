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

    @staticmethod
    def confidence_score(n_anchors: int, lab_report_available: bool) -> float:
        """Calibrated confidence from converging evidence.

        Base 0.7 (single grounded anchor — the rule is deterministic but the
        grounding is thin) + 0.1 per additional distinct anchor (cap +0.2) +
        0.1 with a statutory lab report on hand. Prior violations are
        excluded by design: they speak to severity, not to this violation's
        evidence. Capped at 1.0, rounded to two decimals.
        """
        score = 0.7 + 0.1 * min(max(n_anchors - 1, 0), 2)
        if lab_report_available:
            score += 0.1
        return round(min(score, 1.0), 2)

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
        elif primary_anchor.max_fine_inr == 0 and primary_anchor.imprisonment_months == 0:
            # Zero-schedule anchor (today: §32, the notice procedure itself):
            # the only grounded move is the notice — never a penalty act off
            # a section that authorizes no penalty.
            min_level = EscalationLevel.IMPROVEMENT_NOTICE
        elif primary_anchor.requires_prior_notice and not lab_report_available:
            min_level = EscalationLevel.IMPROVEMENT_NOTICE
        elif not lab_report_available and primary_anchor.section in ("51", "52"):
            min_level = EscalationLevel.SAMPLE_LAB_TEST
        else:
            min_level = EscalationLevel.PENALTY_DIRECTION

        # Minimax floor (legal procedure over the evidence): the floor act is
        # provably the optionality argmax among admissible acts — pinned by
        # test_floor_act_is_the_optionality_argmax, which fails loudly if
        # payoff retuning ever breaks the ordering. The blueprint's U + ω·Opt
        # argmax is NOT used: with the tuned payoffs it would pick
        # Improvement Notice over Sample & Lab-Test for §51, contradicting
        # the §6 spec (max-convexity first). Net utility is still reported
        # in the game-theoretic basis for audit.
        best_act: ActionProfile = ACTION_PROFILES[min_level]
        best_opt_score = self.optionality_score(best_act)

        return {
            "fso_act": {
                "action": best_act.action_name,
                "escalation_level": best_act.level.name,
                "statutory_anchor": f"Section {primary_anchor.section} ({primary_anchor.title})",
                "game_theory_basis": game_theory_basis(best_act, primary_anchor),
                "talebian_basis": talebian_basis(best_act, best_opt_score),
                "confidence": self.confidence_score(len(anchors), lab_report_available),
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
