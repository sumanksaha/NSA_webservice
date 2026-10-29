# Ruler Fix Workflow — from broken binary to robust ruler v4

**Status:** WORKFLOW (execution plan for `Evaluator_V4_Binary_Ruler_Design_Review.md` §5.3 / §5.2)
**Date:** 2026-10-17
**Revision:** 2026-10-17 — composite index added to WF-9 (reporting layer only;
see G11).
**Objective:** Rebuild the answer-correctness ruler so that it closes every gap
found in the measurement process, then re-baseline the frozen benchmark results
under the new ruler.

**Ground truth:** `evaluation/out/ceiling_v5/full_review_tabulation.json`
(150/150 human audit: `model_wrong` 89 · `evaluator_miss` 57 · `reference_narrow` 4).

**Success definition (the target):**
- `CREDIT` binary (recall-first): agrees with human "usable answer" verdicts —
  evaluator_miss recovery ≥ 90%, published false-credit count.
- `CLAIM` binary (precision-first): staged precision targets — v4.0 published
  honestly, v4.1 ≥ 0.60 after reference widening, v4.2 ≥ 0.80.
- `false abstention ≤ 10%`, `evaluator_miss ≤ 5%`, per-component confusion
  matrices published, ruler frozen + versioned, v1/v2/v4 shown side-by-side.
- Composite index (reporting layer, WF-9): one Vals-compatible weighted score
  (accuracy 50 / attribution 40 / presentation 10) for external comparison —
  always published with its per-axis columns, band, and error rate; never a
  promotion gate until calibrated against the 150/150 tabulation.

---

## Gap register (what the workflow eliminates)

| # | Gap (from the design review) | Finding | Eliminated by |
| --- | --- | --- | --- |
| G1 | Component rule measured with disarmed provision leg (`cited_chunk_ids=[]`, `payload_index={}` — `provision_correct` can never be True) | F2 | WF-1, WF-2 |
| G2 | `legal_correct` is itself an overlap threshold (0.35/0.45); recovery driven by overlap, not identity | F1 | WF-2, WF-3 |
| G3 | Per-question OR over 4 arms conflates arm-level with question-level truth | F3 | WF-3 |
| G4 | One shared 0.5 constant across three unrelated mechanisms | F4 | WF-3 |
| G5 | Abstention regex → 38.3% false abstentions; banned markers cover 2 qids | F5 | WF-5 |
| G6 | `critical_error` conflates "wrong provision" with "citations did not resolve" | F6 | WF-3 |
| G7 | Three disconnected reference shapes (single conclusion / overlay one-`add` / gold_concepts) | F7 | WF-4 |
| G8 | 14 qids never widened (human legal task); 24 qids prove more prose is not the fix | §3.2 | WF-6, WF-7 |
| G11 | max-per-ref coverage is provably inert to additive widening (v2 evidence: 38/53 widenings scored Δ0.0; WF-6 rehearsal: 14/14 inert before the amendment). Amendment: two-shape C2 — frozen max-shape control `coverage` vs reference-SET `coverage_union`, primary selected on holdout with pre-declared tie-break (credit holdout F-beta, then lower A7 MAE) | WF-6 | landed v4.2 |
| G9 | No determinism/replicate discipline for a binary decision layer | §3.3 | WF-8 |
| G10 | No re-baselined correctness numbers under a validated ruler | A6 | WF-9 |
| G11 | No single comparable headline metric; commercial-benchmark comparisons are re-derived by hand each time, and a scalar invites Goodhart on one axis | composite decision, 2026-10-17 | WF-9 |

Each workflow step below names: **Objective → Inputs → Work → Outputs → Gate
(pass condition) → Cost**. Steps run 0 LLM calls unless marked.

---

## WF-0 · Ruler versioning + freeze rail (rail, do first, ~½ day)

**Objective:** Make "the ruler changed" a visible, auditable event before any
scoring code moves.

**Work:**
- Create `evaluation/ruler/__init__.py` with `RULER_VERSION` (starts `v4.0-dev`)
  and a `publication_header()` used by every report writer: prints ruler
  version, reference-bundle checksum, threshold table, and the boot rule
  "v1 binary stays frozen as control".
- Rule (from the design review §5.1): a ruler change = version bump + reference
  checksum change. No in-place edits.

**Inputs/Outputs:** none / `evaluation/ruler/` package.
**Gate:** a doctest-style test asserting any report writer emits version +
checksum (add `tests/test_ruler_publication.py`).
**Cost:** 0 LLM.

