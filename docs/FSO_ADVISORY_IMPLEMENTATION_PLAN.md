# FSO Strategic Advisory — Implementation Plan (Deterministic Game-Theory + Talebian)

Status: **Draft for review** — derived from `docs/FSO_GAME_THEORY_ADVISORY_BLUEPRINT.md` + `docs/adr/0003-fso-strategic-advisory-agent.md` + `CONTEXT.md` audit (2026-09-19).  
Target seam: `app/rag/advisor/` (new deep module). Graph seam: `app/rag/agent/graph.py`. Config seam: `app/shared/config.py`.

## 0. What this plan fixes before coding

### 0.1 Blueprint vs ADR vs CONTEXT mismatches

| # | Disagreement | Resolution (this plan) |
|---|---|---|
| 1 | **Node placement** — Blueprint §5: `evidence_sufficiency → fso_advisory_node → generate`. ADR §Decision-3: `retrieve → advise → generate`. Both are **pre-generation** | Advisory must be **post-generation/verification**, not pre-retrieval. The `fso_act` is `advisory_abstain_reason` = fail-closed on `insufficient_statutory_grounding` when citations lack a grounded FSS §. That signal only exists after `generate → verify → citation_quality` (linear) or `evidence_sufficiency` (DAG). Correct placement: **single `fso_advisory` node before `finalize` on all paths** (DAG: after `synthesize→verify→citation_quality`; linear: same). All paths converge at `finalize`, so one insertion covers both. |
| 2 | `extracted_sections` source undefined | Sections come from **grounded evidence**, not free text. Extraction order: `chunk.payload.section_number` / `chunk.legal_identity` (from `app/rag/enrichment/deterministic.py` + Qdrant payload) → fallback regex `§\s*(32|51|52|55|56|58|63|64)` over chunk text → de-dupe. Never parse the LLM answer alone — that would re-introduce hallucination. |
| 3 | §32 missing from `fss_sections.md` but listed as "Improvement Notice" | §32 is real Act text (Improvement Notice) but not in `fss_sections.md`. Penalty schedule for 32 is `max_fine=0, imprisonment=0` (notice, not penalty). Source it from the Act, add comment citing Chapter reference. If corpus later lacks §32 text, advisory must still emit `Improvement Notice u/s 32` as an Act (it is not a citation-dependent fine). |
| 4 | Duplicate ADR `0003` (`0003-fso-strategic-advisory-agent.md` vs `0003-inspection-module-deepening.md`) | Rename deepening ADR to `0004-inspection-module-deepening.md` before merging advisor (pure file move, keep content). |
| 5 | Blueprint `omega_optionality` / `lambda_fragility` as constructor args vs ADR "parameterised payoff table" | Make them **named constants in `ladder.py`** (one tuning point) and expose only via module-level defaults; selector constructor reads them but tests can override. Keeps the seam tunable without touching node logic. |
| 6 | `has_prior_violations` / `has_lab_report` flags have no state wiring | Wire from explicit request fields (`is_repeat_offender`, `has_lab_report` — opt-in booleans on `RAGState`, default `False`). Never infer from retrieved text. UI checkbox → `POST /api/rag/query/agent` payload → `service.run_agent_query` → `initial_state`. |
| 7 | Phase 1 simplified 2-player game vs full 4-player | Keep Phase 1 as 2-player (FBO vs FSO) per ADR Consequences. Structure `penalties.py` + `selector.py` so a future `FourPlayerPayoff` adapter can replace the 2-player payoff without moving the node. |

### 0.2 Non-goals for V1

* LLM explains the Act (justification prose) — deferred; V1 only emits deterministic `game_theory_basis` / `talebian_basis` templates from `explainer.py`. LLM **cannot** veto `fso_act`.
* Persistence of `fso_act` to a claim ledger / DB.
* Legacy Flask `POST /api/rag/query` wiring — agent-only (`/api/rag/query/agent` + `/api/v2/rag/query/agent`).
* UI rendering beyond attaching `fso_act` to the JSON payload (rendering is a follow-up).

---

## 1. Architecture

```
              RAGState (chunks + response + routing)
                         │
         ┌───────────────┴───────────────┐
     linear path                     DAG path
 retrieve → generate → verify →  plan_tasks → budget_gate → execute_task
           citation_quality              → evidence_sufficiency
                         │                → synthesize → verify → citation_quality
                         └───────────────┬───────────────┘
                                    fso_advisory  ← deterministic, no LLM
                                         │
                                      finalize  → RAGResponse { answer, citations, ..., fso_act }
```

* `fso_advisory` is a **pure function** node: `(extracted_sections, is_repeat_offender, has_lab_report) → {fso_act, abstain_reason}`. Latency budget <1 ms, zero tokens.
* Fail-closed: `fso_act=None` + `advisory_abstain_reason="insufficient_statutory_grounding"` when no § in `FSSAI_PENALTY_SCHEDULE` matches, or when `citation_quality_ok=False` and the only sections are from missing citations.

