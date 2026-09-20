"""Pure, deterministic Act-selector: statutory-floor constraint + maximin.

Decision rule (ADR-0006):

1. The **statutory floor** is a hard legal *admissibility constraint* —
   procedural prerequisites (cognizable/repeat offence, prior notice, lab
   evidence, zero-schedule anchors) bound which rungs of the ladder the FSO
   may legally stand on. The floor never selects by itself.
2. Among **admissible** acts (level ≥ floor), the Act maximising the robust
   score ``maximin_value + ω · optionality`` is selected, where the maximin
   value ``min_s U(a, s)`` comes from the computed zero-sum FSO×FBO game in
   :mod:`app.rag.advisor.game` — the FBO best response is *computed*, never
   assumed. Ties resolve to the least-escalatory admissible act.
3. Fail-closed: no grounded statutory anchor → ``fso_act=None`` with
   ``abstain_reason="insufficient_statutory_grounding"``. Never raises on
   caller-controlled input; never calls an LLM.

Under the shipped tables the floor act is provably the robust-score argmax
among admissible acts (the robust-value ordering and the optionality
ordering both align with the ladder below the floor). The invariant tests
``test_floor_act_maximizes_robust_score`` and
``test_floor_act_is_the_optionality_argmax`` fail loudly if payoff retuning
ever breaks that ordering — the blueprint's §6 spec Acts must keep holding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag.advisor.explainer import game_theory_basis, talebian_basis
from app.rag.advisor.game import FboStrategy, fbo_best_response
from app.rag.advisor.ladder import (
    ACTION_PROFILES,
    LAMBDA_FRAGILITY,
    OMEGA_OPTIONALITY,
    ActionProfile,
    EscalationLevel,
)
from app.rag.advisor.penalties import FSSAI_PENALTY_SCHEDULE, StatutoryAnchor

ABSTAIN_REASON = "insufficient_statutory_grounding"

#: Decimals kept when comparing robust scores — neutralises float noise so
#: ties are decided by the least-escalatory tie-break, not by 1-ulp dust.
_SCORE_PRECISION = 9

#: Decimals kept for the maximin value before scoring (payoff data is
#: 1-decimal; this also makes same-value acts exactly tied).
_VALUE_PRECISION = 6


@dataclass(frozen=True)
class _Candidate:
    """One admissible act with its computed game-theoretic scores."""

    profile: ActionProfile
    fbo_strategy: FboStrategy
    maximin_value: float
    optionality: float
    robust_score: float


def _statutory_floor(
    primary_anchor: StatutoryAnchor,
    has_prior_violations: bool,
    lab_report_available: bool,
) -> tuple[EscalationLevel, str]:
    """Least ladder rung the law lets the FSO stand on for this anchor.

    Procedural prerequisites only — the floor *constrains* admissibility
    and never selects the Act by itself (ADR-0006). Returns the floor level
    plus a machine-readable reason for the audit payload.
    """
    if primary_anchor.section in ("63", "64") or has_prior_violations:
        return EscalationLevel.PROSECUTION, "cognizable_offence_or_repeat_violation"
    if primary_anchor.max_fine_inr == 0 and primary_anchor.imprisonment_months == 0:
        # Zero-schedule anchor (today: §32, the notice procedure itself):
        # the only grounded move is the notice — never a penalty act off
        # a section that authorizes no penalty.
        return EscalationLevel.IMPROVEMENT_NOTICE, "zero_schedule_anchor"
    if primary_anchor.requires_prior_notice and not lab_report_available:
        return EscalationLevel.IMPROVEMENT_NOTICE, "prior_notice_prerequisite"
    if not lab_report_available and primary_anchor.section in ("51", "52"):
        return EscalationLevel.SAMPLE_LAB_TEST, "lab_evidence_required"
    return EscalationLevel.PENALTY_DIRECTION, "statutory_penalty_track"


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
            ``{"fso_act": dict | None, "abstain_reason": str | None}``. The
            ``fso_act`` payload carries the computed game facts (FBO best
            response, maximin value, admissible-act table, binding
            constraint) alongside the legacy fields.
        """
        anchors = [
            FSSAI_PENALTY_SCHEDULE[sec]
            for sec in (_normalize_sections(retrieved_sections))
            if sec in FSSAI_PENALTY_SCHEDULE
        ]
        if not anchors:
            return {"fso_act": None, "abstain_reason": ABSTAIN_REASON}

        primary_anchor: StatutoryAnchor = max(anchors, key=lambda a: (a.imprisonment_months, a.max_fine_inr))

        # Hard legal admissibility constraint (never a selection preference).
        floor, floor_reason = _statutory_floor(primary_anchor, has_prior_violations, lab_report_available)

        # Admissible acts in least-to-most-escalatory order, each scored by
        # its computed maximin value plus the Talebian convexity bonus.
        candidates: list[_Candidate] = []
        for level in sorted(ACTION_PROFILES):
            if level < floor:
                continue
            profile = ACTION_PROFILES[level]
            strategy, raw_maximin = fbo_best_response(profile)
            maximin = round(raw_maximin, _VALUE_PRECISION)
            optionality = self.optionality_score(profile)
            candidates.append(
                _Candidate(
                    profile=profile,
                    fbo_strategy=strategy,
                    maximin_value=maximin,
                    optionality=optionality,
                    robust_score=round(maximin + self.omega_optionality * optionality, _SCORE_PRECISION),
                )
            )

        # Ascending-ladder iteration + strict '>' ⇒ the least-escalatory
        # act wins exact ties (Talebian tie-break).
        best = candidates[0]
        for candidate in candidates[1:]:
            if candidate.robust_score > best.robust_score:
                best = candidate

        runner_up = max((c.robust_score for c in candidates if c is not best), default=None)
        margin = round(best.robust_score - runner_up, 3) if runner_up is not None else None

        return {
            "fso_act": {
                "action": best.profile.action_name,
                "escalation_level": best.profile.level.name,
                "statutory_anchor": f"Section {primary_anchor.section} ({primary_anchor.title})",
                "game_theory_basis": game_theory_basis(
                    primary_anchor,
                    maximin_value=best.maximin_value,
                    fbo_best_response=best.fbo_strategy.name,
                    floor=floor,
                    floor_reason=floor_reason,
                    margin=margin,
                ),
                "talebian_basis": talebian_basis(best.profile, best.optionality),
                "confidence": self.confidence_score(len(anchors), lab_report_available),
                "citations": [f"Section {primary_anchor.section}"],
                # --- computed game payload (ADR-0006, additive) -----------
                "fbo_best_response": best.fbo_strategy.name,
                "maximin_value": round(best.maximin_value, 3),
                "binding_constraint": {"type": "statutory_floor", "level": floor.name, "reason": floor_reason},
                "admissible_acts": [
                    {
                        "action": c.profile.action_name,
                        "escalation_level": c.profile.level.name,
                        "fbo_best_response": c.fbo_strategy.name,
                        "maximin_value": round(c.maximin_value, 3),
                        "optionality_score": round(c.optionality, 3),
                        "robust_score": c.robust_score,
                        "selected": c is best,
                    }
                    for c in candidates
                ],
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
