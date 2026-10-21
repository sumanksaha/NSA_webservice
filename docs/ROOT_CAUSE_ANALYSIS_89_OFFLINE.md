# Offline analysis (no LLM calls) - 89 failures: root causes

Generated: 2026-10-16T06:23:00Z
Working dir: C:\github\NSA_webservice

## 1) Failure attribution split (with/without Jev flips)
Source: evaluation/out/ceiling_v5/reeval_89/remediation_attribution.json + gen_C_hybrid.jsonl

**Flips under C_hybrid (relative to pre-remediation):**
- True fixes (became correct under post): 11 (Q010, Q014, Q015, Q016, Q017, Q049, Q061, Q064, Q126, Q133, Q138)
- Regressions/lost: 7 (Q007, Q040, Q082, Q110, Q141, Q146, Q149)

**Bucketed by inferred cause (heuristic: cm>=2+not elim/abst -> retrieval; elim/abst -> generation/gate; cm<=1 -> scoring/decision):**

| Flip type | retrieval | generation/gate | scoring/decision | total |
|---|---|---|---|---|
| true_fix | 4 (Q010,Q014,Q017,Q064) | 3 (Q016,Q133,Q138) | 4 (Q015,Q049,Q061,Q126) | 11 |
| lost | 1 (Q110) | 2 (Q141,Q149) | 4 (Q007,Q040,Q082,Q146) | 7 |

**Classification summary:**
- True fixes are split across buckets (retrieval ~36%, scoring/decision ~36%, generation/gate ~27%) — Jev flips helped boundary cases as well as some retrieval/evidence cases, but not predominantly one bucket.
- Regressions skew to scoring/decision + generation/gate; only 1 clear retrieval regression. Suggests Jev tuning created boundary tradeoffs (precision/abstention) more than wholesale retrieval damage.

**Verdict (item 1):** With/without Jev flips, impact is mixed bucket-wise. True fixes show modest retrieval contribution (4/11). Majority of flips are decision/boundary or gate-related — consistent with Jev acting as decision layer, not fixing missing evidence alone.

## 2) Gold-in-prompt isolation (89 cohort)
Source: evaluation/out/ceiling_v5/rerun_gip/cond_v3_summary.json

- Targets (n_targets): 60
- Fixed under gold-in-prompt (cond_v3): 5 (n_fixed=5, n_remaining=55)
- By audit action:
  - provision_check (n=20, fixed=1)
  - needs_deeper_reasoning (n=16, fixed=1)
  - abstention_gate (n=24, fixed=3)

**Verdict (item 2):** Gold-in-prompt fixes only **5/60 (8.3%)** of targeted cases. This strongly indicates the bottleneck is **not generation/conditioning** for most of these 89 — it’s **retrieval/evidence selection/coverage** (concepts/spans not present in retrieved evidence) or possibly adjudication strictness. Small generation slice exists (abstention_gate had 3 fixes).

## 3) Oracle vs actual evidence
Source: evaluation/out/ceiling_v5/reeval_89/oracle_window_repack_first.json (C_hybrid_section)

**Oracle window repack (first-window/oracle evidence set vs actual):**
- Failure set (n~87): 
  - failure:base_gold 56, failure:dedup_gold 56
  - failure:RECOVERED_GOLD 6, failure:LOST_GOLD 6 (net neutral on gold presence count)
- Control (n~41): RECOVERED_GOLD 2, LOST_GOLD 1
- Gains (recovered/benefited under oracle window): Q096, Q116, Q117, Q121, Q125, Q127, Q001, Q027 (8 questions total across failure+control as listed)
- Losses under oracle window: Q047, Q052, Q055, Q084, Q088, Q136, Q056 (7)

**Verdict (item 3):** Oracle evidence set recovers gold coverage for only **~6 failure cases** (RECOVERED_GOLD=6) while also losing gold for **~6** (LOST_GOLD=6). The number of 89 that become correct purely by having oracle evidence is small in these measured stats (net ~0 change in gold counts; gains/losses mixed). This suggests **evidence set selection/windowing improvements alone will help only a narrow slice** of the remaining 89 under current oracle construction — or that oracle definition/window differs from what generation can actually use. Combined with (2), the ceiling from "perfect evidence" here appears modest; many failures persist even when gold is present or aren't recovered by oracle packing.

## Summary (root causes)
1. **Failure attribution (Jev flips):** Mixed buckets — Jev mainly shifts decision boundaries (scoring/decision + gate) with small retrieval wins (4 true fixes). Regressions concentrated in boundary cases.
2. **Gold-in-prompt:** Only 5/60 fixed (8.3%) -> generation/conditioning is **not** primary bottleneck for most 89.
3. **Oracle vs actual:** Oracle evidence recovers gold for ~6 failure cases, loses ~6; narrow slice. Retrieval/evidence completeness helps some but oracle tests don’t show large universal headroom for this set.

**Conclusion:** The 89 are best explained as a mix: decision-boundary behavior (exacerbated by adjudication strictness), some retrieval/coverage gaps, and a subset tied to gate/abstention. The pipeline direction (especially Jev evaluate->retry, step 5) matches the dominant boundary nature; upstream set-selection (steps 1–3) is plausible but **oracle headroom appears limited** based on these offline artifacts.
