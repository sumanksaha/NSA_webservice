# Concept shortfall: how to close the 85 without losing the gains

Researcherrun 2026-10-12, **0 LLM calls** (offline joins only). Reproduce with
`python -m evaluation.segment_shortfall`; per-question rows in
`out/.../reeval_89/shortfall_segments.json`.

## 1. The 85, segmented by bottleneck

| segment | n | meaning | QIDs |
|---|---|---|---|
| RETRIEVAL | 37 | gold outside top-10 | Q005 Q007 Q020 Q021 Q025 Q026 Q033 Q034 Q038 Q039 Q040 Q050 Q052 Q055 Q063 Q072 Q076 Q082 Q088 Q096 Q097 Q102 Q104 Q110 Q115 Q116 Q117 Q121 Q122 Q125 Q127 Q139 Q141 Q144 Q145 Q147 Q149 |
| MIXED | 15 | gold in window, middling headroom | Q022 Q035 Q036 Q049 Q051 Q057 Q078 Q085 Q086 Q105 Q109 Q111 Q126 Q129 Q131 |
| GATE_EDGE | 12 | 0 concepts missing, still binary-negative | Q016 Q023 Q048 Q067 Q079 Q080 Q084 Q089 Q100 Q114 Q123 Q124 |
| GENERATION | 11 | context covers, answer drops | Q014 Q031 Q032 Q053 Q054 Q058 Q059 Q073 Q074 Q075 Q112 |
| CORPUS | 10 | even gold provisions cover <0.30 | Q010 Q018 Q044 Q062 Q070 Q094 Q107 Q118 Q120 Q130 |

Ranks inside RETRIEVAL: min 11, median 31, max 310; **11 at rank 11-20**
(reachable by a cap-20 retry), 25 beyond rank 20, 1 never retrieved (Q097).
Authority missed on 15/37 RETRIEVAL, 3/11 GENERATION, 3/10 CORPUS, 1/15 MIXED.

## 2. Corrections to prior framing (all change the yield math)

1. **GATE_EDGE is 2/3 already won.** 8 of the 12 (Q016 Q023 Q048 Q067 Q079 Q084
   Q100 Q114) are `resolved`/eliminated on the base arm in `status_report` —
   they fail *binary* but pass *elimination*, and elimination is the counter
   that reads 11 remaining. Only 4 need action: Q080/Q089 (authority-missed),
   Q124 (never retrieved, no generation on either arm), Q123 (base resolves).
   No retrieval or generation work should touch the 8.
2. **Q052 already passes live** (elim=True, bin=1, soft 0.52 on current
   retrieval) despite sitting in RETRIEVAL in the cache — its gold is in the
   loose window. Cached segmentation understates post-remediation retrieval.
   Q052 joins the veto set as a fragile gain.
3. **The scoped arm harms inside GATE_EDGE**: Q100 (base resolved → scoped
   generation), Q123 (base resolved → scoped never_retrieved), Q023 (base
   resolved → scoped generation). Third independent reason to keep it off,
   after the 0-delta recall and the Q060/Q119/Q128 control regressions.
4. **Stage-1 offline retry already recovered 11 of the 37** (Q005 Q007 Q020 Q025
   Q040 Q076 Q096 Q110 Q127 Q141 Q149), plus Q049/Q085 (MIXED) and
   Q053/Q058/Q059 (GENERATION-segment, gold at rank 11-20) — 15 reachable by a
   cap-20 retry with **0 regressed** across 150.

## 3. Treatment per segment (with guardrail for each)

**RETRIEVAL → conditional second pass via the existing TargetPlan retry.**
The machinery exists and is measured: `TargetedRetryPlanner` +
`targeted_retry_node`, Stage-1 A/B +0.147 gold-in-window (CI 0.09-0.21),
22 recovered, 0 lost. What the measured pipeline lacks is the loop itself —
`reeval_89.phase_generate` calls `service.generate` single-pass, so the 85
never got a retry. The fix is routing generation through verify→retry→
regenerate, firing **only on verification failure and never on IE questions**.
Honest yield: 15 have gold reachable at cap-20, but KG-live taught us
coverage converts weakly — expect low-single-digit eliminations, not 15.
The 25 with rank>20 need hierarchy/definition arms (measured 1.0 but n=1-2:
thin, validate before trusting) or fall into CORPUS on audit.