---

## WF-1 · Measurement-input inventory (kills G1's root cause — ~½ day)

**Objective:** Establish, per artifact, which answers carry REAL cited chunk ids
(+ evidence texts), and which are `[n]`-marker-only. This prevents a repeat of
F2's silent shim.

**Work:** script `evaluation/inventory_answer_evidence.py`:
- scan candidate answer stores: `evaluation/out/ceiling_v5/raw/`
  (`experiment_C_per_question.json`, `experiment_D_per_question.jsonl`,
  `experiment_E_per_question.jsonl`), `ceiling_v5/rerun_gip/*.jsonl`,
  `ceiling_v5/reeval_89/`;
- per qid × condition × arm record: does it carry `cited_chunk_ids` /
  resolvable `[n]→chunk_id` (`retrieval_answer_link.json`,
  `payload_index.jsonl`) or not;
- emit `evaluation/out/ceiling_v5/evaluator_v4_input_inventory.json` with
  coverage counts (n answers with identity-grade inputs vs overlap-only).

**Gate (acceptance):** ≥ 90% of the 150 benchmark qids × 4 arms resolvable at
identity grade **by at least one artifact**; else the report names the missing
artifacts explicitly (never silent).
**Cost:** 0 LLM.

---

## WF-2 · Identity-grade re-measurement of the component rule (core fix for G1, G2)

**Objective:** Replace the disillusioned 93%/0.39 number with the real
component-rule confusion matrix: does `provision_identity` (phrasing-independent)
actually recover evaluator misses when it is *armed*?

**Work:** script `evaluation/measure_component_rule_identity.py`:
- consumes WF-1 inventory; pulls real chunk ids + evidence per answer;
- calls `grading.grade_answer` with REAL `cited_chunk_ids`, `evidence_texts`,
  `payload_index` (loaded from `evaluation/out/cache/payload_index.jsonl`),
  `FamilyMap` from `evaluation.resolution`;
- recomputes the v3 rule (`provision_correct OR (legal_correct AND NOT
  hallucination)`) and variants **per arm** — and the anti-F2 sanity check:
  assert `provision_correct` is True for at least some answers (if still 0
  everywhere, the chunk-id join is broken, not the rule);
- output `evaluation/out/ceiling_v5/evaluator_v4_identity_grade.json`:
  per-arm component vectors + confusion matrices vs the human tabulation for
  rules: {v1_th0.5, proposed_v3, provision_only, legal_only, identity+depth}.

**Gate:** published confusion matrix per rule per arm; F2 finding confirmed or
refuted **with numbers**; if refuted (precision improves materially with real
ids), WF-3's design note is updated before coding continues.
**Cost:** 0 LLM.

---

## WF-3 · Component calibration + decision layer (kills G3, G4, G6)

**Objective:** Build the ruler itself — components with independently
calibrated thresholds and the two derived binaries.

**Work:** module `evaluation/evaluator_v4.py` (core, per answer):
- **Components** (deterministic, 0 LLM):
  - `C1 provision_identity` — payload-key `matches_gold` of cited chunks
    (migration of the F6 conflation: separate `unresolvable_citations`
    infra-signal from `provision_wrong` legal-signal; only the latter may
    zero-out);
  - `C2 conclusion_coverage` — max coverage across the v4 reference bundle
    (built in WF-4) with *per-component calibrated* thresholds (ROC vs the
    human tabulation, not inherited 0.5);
  - `C3 abstention_discipline` — calibrated lexicon (v2 from WF-5) + IE flag;
  - `C4 citation_legitimacy` — `[n]` → chunk id → gold coverage;
  - `C5 hallucination_guard` — unchanged deterministic evidence-blob check.
- **Derived binaries:**
  - `CREDIT = C1 OR (C2 ∧ ¬C5) OR abstain_credit`
  - `CLAIM = C1 ∧ C2 ∧ ¬C5 ∧ ¬false_abstention`
  - Per-answer scoring. Any cross-arm/best-arm roll-up is emitted ONLY in the
    report layer (`v4_scorecard` per-cohort tables), never in the credit logic.
- **Soft validity (acceptance A7):** the component scorecard must also agree
  with the human *partial-credit* audit per question — `human_correct` ∈
  {yes, partial, no} mapped to {1.0, 0.5, 0.0}; publish MAE and
  per-verdict-class soft means with per-component basis counts. Both the soft
  and the binary sides of the ruler are rebuilt; neither inherits v1 semantics.
