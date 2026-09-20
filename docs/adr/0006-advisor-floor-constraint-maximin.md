# ADR-0006: Statutory floor as admissibility constraint + computed zero-sum maximin

- **Status:** Accepted (implemented 2026-09-20: `app/rag/advisor/game.py` +
  selector rework; all blueprint §6 spec Acts reproduce exactly)
- **Date:** 2026-09-20
- **Context:** RAG subsystem (`app/rag/advisor/`), superseding the selection
  mechanics of [ADR-0003](0003-fso-strategic-advisory-agent.md) (its
  transport, feature flag, and fail-closed semantics are unchanged)
- **Related terms in `CONTEXT.md`:** `Act`, `FSO escalation ladder`,
  `Optionality`, `Adversary (analysis subject)`

## Context

ADR-0003 V1 pinned the floor act directly: `select_act` computed the
statutory floor and returned `ACTION_PROFILES[floor]`. The blueprint's
decision rule — `argmax[U + ω·Opt]` over admissible acts — was explicitly
**not** computed, because under the V1 payoffs it contradicted the §6 spec
(it picked Improvement Notice over Sample & Lab-Test for §51). The code
comment acknowledged the contradiction and pinned the table instead.

Consequences of that shortcut:

- `deterrence_power`, `fso_cost`, and `OMEGA_OPTIONALITY` never influenced
  decisions — they appeared only in justification prose.
- `game_theory_basis` asserted *"Minimax optimal against FBO defection"*
  while no minimax, no FBO strategy space, and no best response existed
  anywhere in the code — an integrity gap for a module whose stated purpose
  is auditability.

## Decision

**1. The statutory floor is a hard legal admissibility constraint, never a
selection preference.**

`_statutory_floor()` (selector.py) keeps the V1 branch logic verbatim but
returns `(level, reason)` — cognizable/repeat offence, zero-schedule
anchor, prior-notice prerequisite, lab-evidence requirement, or the default
penalty track. It bounds which rungs are admissible (`level ≥ floor`); it
does not select.

**2. Selection = argmax over admissible acts of a computed robust score.**

A new module, `app/rag/advisor/game.py`, defines a zero-sum FSO×FBO game:

- FBO strategies: `{COMPLY, CONTEST, DEFECT}` (blueprint §2.A player 2).
- FSO payoff: `U(a, s) = retention(a, s)·deterrence(a) − cost(a) −
  response_cost(a, s)`, with `retention` (how much statutory effect survives
  each FBO response) and `response_cost` (FSO's follow-on enforcement /
  litigation burden) as documented tables in the same tuning seam.
- Zero-sum ⇒ FBO best response is `argmin_s U(a, s)` — **computed per act**,
  never assumed. It is non-degenerate: FBO defects weak acts (warn, notice)
  but contests prosecution (defiance against a cognizable act is self-harm).
- The FSO's maximin value of act `a` is `min_s U(a, s)`; the selector picks
  `argmax[maximin_value + ω·optionality]` over admissible acts — the
  blueprint's rule, now over a constraint-feasible set with worst-case
  (robust) deterrence instead of the naive point estimate.

**3. Determinism.** Candidates iterate in ascending ladder order with a
strict `>` comparison, so exact ties resolve to the least-escalatory act
(Talebian tie-break). Maximin values are rounded to 6 decimals before
scoring so acts with equal documented values tie exactly rather than by
float dust.

**4. Payload extended additively.** `fso_act` gains `fbo_best_response`,
`maximin_value`, `binding_constraint` (`{type, level, reason}`), and
`admissible_acts` (the full computed score table with a `selected` flag).
Every ADR-0003 field is unchanged; the graph node, service, and routes pass
the dict through untouched.

**5. Explanations cite computed facts.** `game_theory_basis` now renders
the best response, maximin value, binding floor + reason, and the margin
over the runner-up — every claim traces to a computed quantity.

## Consequences

- **Positive:** the module's claims are now true — "minimax optimal" is a
  computed result, not prose. The payoff tables and `OMEGA_OPTIONALITY` are
  live decision inputs, tunable in one seam.
- **Positive:** the V1 contradiction is resolved: under the shipped tables
  the robust-value ordering and the optionality ordering both align with the
  ladder below the floor, so the blueprint §6 spec Acts reproduce exactly
  (all 31 pre-existing advisor tests pass unchanged), and the decision is
  invariant for any `ω ≥ 0` (pinned by test).
- **Risk / trade-off:** the decision is now *payoff-sensitive* where V1 was
  table-pinned. Two invariant tests —
  `test_floor_act_maximizes_robust_score` (ADR-0006) and
  `test_floor_act_is_the_optionality_argmax` (ADR-0003 suite) — fail loudly
  if retuning ever breaks the ordering the spec Acts depend on; shipped
  margins are ≥ 1.0 robust-score units in every spec scenario.
- **Deferred (from the 2026-09-20 SOTA review):** sequential
  extensive-form game over the ladder (backward induction, repeated-game
  treatment of repeat offenders), calibrated confidence (conformal /
  isotonic), Value-of-Information grounding of the optionality score, and
  the outcome-learning loop with versioned parameter sets.