**GENERATION → the same loop, different trigger.** Context already covers the
reference (headroom >0.10); the model dropped specific concepts (Q031's six
bovine terms, Q032's licence terms, Q014's graded-fine scale). These are what
verify→retry exists for. No offline yield number exists — that is what Batch B
measures (11 calls).

**CORPUS → no pipeline treatment.** Gold-labelling audit first (which
provisions do these 10 references actually draw on? 0 LLM, mirror+Qdrant),
then a procurement decision in the s52/s63 mould. Pipeline work here would
spend budget moving text that is not the reference's text.

**GATE_EDGE → leave the 8; treat the 4.** Q080/Q089: authority grounding
(retry targeting authority provisions, or citation requirement — 25 failures
miss authority repo-wide). Q124: corpus-presence check, then generate. Q123:
nothing (base resolves).

**MIXED → read individually after the loop lands.** Q049/Q085 are already
retry-recoverable offline; the rest re-segment once post-retry windows exist.

## 4. Why the veto set survives (mechanism, not hope)

1. **Conditional fire.** `targeted_retry_node` returns `skipped: no_failures`
   when verification passes — passing QIDs never enter the retry path. The
   veto set (Q015 Q024 Q047; Q002 Q081 Q093 Q095; Q052; 11+1 IE credits;
   Q060 Q119 Q128) is structurally unreachable by the change.
2. **Abstention-safe.** IE questions skip retry entirely; the 11 control
   credits (23% of control binary) cannot be talked out of abstaining.
3. **Additive UNION, cap 20.** Offline 0 regressed in 150 — but live
   re-generation can still shift answers, so every validation batch re-runs
   the veto set and aborts on any flip.
4. **Scoped arm stays off.** Three independent harms documented; nothing in
   this plan re-enables it.

## 5. Validation plan (LLM, ~45-50 of ~867 remaining)
- **Batch A — retry conversion (34 calls):** the 15 retry-reachable QIDs × 1
  retry-pass call each (baselines cached) + 19-QID veto guard
  (4 failure + 4 control substantive + 11 IE credits) × 1.
- **Batch B — generation loop (11 calls):** the 11 GENERATION QIDs × 1,
  sharing Batch A's guard if the code path is one change (it is), else +19.
- **Go/no-go:** Batch A needs ≥3 eliminations with 0 veto flips to justify
  keeping the loop; Batch B needs ≥2. Anything less and the loop is correct
  machinery with no live yield — document and stop.
- **Explicitly not in plan:** full 89×2 sweep (~178 calls to tighten a CI
  around small effects), KG structure work (live ~0), concision prompt
  (3/82), metric change (invalidates all priors), re-enabling the scoped arm.

## 6. Design correction caught by the veto guard (2026-10-12, live run)
The first version of the retry harness (`retry_second_pass.py`) mirrored
Stage-1's offline baseline exactly: top-10 C_hybrid. The veto guard caught
that this is **not faithful to the live pipeline** — ContextBuilder packs up
to 20 chunks, and gold at ranks 11-20 (Q015 rank 19, Q052 loose-window) falls
out of a top-10 base. Veto re-runs failed Q015 and Q052 that pass on wider
windows. 7 calls discarded; harness corrected to base top-20 with RRF fusion
(`rrf_fuse_chunks`, the same production fusion the KG path uses) so matches
compete by merit instead of truncating the window. Lesson recorded because it
generalises: an offline proxy's exact construction is not automatically the
right live baseline — the veto set exists to catch exactly this, and it did.

## 7. Production bug found en route: startup bootstrap flaps (P1, unblocks experiment)

2026-10-12: `create_app()` fails non-deterministically against a healthy
`instance/app.db` (63 tables, `integrity_check` ok) with three different errors
across runs: `no such table: user` (seed), `NoSuchTableError: adjudications`
(verify), `table role already exists` (create_all fallback). A stale
`app.db-journal` is present, suggesting an unclean shutdown (powercut).
The experiment is insulated via the factory's own test seam
(`create_app(db_uri=<scratch>)`; the harness is DB-free), but a service that
cannot reliably boot is production-down until fixed. Repro:
`python repro_app.py` / `which_db.py` / `check_db.py` (repo root, scratch —
delete before commit). Fix belongs in `app/db_bootstrap.py` (make the
check-then-act atomic under one connection, or order create_all before verify
unconditionally), not in any harness.

