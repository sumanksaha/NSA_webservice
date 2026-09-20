"""Pure, deterministic Act-selector: statutory-floor constraint + subgame values.

Decision rule (ADR-0006 + ADR-0007):

1. The **statutory floor** is a hard legal *admissibility constraint* —
   procedural prerequisites (cognizable/repeat offence, prior notice, lab
   evidence, zero-schedule anchors) bound which rungs of the ladder the FSO
   may legally stand on. The floor never selects by itself.
2. Among **admissible** acts (level ≥ floor), the Act maximising the robust
   score ``V(ℓ) + ω · optionality`` is selected, where ``V(ℓ)`` is the
   escalation-subgame value from :mod:`app.rag.advisor.game` — solved by
   backward induction over the FSO act → FBO response → escalate-or-close
   tree, so the FBO best response *anticipates* escalation (ADR-0007), and
   the repeated-game discount δ encodes how far the escalation threat stays
   credible for repeat offenders. Ties resolve to the least-escalatory
   admissible act.
3. Fail-closed: no grounded statutory anchor → ``fso_act=None`` with
   ``abstain_reason="insufficient_statutory_grounding"``. Never raises on
   caller-controlled input; never calls an LLM.

Under the shipped tables the floor act is provably the robust-score argmax
among admissible acts for every reachable floor and both discount values
(δ ∈ {1.0, 0.3}) — pinned by ``test_floor_act_maximizes_robust_score`` and
``test_floor_act_is_the_optionality_argmax``, which fail loudly if payoff
retuning ever breaks that ordering — so the blueprint's §6 spec Acts keep
holding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag.advisor.confidence import ConfidenceAssessment, ConfidenceFn, HeuristicConfidence
from app.rag.advisor.explainer import game_theory_basis, talebian_basis
from app.rag.advisor.game import FboStrategy, continuation_discount, sequential_stage_values
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
        confidence_fn: ConfidenceFn | None = None,
    ) -> None:
        self.lambda_fragility = lambda_fragility
        self.omega_optionality = omega_optionality
        #: Confidence seam (ADR-0008): heuristic_v1 by default; a fitted
        #: isotonic table is a drop-in replacement.
        self.confidence_fn: ConfidenceFn = confidence_fn or HeuristicConfidence()

    def optionality_score(self, profile: ActionProfile) -> float:
        """Talebian convexity: R·I − λ(1−R)."""
        return (profile.reversibility * profile.information_yield) - (
            self.lambda_fragility * (1.0 - profile.reversibility)
        )

    def confidence_score(self, n_anchors: int, lab_report_available: bool) -> float:
        """Confidence value via the configured seam (compat float accessor).

        Delegates to :attr:`confidence_fn`; identical numbers to the V1
        heuristic under the default ``heuristic_v1`` parameter set.
        """
        return self.confidence_fn(n_anchors, lab_report_available).value

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

        # Escalation subgame (ADR-0007): backward induction over the
        # act → response → escalate-or-close tree, discounted by the FBO's
        # revealed type (repeat offenders deflate the escalation threat).
        discount = continuation_discount(has_prior_violations)
        stage_rows = sequential_stage_values(discount=discount)

        # Admissible acts in least-to-most-escalatory order, each scored by
        # its computed subgame value plus the Talebian convexity bonus. The
        # FBO best response is the row argmin *anticipating* escalation;
        # ties resolve to the earliest declared strategy (deterministic).
        candidates: list[_Candidate] = []
        for level in sorted(ACTION_PROFILES):
            if level < floor:
                continue
            profile = ACTION_PROFILES[level]
            row = stage_rows[level]
            strategy = FboStrategy.COMPLY
            for candidate_strategy in FboStrategy:
                if row[candidate_strategy] < row[strategy]:
                    strategy = candidate_strategy
            maximin = round(row[strategy], _VALUE_PRECISION)
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

        assessment: ConfidenceAssessment = self.confidence_fn(len(anchors), lab_report_available)

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
                "confidence": assessment.value,
                "citations": [f"Section {primary_anchor.section}"],
                # --- computed game payload (ADR-0006/0007, additive) -------
                "fbo_best_response": best.fbo_strategy.name,
                "maximin_value": round(best.maximin_value, 3),
                "continuation_discount": discount,
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
                # --- confidence provenance (ADR-0008, additive) ------------
                "confidence_param_set": assessment.param_set,
            },
            "abstain_reason": None,
        }


def compute_fso_advisory(
    retrieved_sections: list[str] | tuple[str, ...] | None,
    has_prior_violations: bool = False,
    lab_report_available: bool = False,
    confidence_fn: ConfidenceFn | None = None,
) -> dict[str, Any]:
    """Convenience wrapper with default-tuned selector (module seam)."""
    return DeterministicActSelector(confidence_fn=confidence_fn).select_act(
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
