# ADR-0007: Sequential escalation game over the FSO ladder

- **Status:** Accepted (implemented 2026-09-20: `sequential_stage_values` /
  `escalation_subgame_values` in `app/rag/advisor/game.py` + selector
  rework; all blueprint §6 spec Acts reproduce exactly)
- **Date:** 2026-09-20
- **Context:** RAG subsystem (`app/rag/advisor/`), extending the selection
  mechanics of [ADR-0006](0006-advisor-floor-constraint-maximin.md) (its
  statutory-floor constraint, Talebian optionality term, and fail-closed
  semantics are unchanged)
- **Related terms in `CONTEXT.md`:** `Act`, `FSO escalation ladder`,
  `Optionality`, `Adversary (analysis subject)`

## Context

ADR-0006 selects over simultaneous maximin values `min_s U(a, s)`: the FBO
responds to each act in isolation. The real FSO–FBO relationship repeats —
every low rung carries an implicit escalation threat ("defy this and the
next act follows"). A simultaneous game cannot price that threat, and it
cannot distinguish a first-time FBO (threat credible) from a convicted
repeat offender (defiance already revealed as sustainable).

## Decision

**1. Extensive-form game solved by backward induction (top rung down).**

The tree: FSO plays act `a_ℓ` → FBO responds `s` → on a non-complying
response the FSO may **close** (bank `U(a_ℓ, s)`) or **escalate** directly
to any higher rung's subgame — the statutory floor permits skipping (e.g.
repeat offences jump straight to prosecution), so the continuation value
is the *best* higher subgame, not merely the adjacent rung:

    escalate(ℓ, s) = δ · max_{k>ℓ} V(k) − cost(ℓ) − response_cost(ℓ, s)

Escalating keeps sunk costs and discards the abandoned rung's partial
deterrence (the higher act re-achieves the statutory outcome on its own).
COMPLY never escalates (nothing to follow up). The top rung is absorbing
(`W = U`, no escalation branch). Each stage row holds
`W(a_ℓ, s) = max(U, escalate)`; the FBO best response is `argmin_s W`
*anticipating* escalation; `V(ℓ) = min_s W` is the subgame value the
selector scores. Ties keep close (strict `>`) and resolve to the earliest
declared strategy — deterministic, least-escalatory bias.

**2. Repeated-game continuation discount δ encodes offender type.**

`continuation_discount(has_prior_violations)`: first-time FBO `δ = 1.0`
(escalation threat fully credible), convicted repeat offender `δ = 0.3`
(threat deflated — only the immediate statutory effect counts). The
statutory floor still escalates repeats by law (§64); δ explains *why*
that is strategically sound. δ is reported in the payload
(`continuation_discount`), not assumed silently.

**3. Selector scores subgame values; payload extended additively.**

`select_act` solves `sequential_stage_values(discount)` per call and
picks `argmax[V(ℓ) + ω·optionality]` over admissible acts. `fso_act` gains
`continuation_discount`; `maximin_value` now carries the subgame value
`V(ℓ)` (coincides with the simultaneous maximin under shipped tables —
see below). Every ADR-0003/0006 field is unchanged.

## Consequences

- **Positive:** the FBO response genuinely anticipates escalation
  (subgame-perfect), and repeat offenders are priced distinctly — both
  pinned by `tests/test_rag_advisor_sequential.py` (13 tests, hand-derived
  values, synthetic-table mechanics test).
- **Positive:** backward compatible — under the shipped tables prosecution
  is dominated (`V(5) = −5.0`), so closing always beats escalating and
  `W ≡ U` at both discount values; all blueprint §6 spec Acts hold and all
  31 pre-existing advisor tests pass unchanged.
- **Risk / trade-off:** `δ = 0.3` is an uncalibrated tuning constant (no
  outcome data exists yet); it lives in the `game.py` tuning seam next to
  `RETENTION` / `FSO_RESPONSE_COST` and is reported per-decision so its
  influence stays auditable. Calibrate against outcomes when the
  ADR-0008 learning loop arrives.
- **Risk / trade-off:** the sequential machinery is currently a no-op on
  shipped payoffs (option value only). If retuning ever makes escalation
  profitable, the invariant tests
  (`test_floor_act_maximizes_robust_score_both_discounts`, spec-Acts tests)
  fail loudly.