Update: a fourth mode appeared — `table user already exists` on a FRESH
scratch database that cannot contain anything. That rules out stale-DB
theories: the bootstrap executes creation more than once (or against two
engines), i.e. the check-then-act itself races. Repro rate is roughly 1 in 4-5
boots; harnesses mitigate with the scratch-DB seam plus re-launch.

## 8. Interim veto-guard readings (live, top-20 windows)
Q015 bin=True held (elim flickers False on one run — concepts_missing varies
run to run at temp 0.1; the *substantive* pass is stable, the elimination flag
is not, which bounds how literally to read single-run elim flips including
Q016's). Q047/Q002 binary held. Q060 (IE) abstains correctly. Q052
elim=True again — fragile gain now holding 2/2 on current retrieval.

## 9. Verdict: retry loop fails go-criteria — document and stop (2026-10-12)

`retry_second_pass.py`, 19 clean pairs + 3 trigger-skips, veto guard clean:

| | base (live top-20) | retry (RRF-fused) |
|---|---|---|
| soft mean | 0.362 | 0.367 (**+0.005**) |
| binary | 0/19 | 0/19 |
| eliminated | 2/19 | 1/19 (Q085 lost, none gained) |

Go-criteria were ≥3 eliminations with 0 veto flips. Result: net **−1**.
Per §5, the loop is correct machinery with no live yield — documented, stopped.
Batch B needed no separate run: 10 of its 11 GENERATION QIDs ran inside this
batch (Q058 skipped by trigger design).

Why Stage-1's +0.147 did not convert, in order of evidence:

1. **Stage-1 measured retrieval, not correctness.** Gold-in-window recovery is
   necessary but not sufficient — the KG live test showed the same gap
   (coverage +0.042 → correctness −0.008). Two independent confirmations that
   recovered context converts weakly.
2. **The floor moved.** Fresh top-20 baselines already eliminate 2/19 (Q054,
   Q085) — remediation plus the wider window compressed the headroom retry was
   meant to fill. The cached segmentation understated current retrieval twice
   now (Q052, then these).
3. **Retry can harm.** Q085 elim True→False: RRF fusion displaced a working
   context. Same harm shape as KG Q014 (−0.157). Neither fusion is free.
4. **3/24 never reach the LLM.** `definition_search` plans resolve to
   arm=none (Q007, Q058) and Q025 matched nothing — Stage-1's definition
   recoveries (n=2) do not transfer live. The planner arm with the thinnest
   offline evidence is the one that no-ops live.

Positive soft delta on ~half the pairs (largest Q053 +0.094, Q040 +0.076)
with mean ≈ 0: directionless variance, not a lever. The veto guard held
everywhere (0 flips; Q015's elim flicker with binary held bounds single-run
elim noise, Q016's flip included).

**Spend:** retry harness ~62 calls (7 discarded top-10 + 7 smoke + 43 batch +
5 requeue) + KG 33 ≈ **95/900**. Remaining ~805 held. The 25 rank>20 RETRIEVAL
QIDs and the CORPUS 10 were never going to be fixed by this loop; they go back
to hierarchy/definition validation (thin evidence) and the gold-labelling
audit (0 LLM) respectively. No further LLM spend proposed until the audit
reports — it bounds everything else.

## 10. Next level (2026-10-12, +4 LLM calls): production-faithful retry ctx

The 3 trigger-skips were re-examined instead of accepted. `_mine_defined_term`
reads `ctx["chunks"]` + `ctx["answer"]`; the harness passed only `top_k` while
production (`linear.py`) passes both. After the fix the skips split
informatively: **Q058's noop was harness artifact** (real definition plan,
retry fires, +0.059, no flip), **Q007's noop is genuine** (no defined term even
with full context — a true planner abstention). Lesson: a skip needs the same
autopsy as a flip; "the planner declined" and "the harness starved the planner"
look identical in the log and mean opposite things. Total session spend ≈
102/900.

## 6. What this rules out, restated for the record

More recall, more graph, shorter answers, and a kinder gate all miss at least
one segment's bottleneck while risking the veto set. The only treatment with a
measured offline yield, zero measured regressions, and a structural no-harm
mechanism is the conditional retry loop — which is also the only treatment
the measured pipeline has never actually run.