- Single-value wrapper `binary_correct` is **removed** from new reports;
  dual-metric publication is mandatory (`credit_binary`, `claim_binary`).

**Gate:** implement v4.0 acceptance — components armed + confusion matrices
published; the component threshold table carries per-component, per-class basis
counts (n=150 is small; show the counts, not only rates).
**Cost:** 0 LLM.

---

## WF-4 · Versioned multi-reference bundle (kills G7, prerequisite for G8)

**Objective:** One frozen reference bundle per qid — the conclusion + concepts +
widened refs + expected abstention, single source for C1/C2/C3.

**Work:** script `evaluation/build_references_v4.py`:
- source: benchmark `acceptable_conclusion` + `gold_concepts` +
  `insufficient_evidence`, ∪ `evaluator_v2_overlay.json` widened entries
  (additive merge, same discipline as `step0_dual_score_widen.validate_widening`);
- write `evaluation/evaluator_v4_references.json`:
  `{qid: {conclusions: [...], concepts: [...], expected_abstain: bool, provenance}}`;
- SHA-256 checksum recorded in the file + printed by `publication_header()`.

**Gate:** checksum stable across rebuilds; every C2/C3 consumer imports from
the bundle only (test greps for direct `acceptable_conclusion` use in
evaluator_v4 paths and fails if found).
**Cost:** 0 LLM.

---

## WF-5 · Abstention lexicon v2 (kills G5)

**Objective:** False abstention ≤ 10% (failure-cohort baseline: 38.3%).

**Work:** script `evaluation/calibrate_abstention.py`:
- re-run the lexicon over the worksheet/stored answers per-qid; compare to
  human verdict + reference `expected_abstain`;
- propose (a) marker additions (missed abstentions: 11.7%) and (b) qid-scoped
  banned markers (false positives: 38.3%) — merge path already exists in v2
  overlay schema (`banned_abstain_markers.applies_to_qids`);
- human resolves ambiguous qids (same workflow as WF-6).

**Gate:** measured false_abstention ≤ 10% and missed_abstention ≤ 5% against
the tabulation; every ban/add has a qid-scoped provenance entry.
**Cost:** 0 LLM.

---

## WF-6 · Reference widening — 14 `widen_overlay` qids (HUMAN legal review, G8a)

**Objective:** Author widened gold references for Q012, Q028, Q068, Q071, Q083,
Q090, Q119, Q128, Q132, Q133, Q134, Q135, Q137, Q146.

**Work (human-in-the-loop, machine-assisted):**
- machine generates a *draft packet* per qid: question, gold units, current
  reference, the audit's reviewer notes, the failing answer texts
  (drafts only — the repo rule is explicit: authoring gold text is a legal
  review task and must not be auto-drafted);
- human edits/approves; machine validates (`validate_widening`: anchor,
  additivity, no narrowing) and merges into the WF-4 bundle (`add` entries);
- **supporting context:** retrieval already proves gold reachability is 100%,
  so reference widening is bounded work — the texts exist in the payload index.

**Gate:** all 14 qids merged additively with provenance; bundle checksum
bumped (WF-0 discipline).
**Cost:** human legal review; machine cost 0 LLM (packet generation is local).

**Machine stage landed (WF-6a):** `evaluation/widen_references_v4.py` — packet
builder / validator / additive merger, tests `tests/test_widen_references_v4.py`
(7 gates). 14 packets live under
`evaluation/out/ceiling_v5/widen_v4/packets/` (question + audit record + frozen
references + gold evidence anchors + the four audited arm answers + a labelled
`### WF6-ENTRY` block). No text is machine-drafted. Commands:
`--build-packets` (done), `--validate-packets` (dry-run), then
`--merge-from-packets` after the human pass (additive; version bump automatic;
idempotent — re-runs are no-ops on already-merged entries). The 33
missed-abstention rows from WF-5 (`abstention_calibration_v2.json`,
`missed_abstention_notes`) are NOT widening material: they are generator-side
refusals that were never written, queued here for the human's disposition
alongside the packets.