File seam (per blueprint §3, corrected):

```
app/rag/advisor/
├── __init__.py          # public re-export: compute_fso_advisory, DeterministicActSelector, FSSAI_PENALTY_SCHEDULE
├── penalties.py         # StatutoryAnchor dataclass + FSSAI_PENALTY_SCHEDULE (ground truth from fss_sections.md + §32)
├── ladder.py            # EscalationLevel + ActionProfile + ACTION_PROFILES + tuned constants (LAMBDA/OMEGA, thresholds)
├── selector.py          # DeterministicActSelector.select_act (minimax + convexity)
└── explainer.py         # template justification (game_theory_basis / talebian_basis) — no LLM
app/rag/agent/nodes/advisory.py  # fso_advisory_node(state) → partial RAGState (thin adapter)
```

Rationale: `app/rag/advisor/` owns the math; `app/rag/agent/nodes/advisory.py` owns the graph I/O (section extraction, state keys). Keeps the advisor testable without importing LangGraph.

---

## 2. Config seam (Step 1 — do first)

* **One row** in `app/shared/config.py::_TABLE`:

```python
Setting(
    "FSO_ADVISOR_ENABLED",
    "fso_advisor_enabled",
    bool,
    False,
    help="Attach deterministic FSO Act advisory (game-theory+Talebian) on the agent path.",
)
```

* One line in `.env.example` (`FSO_ADVISOR_ENABLED=false`) — enforced by `tests/test_shared_config.py::test_env_example_keys_are_declared`.
* Accessor: `cfg.fso_advisor_enabled`.
* Wiring: `app/rag/agent/graph.py::_resolve_fso_advisor(enabled: bool|None)` mirroring `_resolve_evidence_selector`; `build_graph(..., fso_advisor: bool|None=None)` inserts the node. Cache key becomes `(hitl, evidence_selector, fso_advisor)` — see §4.

---

## 3. State extensions

Add to `app/rag/agent/state.py::RAGState`:

```python
# --- FSO advisory (Phase: FSO strategic advisory) ---
extracted_sections: list[str]  # derived from chunks, for telemetry only
is_repeat_offender: bool  # input flag — previous convictions
has_lab_report: bool  # input flag — lab evidence available
fso_act: dict[str, Any] | None  # selector output
advisory_abstain_reason: str | None  # "insufficient_statutory_grounding" | None
fso_advisory_enabled: bool  # resolved per-request for audit
```

Extend `initial_state()` defaults: `is_repeat_offender=False, has_lab_report=False, fso_act=None, advisory_abstain_reason=None`.

All keys optional with defaults so existing tests/legacy payloads keep passing.

---

## 4. Graph integration

### 4.1 Insertion point

Insert **once**, before `finalize`, after the verification gates that already exist on every path:

* Linear: `citation_quality → fso_advisory → finalize`
* DAG: `synthesize → verify → citation_quality → fso_advisory → finalize` (DAG's `synthesize` already feeds `verify`; reuse the same tail)
* `abstain → finalize` also flows through `fso_advisory` (abstained answer still gets `abstain_reason`).

Implementation (inside `build_graph`):

```python
def _resolve_fso_advisor(explicit): ...


# after citation_quality node creation
if _resolve_fso_advisor(fso_advisor):
    builder.add_node("fso_advisory", lambda s, c=None: nodes.fso_advisory_node(s))
    # rewire edges: citation_quality → fso_advisory → finalize (and DAG equivalents)
    # when HITL on: citation_quality → review → fso_advisory → finalize
```

Keep HITL ordering: `review` (human approval) still gates `finalize`; advisory runs **after** review so the Act reflects the approved answer (alternatively before — decision is to run after review; document the choice).

### 4.2 Cache

`_graph_cache: dict[tuple[bool,bool,bool], Any]` keyed by `(hitl, evidence_selector, fso_advisor)`. `_get_graph(hitl, evidence_selector, fso_advisor, checkpointer)` resolves all three. Graphs with a checkpointer bypass cache as before.

### 4.3 Node (`app/rag/agent/nodes/advisory.py`)

```python
_selector = DeterministicActSelector()  # singleton, stateless


def fso_advisory_node(state) -> dict:
    extracted = _extract_sections(state)  # from chunks + response.citations
    result = _selector.select_act(
        retrieved_sections=extracted,
        has_prior_violations=bool(state.get("is_repeat_offender")),
        lab_report_available=bool(state.get("has_lab_report")),
    )
    return {
        "extracted_sections": extracted,
        "fso_act": result["fso_act"],
        "advisory_abstain_reason": result["abstain_reason"],
        "fso_advisory_enabled": True,
    }
```

`_extract_sections` reads `state["chunks"]` + `state["evidence"].values()` (DAG) + `state["response"].get("citations")`, pulls `payload.section_number` / `legal_identity.section`, dedupes, filters through `FSSAI_PENALTY_SCHEDULE`.

Fail-closed path writes `fso_act=None` and the abstain reason; `finalize_node` copies it into `response["fso_act"]` / `response["advisory_abstain_reason"]` (additive fields, see §5).

---

## 5. Deterministic core (`app/rag/advisor/`)

### 5.1 `penalties.py`

```python
@dataclass(frozen=True)
class StatutoryAnchor: section, title, max_fine_inr, imprisonment_months, is_cognizable: bool, requires_prior_notice: bool

FSSAI_PENALTY_SCHEDULE = {
  "32": Anchor("32","Improvement Notice",0,0,False,False),
  "51": Anchor("51","Penalty for sub-standard food",500_000,0,False,False),
  "52": Anchor("52","Penalty for misbranded food",300_000,0,False,False),
  "55": Anchor("55","Failure to comply with FSO directions",200_000,0,False,True),
  "56": Anchor("56","Unhygienic/unsanitary processing",100_000,0,False,False),
  "58": Anchor("58","Contravention without specific penalty",200_000,0,False,False),
  "63": Anchor("63","Business without licence",500_000,6,True,False),
  "64": Anchor("64","Subsequent offences",1_000_000,12,True,False),
}
```

Values sourced from `fss_sections.md` (or Act for §32); cite section in comments.

### 5.2 `ladder.py`

As blueprint §4.2 verbatim, plus:

```python
LAMBDA_FRAGILITY = 0.5
OMEGA_OPTIONALITY = 2.0
```

### 5.3 `selector.py` — implement blueprint §4.3 verbatim with two fixes

* Fix 1: `requires_prior_notice` gate for §55 — if `requires_prior_notice and not lab_report_available` → `min_level=IMPROVEMENT_NOTICE` (emit §32), matching Act's prior-notice requirement.
* Fix 2: When `extracted_sections` contains multiple §, `primary_anchor = max(anchors, key=(imprisonment, max_fine))` — so §64 always beats §51.
* Return shape always `{"fso_act": dict|None, "abstain_reason": str|None}`; never raise (fail-closed).
* `assert best_act is not None` only fires when `anchors` non-empty — keep, but guard with explicit `if best_act is None: return abstain`.

### 5.4 `explainer.py`

Pure string templates (no LLM): `game_theory_basis` + `talebian_basis` + `citations` as in blueprint. Keeps deterministic guarantee.

---

## 6. Service + routes + health

* `app/rag/agent/service.py::run_agent_query` gains `fso_advisor: bool|None`, `is_repeat_offender: bool`, `has_lab_report: bool` passthrough → `initial_state(...)`. Validation: booleans only.
* `app/rag/agent/routes.py::query_agent` reads `payload.get("fso_advisory")` (bool, optional, per-request override) plus the two offender/lab flags; flag resolution: `cfg.fso_advisor_enabled` if body field absent, body field wins if present (same pattern as `use_agent`). Thread the flags into `run_agent_query`.
* `app/rag/agent/graph.py::run_agent` / `resume_agent` accept `fso_advisor` passthrough to `_get_graph`.
* `/api/rag/health` reports `fso_advisory: {enabled: bool}` alongside existing `reranker`/`embedder` probes (auth-gated details omitted).

Backward-compat: default `false`, so existing traffic unchanged; response field `fso_act` appears only when enabled.

`finalize_node` augmentation:

```python
response["fso_act"] = state.get("fso_act")  # None or dict
if state.get("advisory_abstain_reason"):
    response["advisory_abstain_reason"] = state["advisory_abstain_reason"]
```

---

## 7. UI wiring

* `app/templates/rag/query.html` — add checkbox "FSO advisory (game-theory + Talebian)" next to "Use agent pipeline". Disabled/hidden when `fso_advisor_enabled` is off? Instead show always but gate per-request via `fso_advisory` boolean (same as `use_agent`).
* `app/static/js/rag_query.js` — POST `fso_advisory` + `is_repeat_offender` + `has_lab_report` alongside `use_agent`; render `fso_act` block (act, statutory_anchor, game/talebian bases, confidence, citations) or abstain reason.
* FastAPI gateway `asgi.py` v2 route mirrors Flask wiring (pydantic model gains `fso_advisory: bool|None`, `is_repeat_offender: bool=False`, `has_lab_report: bool=False`).

---

## 8. Tests (mirrors blueprint §6 + ADR Consequences)

File: `tests/test_rag_agent_advisor.py` (new). All offline, no Qdrant/LLM/torch.

```python
test_fail_closed_no_anchor  # [] → fso_act=None, abstain_reason="insufficient_statutory_grounding"
test_substandard_without_lab  # ["51"], lab=False → SAMPLE_LAB_TEST anchored on §51
test_unlicensed_operation  # ["63"] → PROSECUTION anchored on §63
test_subsequent_offence_prior  # ["51","64"], prior=True → PROSECUTION anchored on §64 (max severity)
test_improvement_notice_gate  # ["55"], lab=False → IMPROVEMENT_NOTICE (prior-notice rule)
test_repeat_offender_escalates  # ["51"], prior=True → PROSECUTION
test_unknown_section_ignored  # ["99"] → abstain
test_mixed_known_unknown  # ["99","52"] → anchored on §52
test_optionality_scores_are_deterministic
test_graph_attaches_fso_act_when_enabled  # integration: run_agent with flag → response contains fso_act
test_graph_no_fso_act_when_disabled
```

Other tests:

* `tests/test_shared_config.py` — `FSO_ADVISOR_ENABLED` in `.env.example` parity.
* `tests/test_rag_agent_graph.py` — graph topology includes `fso_advisory` node when flag on; cache key includes third bool.
* `tests/test_rag_agent_routes.py` — Flask `POST /api/rag/query/agent` validation for `fso_advisory` / offender flags.

Run `pytest tests/test_rag_agent_advisor.py -v` as gate.

---

## 9. Task breakdown (ticket order — edges as text)

1. **ADR housekeeping** — rename `0003-inspection-module-deepening.md → 0004-...` (no code, unblocks).
2. **Config seam** — add `FSO_ADVISOR_ENABLED` row + `.env.example` line + `seed_config` parity.
3. **`app/rag/advisor/` seam** — `penalties.py`, `ladder.py`, `selector.py`, `explainer.py`, `__init__.py` + unit tests `test_rag_agent_advisor.py` (§8 list; purely deterministic, zero graph dependency).
4. **State extension** — extend `RAGState` + `initial_state()` + `finalize_node` (`fso_act` passthrough).
5. **Advisory node** — `app/rag/agent/nodes/advisory.py` (`fso_advisory_node` + `_extract_sections`) with its own unit tests.
6. **Graph integration** — `build_graph(fso_advisor=...)` + `_resolve_fso_advisor` + `_get_graph` cache key extension + conditional edges before `finalize`; hitl ordering documented; topology tests.
7. **Service/routes/health** — `run_agent_query`/`resume_agent_query` passthrough, `query_agent` payload parsing, health probe, FastAPI v2 pydantic.
8. **UI** — `query.html` checkbox + `rag_query.js` render of `fso_act` / abstain reason.
9. **Docs** — update `CONTEXT.md` "FSO strategic advisory" entry with `FSO_ADVISOR_ENABLED` and graph node name; set ADR-0003 Status=Accepted.

Edges: 2 → 3, 3 → 5, 4 → 6, 5 → 6, 6 → 7, 7 → 8, 8 → 9. (4 can run in parallel with 3/5.)

---

## 10. Verification checklist (before merge)

- [ ] `FSO_ADVISOR_ENABLED` appears in `cfg.table()` and `.env.example`; `pytest -k test_env_example_keys_are_declared` passes.
- [ ] `python -m pytest tests/test_rag_agent_advisor.py tests/test_rag_agent_nodes.py tests/test_rag_agent_graph.py -v` passes (no network, no Qdrant).
- [ ] Graph builds in all four combos: `(hitl × evidence_selector × fso_advisor)` = 8 topologies; `_graph_cache` key includes all three.
- [ ] `finalize` response always contains `fso_act` when enabled (dict or null) and `advisory_abstain_reason` when abstained; never mutates `answer` / `citations` / `groundedness`.
- [ ] Section extraction proven: synthetic chunk with `payload.section_number="51"` yields `extracted_sections=["51"]`; chunk with only text `"Section 55 direction"` yields same via regex fallback.
- [ ] Fail-closed proven: empty `chunks` or unknown sections → `fso_act=None`.
- [ ] `ruff check . && ruff format --check . && mypy` clean on `app/rag/advisor/**`.
- [ ] No LLM import in `app/rag/advisor/**` (grep `GroundedLLMClient` / `openai` yields zero).

---

## 11. Rollout

Default `FSO_ADVISOR_ENABLED=false`. Merge inactive. Canary by per-request `fso_advisory:true` on `POST /api/rag/query/agent` (UI checkbox) without flipping global flag. Promote to default `true` only after offline test suite + manual probe on staging (`curl /api/rag/health` shows `fso_advisory.enabled`).

---

## 12. Follow-ups (explicitly deferred)

* LLM justification enrichment (gated by same lenses, `game_theory`/`talebian` prompts in Notepad service) — renders prose around the deterministic Act without overriding it.
* 4-player game (AO/Court as players) — swap `selector` payoff adapter.
* Act persistence / audit ledger.

