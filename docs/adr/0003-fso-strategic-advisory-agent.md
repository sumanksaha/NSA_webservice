# ADR-0003: FSO strategic advisory agent (game-theory + Talebian)

- **Status:** Proposed
- **Date:** 2026-09-19
- **Context:** RAG subsystem (`app/rag/`), agent graph in `app/rag/agent/`
- **Deciders:** Architecture review (grill-with-docs: grilling + domain-modeling rounds 1–2)
- **Related terms in `CONTEXT.md`:** `Act`, `FSO escalation ladder`, `Optionality`, `Skin-in-the-game`, `Adversary (analysis subject)`

## Context

The RAG agent already produces a grounded, cited `RAGResponse`
(`answer` + `citations` + `retrieved_chunks` + `groundedness`). There is no
mechanism to step *from* that legal analysis *to* a recommended FSO action,
resolved through explicit game-theoretic and Talebian principles.

The Notepad `evaluate_note` path (`app/ai_assistant/service.py`) already emits
`game_theory` and `talebian` as free-text LLM lenses — but (a) they sit on
*Notepad notes* (ideas/todos), not on grounded legal analysis, and (b) they are
soft prompts, not a strict decision model. The FSO-advisor requirement is
"the act is determined **strictly** based on game theory and Talebian strategy"
— i.e. the *Act must be computable*, not prompted.

The decision has two hard-to-reverse surfaces that must be settled together:
(1) **where the Act is decided** (deterministic engine vs. LLM), and
(2) **which transport the pre-generation checkbox lives on** (LangGraph agent
vs. legacy/pipeline). Getting these wrong retrofits the whole module.

## Decision

**1. The Act is chosen by a deterministic Act-selector; the LLM only explains it.**

Per Q7 (confirmed by the design interview): act-selection is a pure, stateless
function over grounded evidence — never an LLM call. The "formal model buildup
later" (Q3B) therefore means formalising the *explanation* + sharpening payoffs,
*not* moving Act-decision into the LLM.

- The selector reads the **§section numbers** cited in the retrieved chunks
  (`fss_sections.md` provides the penalty schedule: §51 ₹5L, §52 ₹3L, §55
  ₹2L, §56 ₹1L, §58 ₹2L, §63 up to 6 mo + ₹5L, §64 2× + daily fine + licence
  cancellation). Sections anchor the **statutory consequence** half of each
  payoff.
- **FSO escalation ladder** (CONTEXT.md): inspect & warn → sample & lab-test →
  Improvement Notice §32 → show-cause / penalty direction §55 → prosecution.
  The deterministic engine walks this ladder least-to-most-irreversible.
- **Game-theory filter:** a 2-player (FBO vs FSO) cost-to-FBO vs
  enforcement-cost-to-FSO comparison; the Act is the least-cost FSO move that
  still meets the grounded statutory consequence **given FBO best-response
  (defection tolerated)**. Selection is minimax-over-FBO-defection → robust Act.
- **Talebian filter (optionality):** among game-theoretically admissible Acts,
  prefer the one with the highest `optionality_score` (reversibility ×
  upside-under-evidence-discovery); escalate to irreversible Acts **only when
  the grounded penalty schedule is unmet**. This is the convexity/optionality
  rule from Q5.
- An LLM explainer (gated by the same `game_theory`/`talebian` lens prompts)
  renders the *justification prose* for the chosen Act only. The LLM **cannot**
  override, veto, or substitute the Act.

**2. The Act is attached, not substituted; fail-closed on missing grounding.**

- When the checkbox is on, the graph runs the selector and attaches one
  `fso_act` field to the `RAGResponse`:
  `{act, statutory_anchor, action, game_theory_basis, talebian_basis,
  confidence, citations}`. The grounded legal `answer` is preserved unchanged.
- If retrieval returns **no actionable FSS §** for the query, the selector
  abstains: `fso_act = null` with an `abstain_reason =
  "insufficient_statutory_grounding"` — fail-closed (no Act without a cited
  statutory basis). This reuses the graph's existing `abstain_node` semantics.

**3. Surface: the checkbox lives on the LangGraph agent path, as a graph node.**

- A new **opt-in** feature flag `FSO_ADVISOR_ENABLED` (one `cfg` row + one
  `.env.example` line, enforced by `test_env_example_keys_are_declared`).
- The existing `rag/query.html` "Use agent pipeline" checkbox is wired to also
  enable advisory mode; the deterministic selection runs as a new `advise`
  node between `retrieve` and `generate` inside `build_graph`.
- This is the v2 `/api/v2/rag/query/agent` transport (the existing UI posts
  there). The pre-existing half-wired `/api/v2/rag/query` gap is **out of
  scope** here and tracked separately (Q2-B), so it is not conflated with this
  decision.

## Consequences

- **Positive:** the Act is auditable and offline-testable — Act-selection has
  no LLM dependency, so `tests/test_rag_agent_advisor.py` can assert exact Acts
  from synthetic §-cited chunk fixtures (mirroring `test_rag_agent_nodes.py`).
- **Positive:** grounding is preserved end-to-end — an Act's statutory anchor
  is always a *retrieved* § section, and the `abstain_reason` path is the
  same `abstain_node` the graph already uses.
- **Positive:** backward compatible — the flag is opt-in and off by default;
  the `answer` is untouched; `fso_act` is a purely additive response field.
- **Risk / trade-off:** Phase 1 uses a *simplified* 2-player game (FBO vs FSO)
  rather than a full 4-player (FBO/FSO/Adjudicating Officer/Court) model. This
  is the agreed "soft start" (Q3B); richer game structure is deferred to the
  formal-model-buildup phase and is explicitly non-blocking for V1.
- **Risk / trade-off:** the selector is deterministic but *parameterised*
  (penalty values, cost weights, λ for optionality, reversibility flags,
  per-Act escalation ranks) — these live in one seam (`app/rag/advisor/`
  payoff table) so the model is tunable without touching node logic.
- **Out of scope:** UI rendering of the Act beyond attaching `fso_act`;
  persistence of Act recommendations to a claim ledger; the legacy Flask
  `/api/rag/query` path (opt-in is agent-only in V1).