**WF-6a LANDED:** the human review batch arrived (14/14 filled packets),
passed all repository gates (anchor-verbatim vs payload index, add length,
v1-dedupe, D2-independence) and merged additively — bundle **v1.1.1 → v1.2.1**,
216 total conclusions, 14 WF-6 provenance entries, both checksums verified,
merge idempotent. Rehearsal finding: under the frozen max-per-ref coverage the
merge was **functionally inert** (scorecard byte-identical; answer/add overlap
below answer/v1 overlap for all 14) — see gap register **G11** for the
two-shape C2 amendment (ruler v4.2-dev, scorecard rev 2). Under the amended
ruler the widened bundle produces: CLAIM holdout precision **1.00** (4/4) at
cal t2=0.4 precision 0.81 (staged A2 bound >=0.60 met), soft MAE 0.3253 →
**0.2945** (A7 still open), evaluator_miss vs model_wrong soft separation
**0.614 vs 0.376** — separation WF-2 proved impossible under single-component
thresholds.

---

## WF-7 · Rule-side disposition — 24 `overlay_insufficient` qids (G8b)

**Objective:** Recover the 24 qids that v2 widened once and still fail — by rule
fixes, explicitly NOT by more prose.

**Work:** script `evaluation/audit_insufficient_24.py` — per qid, classify the
confirmed cause against the component vocabulary (from WF-2 identity-grade
data): {abstention-marker FP (WF-5), temporal heuristic, multi-provision —
answer cites correct unit but coverage counts against wrong conclusion string,
penalty-structure absence, unresolvable infra inputs (WF-1 gaps)}. Emit a
disposition table (component-fix per qid).

**Gate:** every one of the 24 qids has exactly one evidence-backed cause class
or is escalated for human review; no reference-prose additions for this bucket.
**Cost:** 0 LLM.

**WF-7 LANDED:** `evaluation/audit_insufficient_24.py` (+ tests, 4 gates) re-judged
all 24 under the v4.2 ruler (WF-5 bans + WF-6 widening + union coverage):
**22 of 24 recovered** via the armed C1 identity lane and the CREDIT rule
(8 also pass CLAIM ≥0.4); **0** abstention-marker FPs, temporal FPs, string
mismatches, or penalty-structure cases remain. The 2 residuals (Q056, Q087 —
both single-use-plastics questions the audit confirms correct) are
`unresolvable_infra_input`: the audited arm stored **no cited-chunk ids** for
them (the WF-1 loss of experiment D/E data), so the identity leg cannot verify
— a data-side repair (re-extraction for the audited arm), not a rule or prose
fix; they are escalated with evidence in `wf7_dispositions.md`. Gate result:
24/24 exactly one class or escalated, zero prose proposed.

---

## WF-8 · Determinism + replicate discipline (kills G9)

**Work:**
- determinism test: two offline full-benchmark passes of evaluator_v4 → byte
  identical verdicts (`tests/test_evaluator_v4_determinism.py`);
- live rules: any live-LLM arm scored under v4 requires 3 replicates and a
  paired gate (dev CLAIM improvement, holdout untouched), matching the
  step0–step3 gate discipline already in `evaluation/`.

**Gate:** CI-green determinism test; replicate rule documented in the
publication header.
**Cost:** 0 LLM offline.

**WF-8 LANDED:** `tests/test_evaluator_v4_determinism.py` (2 tests, all green
in the 33-test ruler suite) enforces (a) two full ``build_rows()`` passes
identical row-by-row, and (b) the FULL published scorecard written twice is
**byte-identical, publication header included** — the header carries no
wall-clock fields by design, so version + bundle checksum + thresholds + boot
rules + body reproduce exactly. The **G9 replicate discipline is now boot
rule 6** in ``evaluation/ruler/__init__.py`` (a live-LLM arm requires 3
replicates and a paired gate: CLAIM must improve on calibration while the
holdout stays untouched); ``validate_header`` rejects any report that omits
it, so document discipline is enforced by the rail itself.

**Version promotion (post-WF-9, first live measurement):** with the WF-8/WF-9
acceptance landed the ``-dev`` suffix is retired — ``RULER_VERSION = "v4.2"``.
Version-promotion is a recorded event, not a ruler-semantics change, so the
bundle checksum is unchanged (1.2.1) and the frozen scorecard/baseline artifacts
were rebuilt so their embedded headers agree. The rail test now fails loudly
if the version ever silently regresses to ``-dev``.

## Live A/B · first live arm under the promoted ruler (boot rule 6, executed)

`evaluation/live_ab_replicate.py` — 3 replicates of the frozen Experiment B
configuration (K=10, CE cache, prompt, model, temp), 20 deterministically
chosen qids (12 calibration / 8 holdout), explicit kill-switch opt-in per run
(refuses without ``LLM_ENABLED=true`` + ``MAX_LLM_CALLS``). 60/60 calls
completed with **zero transport errors** (the frozen baseline K arms carry
~25 % — the live path today is materially healthier; disclosed per arm).
Scores are fresh-graded under v4.2 per answer.

Result (artifact `live_ab_v4_2.json`, header v4.2):
- paired gate vs the frozen baseline's K=10 CLAIM on the SAME 12-qid
  calibration cohort: baseline 0.25 (n=12); live replicates 0.167 / 0.167 /
  0.25 (mean 0.194) → **NOT_IMPROVED** — an identical-config re-run is expected
  to land at or below baseline within noise; publishing honestly rather than
  re-running until a win appears (anti-Goodhart).
- cross-replicate noise band [0.167, 0.25] on CLAIM — a single live run could
  read a quarter of the calibration cohort either way; this IS the reason rule
  6 exists.
- credit on calibration 0.667 / 0.750 / 0.667 — the recall lane is stable
  across replicates (same-band),
- holdout reported untouched (no gate applied there) per rule 6.

**Gate hardening (found live):** the Experiment-B import chain re-runs
``load_dotenv(override=True)`` from ``.env`` (via ``eval_e2e_v2``), which
silently overwrites the caller's explicit env — and this working copy's
``.env`` carries a standing ``LLM_ENABLED=true`` / ``MAX_LLM_CALLS=700``, so a
naive gate read after import always sees "enabled". The opt-in is therefore
captured at CLI entry **before any evaluation import** and passed explicitly
into ``run_live(cohorts, opt_in=(live, budget))``; ``main()`` judges the
caller's environment, never the post-import state. Tests pin both refusal
paths (env-read + explicit tuple). NOTE for the operator: the standing
``.env`` budget is a machine-local footgun — every eval script that imports
the Experiment-B chain inherits a 700-real-call budget; consider
``LLM_ENABLED=false`` in ``.env`` and per-run opt-ins instead (left untouched
— machine-local config is the operator's call).

---

## WF-9 · Re-baseline under ruler v4 (kills G10, closes the loop)

**Work:** script `evaluation/rebaseline_experiment_b.py`:
- rescore Experiment B arms (K-sweep + oracle) through evaluator v4 against
  WF-4 references; outputs side-by-side v1/v2/v4 aggregate tables
  (`credit_binary`, `claim_binary`, per-dimension rubric rates, bands);
- **composite index row in the same tables** — reporting layer only, frozen
  weights, Vals-VLAIR compatible so commercial comparisons are re-runnable
  instead of re-derived by hand (G11). Additive changes only to the existing
  v4 scorecard writer (`evaluation/evaluator_v4.py` →
  `evaluation/out/ceiling_v5/evaluator_v4_scorecard.json`); the WF-9
  rebaseline adds the K-sweep/oracle rows:
  - `accuracy` 50% — rubric dimensions (`rubric_top_lawyer.py`), partial
    credit (maps to the Vals 0–3 accuracy scale via the human tabulation's
    yes/partial/no verdicts);
  - `attribution` 40% — citation precision/recall harmonic mean, with
    groundedness as a floor;
  - `presentation` 10% — rubric presentation dimension;
- **publication rules** (mandatory, same class as WF-3's dual-metric rule):
  per-axis columns always printed beside the composite (never the composite
  alone), band ±5pp (n=150) printed with it, transport-error rate disclosed
  alongside (Exp B precedent: ~25% of requests errored);
- **promotion rule:** the composite is a *reporting index, not an
  optimization target or A/B gate* — adopting it as one requires the accuracy
  axis to first agree with the 150/150 tabulation (`evaluator_miss ≤ 10%`,
  WF-2/WF-3 data). Until then every promotion gate stays per-axis
  (`credit_binary`, `claim_binary`, per-dimension rubric rates): a weighted
  sum may not adopt or veto an arm, because one axis's gain can mask another's
  regression (`retry_guard` per-axis veto precedent; the abstention-
  composition artifact was a scalar-gate failure);
- freeze `evaluation/out/ceiling_v5/evaluator_v4_baseline.json` and declare
  superseded figures (33.1→37.9% K-sweep, ~12% binary) with a pointer to the
  new baseline.

**Gate (overall acceptance, from design review §5.2):**
- A1 identity-grade components armed (WF-2 + WF-3);
- A2/A3 staged CLAIM precision (publish honestly; improve only via WF-6/WF-7);
- A4 determinism (WF-8);
- A5 bands published with every binary;
- A6 new baseline frozen, including the composite-index row with its
  mandatory per-axis columns (reporting layer only, per the promotion rule
  above).

**WF-9 LANDED (workflow complete):** `evaluation/rebaseline_experiment_b.py`
rescored ALL 41 arm-rows of Experiment B through the v4.2 ruler
(8 K-arms + oracle, 1351 stored answers; identity components from the frozen
checkpoint grader vectors, C2/C3 recomputed fresh under bundle v1.2.1) and
froze `evaluation/out/ceiling_v5/evaluator_v4_baseline.json`
(`evaluator-v4-baseline-1`, tests 5 green incl. a rebuild byte-identical
gate). Headline under the new ruler — credit_binary climbs K → 0.354 (K=1),
0.786 (K=100), 0.966 (oracle); claim_binary (precision-first, t2=0.4) 0.04–0.26
(oracle 0.234, holdout-validated precision 1.00); accuracy axis (0–3) 0.61 →
1.51 (oracle 1.61). Transport errors disclosed per arm (K arms ~25 %, oracle
4 %) and the v1 binary control rides beside every v4 column (boot rule R2).
`superseded_figures` contains the v1-era pointers (K-sweep soft 33.1–37.9 %,
binary ~12 %, oracle 35.1/3.45/80.69 %) so the old numbers carry a citation to
their replacement. **All WF-0…WF-9 gates green.**

**Cost:** 0 LLM (rescore over stored answers). Qdrant/Neo4j not required.

---

## Execution order & dependencies

```
WF-0 (rail) ─┬─►  WF-4 (bundle) ─►  WF-6 (human widening) ─► bundle bump
             ├─►  WF-1 (inventory) ─► WF-2 (re-measure) ─► WF-3 (calibrate) ─┐
             ├─►  WF-5 (abstention, needs WF-2 data + WF-4) ──────────────────┤
             └─►  WF-7 (24-qid disposition, needs WF-2 + WF-4) ───────────────┴─► WF-8 ─► WF-9
```

Parallelizable: WF-1 and WF-4 can start immediately; WF-6 (human) can proceed
alongside WF-2; everything before WF-9 is 0-LLM and needs no Qdrant.

## Kickoff checklist (first working session)
1. Create `evaluation/ruler/` + publication-header test (WF-0).
2. Run WF-1 inventory; publish coverage numbers.
3. If ≥90% identity-grade coverage: run WF-2 and post the corrected confusion
   matrix to `evaluation/out/ceiling_v5/evaluator_v4_identity_grade.json`.
4. Generate WF-6 draft packets for the 14 qids (human review begins).

**Non-goals:** no LLM model/skill changes; no retrieval changes; no benchmark
question edits (v1.0 frozen; reference bundle is the only new artifact); no
calls made with `MAX_LLM_CALLS=0` until an explicit, budgeted gate re-enables
them for live A/Bs only; no composite-index optimization or arm gating until
its accuracy axis is calibrated against the tabulation (WF-9 promotion rule).

## Lane A · IE disposition packets (`evaluation/ie_disposition_packets.py`)

The 11 IE qids split into two *measured* classes: A1 answered-in-every-arm
(Q042 Q060 Q090 Q098 — audited arms all answered with zero refusal phrasing)
and A2 no-K-curve-trace (Q119 … Q137 — all checkpoint arms errored, though
their audited arms mostly answered). All 11 already carry
`expected_abstain=True` in the bundle — the ruler side is done; the machine
cannot author the decision. Lane A therefore mirrors the WF-6 packet
architecture for a human choice: build (evidence packet per qid) → validate
(choice in {A, B, C} + human rationale ≥20 chars) → merge (stamps the
NON-scoring `questions[qid].ie_disposition` field — no rule reads it —
bumps the bundle minor version to 1.3.1 on first accept, re-verifies the
content checksum, is idempotent, and refuses conflicting re-decisions
without silent overwrite). The human tabulation is never modified.
Packets: `evaluation/out/ceiling_v5/ie_disposition/packets/{QID}.md` +
reviewer guide `README.md` — **awaiting the legal reviewer's dispositions**;
the merge is the R1-versioned bundle event that pairs with a checksum change.
