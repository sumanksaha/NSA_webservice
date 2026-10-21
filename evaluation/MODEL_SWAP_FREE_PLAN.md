# Free-model swap plan — laguna-s-2.1:free retirement (2026-10-31)

**Status:** executed — `minimax/minimax-m3.1-flash:free` is the default answer model since 2026-10-19 · **Created:** 2026-10-18
**Machine-readable companion:** [`model_swap_free_samples.json`](./model_swap_free_samples.json) (seed `20261018`, exact QID lists)

## 1. Why

`openrouter/poolside laguna-s-2.1:free` retires **2026-10-31**. The OpenRouter
`/api/v1/models` response carries `expiration_date: 2026-10-31` on **all four**
poolside listings (`laguna-s-2.1`, `laguna-s-2.1:free`, `laguna-xs-2.1`,
`laguna-xs-2.1:free`), so falling back to a paid Laguna is not an option.
benchable.ai already shows `laguna-m.1` / `laguna-xs.2` as "Unavailable" — this
looks like a Poolside-wide OpenRouter retirement.

Token footprint per question — **measured 2026-10-19** on the harness path
(3 calls reading provider-reported usage: prompt 2,269 / 5,227 / 7,808, avg
**5,101 in**; completion **1,024 on every call** — minimax is hitting the
`max_tokens` cap; method in §8). Production runs 2 calls/question (generation
+ hallucination detector); this eval harness runs **1 call/QID**.
On paid models that is ≈ $0.0005–0.002/call — cost is not decisive;
**reliability and gate-fit are**.

## 2. Candidates under test (stage 1 = stage 2 = same three)

| Slug | AA intelligence index | Notes |
|---|---|---|
| `nvidia/nemotron-3.5-super-vl:free` | 26.5 | Highest-AA free candidate; recommended free pick |
| `thinkingmachines/inkling-small:free` | *not yet published* | User-selected; record observed index during run |
| `google/gemma-4-26b-a4b-it:free` | 16.7 | 26B MoE / 3.8B active, reasoning **off** by default, 262K ctx (chosen over `gemma-4-31b-it:free` ii 14.7) |

Not selected, kept for reference: `deepseek/deepseek-v4-flash-0731` (paid
runner-up), `google/gemma-4-31b-it:free`.
Excluded: `anthropic/claude-haiku-5.5` (repo already measured coverage
collapse + 5/13 empty 200s — `CURRENT_STAGE_ASSESSMENT.md` §10) and anything
with near-term expiration (`dots-3-note-preview` 12/31, `gemini-2.5-*` 10/20,
`mimo-v2.5*` 10/21).

Free-tier constraints: **20 RPM, 50 req/day (1,000/day after $10 lifetime
credits)**, account-wide across all `:free` slugs. Plan total ≈ 124 requests
⇒ needs the $10-funded tier to fit in one day; `MAX_LLM_CALLS=700` in `.env`
is per-process and ample.

## 3. Two-stage sample design

Paired against cached laguna baselines: for each QID the **only** variable is
the model (laguna side is reused from cache). Hard constraint discovered
2026-10-18: **only 36 of 150 QIDs carry a cached laguna baseline**
(34 failure / 2 control of the 89/61 benchmark split).

### Stage 1 — small sample, N = 10 (smoke / gate)

Sentinel-heavy, all from the canonical 13-QID discriminating set:

```
Q052 Q015 Q047 Q002   must-hold passes (losing any vetoes the swap)
Q060                  IE abstention guard (control; chattier models lose this credit)
Q084 Q023 Q123        near-miss + elimination flips
Q007 Q020             plain near-misses (1–2 concepts short)
```

Canonical-13 members `Q025 Q016 Q014` are deferred to stage 2 (all 13 are in
the medium sample).

### Stage 2 — medium sample, N = 40 (analysis)

The 36 baseline-available QIDs (all canonical 13 + the 18-QID near-miss band +
remainder) **plus 4 seeded control-cohort laguna fills** ⇒ 34 failure /
6 control.

**Laguna fills — run these BEFORE 2026-10-31** (14 IE control questions were
eligible; 4 chosen with seed `20261018`):

```
Q037 Q108 Q137 Q138
```

```powershell
python -m evaluation.ab_model --models poolside/laguna-s-2.1:free `
  --qids Q037,Q108,Q137,Q138 --max-calls 4 --delay 5
```

Writes `cond=laguna-s-2.1` rows into `evaluation/out/ceiling_v5/reeval_89/model_ab.jsonl`
(3rd-priority baseline source, same mechanism used for Q084/Q023/Q123).

## 4. Run order (from repo root)

```powershell
# 0. fill the 4 laguna baselines (once, before 10/31)  — see command above

# 1. stage 1: 10 QIDs x 3 models = 30 calls
python -m evaluation.ab_model --qids Q052,Q015,Q047,Q002,Q060,Q084,Q023,Q123,Q007,Q020 `
  --models nvidia/nemotron-3.5-super-vl:free,thinkingmachines/inkling-small:free,google/gemma-4-26b-a4b-it:free `
  --max-calls 30 --delay 5

# 2. stage 2: pass the FULL 40 — already-cached (qid, cond) pairs are skipped,
#    so stage-1 rows are not re-billed; new spend = 30 QIDs x 3 models = 90 calls
python -m evaluation.ab_model --qids <full-40 list from model_swap_free_samples.json> `
  --models nvidia/nemotron-3.5-super-vl:free,thinkingmachines/inkling-small:free,google/gemma-4-26b-a4b-it:free `
  --max-calls 120 --delay 5

# 3. any empty-200 answers (haiku failure mode) — requeue, do not silently keep blanks
python -m evaluation.ab_model --requeue-errors <errors-file> --models <same three>
```

Prerequisites: `.env` has `LLM_ENABLED=true`, `MAX_LLM_CALLS=700`, and
`OPENROUTER_API_KEY` set. Results land in
`evaluation/out/ceiling_v5/reeval_89/model_ab.jsonl`, keyed by `(qid, cond)`;
`cond` = slug's last path segment without `:free` (so
`nemotron-3.5-super-vl`, `inkling-small`, `gemma-4-26b-a4b-it` — none collide
with existing `apodex-1.1-mini` / `nemotron-3.5-lightning` / `claude-haiku-5.5`
rows).

### Allow-list prerequisite (applied 2026-10-18)

`SSLBypassLLMClient` inherits `GroundedLLMClient`, whose construction validates
against `ALLOWED_MODELS` — and construction sits **outside** the per-question
`try`, so an unlisted slug crashes the run before it writes any record. The
three candidate slugs were added to `ALLOWED_MODELS`
(`app/shared/llm_client.py`) on 2026-10-18, laguna retained. Revert with:

```powershell
git checkout -- app/shared/llm_client.py
```

Existing tests are unaffected: the disallow-list tests
(`tests/test_llm_kill_switch.py`, `tests/test_rag_generation.py`) parametrize
on `deepseek/deepseek-v4.1-flash`, `openai/gpt-4o-mini`, `typesafe/jev-router`,
`meta-llama/llama-3.3-70b-instruct:free` — none of them the new slugs — and
`test_model_default_is_laguna` still passes (default unchanged).

## 5. Decision criteria

`python -m evaluation.ab_model` prints per-model paired stats. Judged as:

1. **Primary** — paired soft mean delta vs cached laguna
   (`soft = (jaccard + coverage) / 2`).
2. **Veto** — losing any must-hold pass (`Q052 Q015 Q047 Q002`) **or** the
   `Q060` abstention credit kills a swap regardless of its mean.
3. **Structural checks**
   - answer-length blowup = chain-of-thought leaking into the answer body
     (the jaccard gate punishes verbosity ~6x; known gate-hostile mode);
   - empty 200 responses → `--requeue-errors`, never accept blanks;
   - elimination flips on `Q084 Q023 Q123`.
4. Prior attempts and their failure modes: `CURRENT_STAGE_ASSESSMENT.md` §10
   (apodex-mini 0.424/3, nemotron-lightning 0.400/1, claude-haiku-5.5 coverage
   collapse; laguna reference 0.457 soft / 4 binary).

Free-tier day budget: stage 1 = 30 requests, stage 2 = 90, fills = 4,
retries on top ⇒ ≈124 total. If running on the unfunded 50/day tier, split
across three days.

## 6. Swap executed (2026-10-19)

**All items below were applied on 2026-10-19** when minimax became the
default. Two intentional non-changes: `LAGUNA` in `evaluation/ab_model.py:48`
still names the **eval baseline** (not the default — paired reports must keep
comparing against cached laguna rows), and laguna itself stays in
`ALLOWED_MODELS` until it retires 2026-10-31.

Applied: `DEFAULT_ANSWER_MODEL` (`app/shared/llm_client.py`),
`DEFAULT_MODEL` (`app/rag/generation/llm_client.py:79`), `TokenCounter`
default (`app/rag/verification/token_counter.py:73`), hardcoded OpenRouter
default in `app/ai_assistant/service.py:320`, `.env` + `.env.example`
(`RAG_LLM_MODEL`), `scripts/live_llm2.py` / `live_llm3.py` fallbacks,
`test_model_default_is_laguna` → `test_model_default_is_minimax`, the stub
assertion, and the no-fallback model assertions in
`tests/test_llm_kill_switch.py`. Verification: 260 tests passed across the
six affected files; runtime smoke check shows `DEFAULT_ANSWER_MODEL`,
`GroundedLLMClient().model`, `TokenCounter().model`,
`RAG_LLM_MODEL`, and `AIAssistantService._get_model()` all resolving to
`minimax/minimax-m3.1-flash:free`, with unknown models still rejected.

To revert: `git checkout -- app/ tests/ .env.example scripts/` (plus the
`RAG_LLM_MODEL` line in `.env`).

## 7. Run log

### 2026-10-18 — fills + stage 1 (all free)

* **Laguna fills 4/4 OK** (`Q037 Q108 Q137 Q138`; Q137/Q138 are elim=True) —
  the pre-10/31 baseline obligation is discharged.
* **`nvidia/nemotron-3.5-super-vl:free` — 9/10 OK** (Q084 first-attempt 502,
  requeued same day). Paired n=9 vs cached laguna: **soft 0.491 → 0.472
  (delta −0.019)**; binary 4→3; elim 5→3; answer len 2914→4102 chars (+41%).
  Best mean of any candidate tested to date (apodex −0.033, lightning −0.058,
  haiku −0.251) — **but it loses the Q060 abstention credit
  (bin True→False)**, which is an explicit veto criterion, plus elim on
  Q052/Q002/Q060, while gaining elim+bin on Q015. Verdict pending the
  medium sample; the Q060 loss is the item to watch.
* **`thinkingmachines/inkling-small:free` — DISQUALIFIED (10/10 HTTP 403).**
  OpenRouter gates this slug to agentic harnesses:
  `"is only available on agentic harnesses"` /
  `failed_routing_step: "Gate Free Endpoints by Agentic Harness"`. Not a
  transient error and not fixable from a server-side API client. The paid
  slug `thinkingmachines/inkling-small` (non-`:free`) is untested.
* **`google/gemma-4-26b-a4b-it:free` — 10/10 HTTP 429**, upstream provider
  (Google AI Studio) shared-pool rate limit
  (`limit_source: upstream_provider_shared_pool`) — retryable, not our
  account quota. The same-day stage-1 requeue was cancelled by a server
  restart before it produced results (see 10-19 below).
* Account usage: ~34 free-tier requests before nemotron stage 2; no
  account-level 429s observed (nemotron calls succeeded throughout).
* **Stage 2 nemotron: DONE same day — 31 calls** (Q084 requeued after its
  502 → recovered; Q110 502'd twice, left for a later requeue). Paired
  **n=39: soft 0.387 → 0.384 (delta −0.0025 — parity)**; binary 6→5;
  elim 11→6; answer len 3199→4161 (+30%).
  - Binary must-holds: **Q015 ✓, Q047 ✓, Q002 ✓** (all True→True; Q015
    even gains elim); Q052 stays bin 0→0 (elim lost, not a binary loss).
  - **Veto triggered: Q060 abstention credit lost (True→False)** — and
    *every* alternative tested loses Q060 (apodex/lightning/haiku too),
    which suggests the credit is laguna-specific rather than a
    model-quality signal. Revisit the veto's weight before swapping.
  - elim: holds Q138; loses Q052/Q002/Q060/Q054/Q084/Q085; gains Q015
    (net 11→6 — laguna is still distinctly better at triggering
    elimination).
  - Soft-swing tails: biggest gains Q031 +0.154, Q016 +0.126, Q014 +0.100,
    Q040 +0.087; biggest losses Q032 −0.105, Q075 −0.082, Q049 −0.066,
    Q123 −0.064. No CoT-leak/length blowup (4.2k vs 3.2k chars; in line
    with apodex 3.8k / lightning 4.0k).
  - Reliability: 2/32 nemotron calls 502'd (~6%); zero 429s, zero empty
    answers. First-attempt success 94%.
  - **Provisional verdict: best free candidate by a wide margin on the
    mean (−0.0025 vs −0.033/−0.058/−0.251), fails only the Q060 veto and
    trails on eliminations.** Decision needs the gemma/inkling slots
    resolved and a Q060-veto policy call.

### 2026-10-19 — minimax-m3.1-flash: new candidate, stage 1

* New slug **`minimax/minimax-m3.1-flash:free`** (1M ctx, no expiration,
  provider Minimax, `reasoning: null`, cost 0) added to `ALLOWED_MODELS`.
  Probe: HTTP 200 — no agentic-harness gate (inkling) and no upstream 429
  (gemma).
* **Stage 1: 10/10 OK, zero errors.** Paired vs cached laguna:
  **soft 0.484 → 0.458 (delta −0.0261)**; **binary 4→4 (all must-holds
  held)**; **Q060 abstention credit HELD (bin True→True, elim True→True) —
  first candidate ever to pass this**; elim 6→5 (lost Q052/Q084/Q023,
  gained Q123/Q007); answer len **2989→4973 chars (+66%) — the chattiest
  candidate yet, which is the main drag on its jaccard-sensitive mean**.
* **Stage 2: DONE same day — 30/30 OK (40/40 total, zero errors).** Paired
  **n=40: soft 0.384 → 0.395 (delta +0.0111 — BEATS laguna)**;
  **binary 6→6**; **elim 11→11**; answer len 3179→4995 (+57%);
  latency avg 12.6s / median 11.9s / p90 16.8s.
  - **Every veto check passes**: Q015/Q047/Q002 binary held, **Q060
    abstention credit held (bin and elim True→True)**, both IE fills
    (Q137/Q138) held.
  - elim composition: holds Q054/Q079/Q085/Q137/Q138; loses
    Q052/Q084/Q023; gains Q007/Q123/Q149 → exact net parity.
  - Soft-swing tails: gains Q031 +0.166, Q058 +0.129, Q053 +0.120,
    Q040 +0.097; losses Q075 −0.083, Q025 −0.079, Q052 −0.070,
    Q060 −0.064.
  - Reliability: 40/40 first-attempt success — zero 403/429/502, zero
    empty answers.

### Candidate scoreboard (paired vs cached laguna, same pools)

| candidate | n | Δ soft | binary | elim | len | veto | reliability |
|---|---|---|---|---|---|---|---|
| **minimax-m3.1-flash:free** | 40 | **+0.011** | 6→6 ✓ | 11→11 ✓ | +57% | **PASS** | 40/40 |
| glm-5.3-flash (paid) | 40 | +0.009 | 6→6 ✓ | 11→9 | +19% | **PASS** | 40/40 · $0.031 |
| nemotron-3.5-super-vl:free | 39 | −0.003 | 6→5 ✗ Q060 | 11→6 | +30% | FAIL | 30/32 (2×502) |
| apodex-1.1-mini:free | 13 | −0.033 | 4→3 ✗ | 6→4 | +22% | FAIL | 13/13 |
| nemotron-3.5-lightning:free | 13 | −0.058 | 4→1 ✗ | 6→3 | +29% | FAIL | 13/13 |
| claude-haiku-5.5 | 9–13 | −0.150…−0.251 | collapse | 6→0 | −85% | FAIL | empty 200s |
| inkling-small:free | — | — | — | — | — | — | 403 agentic gate — disqualified |
| gemma-4-26b-a4b-it:free | — | pending — upstream 429 (Google AI Studio), requeue owed | | | | | |

* **Provisional pick: `minimax/minimax-m3.1-flash:free`** — the only
  candidate to beat laguna on the paired mean, with full binary + elim
  parity and no veto triggered. Watch items before the 10/31 cutover:
  +57% answer length (production runs this twice per question — token/UX
  cost) and single-run-per-QID variance — a confirm run is cheap insurance.
* **Swap landed same day (2026-10-19):** minimax is now the default answer
  model everywhere (see §6 for the full applied list and revert command).
  260 tests passed across the six affected files; runtime smoke check
  confirms all default-model surfaces resolve to
  `minimax/minimax-m3.1-flash:free`.
* Still pending from 10-18: gemma stage-1 requeue (cancelled by server
  restart) and nemotron Q110 (persistent 502).
* **Paid candidate added 2026-10-19 — `z-ai/glm-5.3-flash`** (first paid
  model tested, cost gated on user approval): 1-call probe then the full
  N=40 → **40/40 zero errors, paired soft 0.3838 → 0.3923 (Δ +0.0085), all
  vetoes held, len +19%, $0.031 total**. Full cost model + analysis in §8.
* **Frontier paid candidates — costed, probed, declined 2026-10-19**
  (`anthropic/claude-sonnet-5.5`, `openai/gpt-6.1-sol`): both $2/$10 per
  Mtok → ~$1.96 for a paired N=40. 1-call probes: both returned 200 but
  **truncated at the repo's `max_tokens=1024`** (reasoning is on by default
  and shares the cap — Sonnet spent 791/1,024 tokens thinking), and Sonnet's
  tokenizer counts the same prompt ~50% higher (7,834 vs 5,154 tokens).
  Full run **not executed** — it would measure the cap, not the models, at
  ~30× the GLM cost. Slugs removed from `ALLOWED_MODELS`. Details in §9.

## 8. Paid candidate: `z-ai/glm-5.3-flash` (2026-10-19)

First paid candidate tested. No `:free` GLM 5.3 variant exists; the run was
costed first and approved by the user before spending.

### Cost model (measured, not config-derived)

Basis: 3 real harness-path calls (free minimax) reading provider-reported
usage → prompt **2,269 / 5,227 / 7,808** (avg **5,101**), completion
**1,024 on every call** (the `max_tokens` cap is being hit).

| tier | $/Mtok in | $/Mtok out | est. 40 calls |
|---|---|---|---|
| **`z-ai/glm-5.3-flash`** | 0.048 | 0.16 | **$0.016** |
| `z-ai/glm-4.7-flash` | 0.0605 | 0.40 | $0.029 |
| `z-ai/glm-5.3` | 0.079 | 1.40 | $0.073 |
| `z-ai/glm-5.3-flashx` | 0.37 | 1.25 | $0.13 |
| `z-ai/glm-5.3-prime` (flagship) | 2.80 | 8.80 | $0.93 |

Routing caveat: the default OpenRouter route picked **Wafer** ($0.074/$0.20
per Mtok) over the cheapest listing (OpenInference $0.048/$0.16); the flash
slug is served by 30+ providers spanning $0.048–0.225 in / $0.16–0.75 out.
Budget ~1.2–2× the headline price.

Account at decision time: $10.00 credits, **$8.32 remaining**; key guardrail
**$3.00/day** (spend resets daily) — a ~$0.03 run is not a constraint.

### Probe (1 call, Q052, must-hold sentinel)

HTTP 200 in 4.9s, provider Wafer, `finish_reason: stop`, content 1,877 chars,
CoT in a **separate** `reasoning` field (43 tokens) → no CoT leak into the
scored text, completion 501 tokens (cap not hit), billed **$0.000487**.
Probe grade on Q052: soft 0.466 vs laguna **0.4865** (−0.021).
*(Corrected 2026-10-19: the figure first recorded here, 0.321, is the
`gen_C_hybrid.jsonl` C_hybrid-pipeline score for Q052, not a laguna record.
The paired harness baseline for Q052 is `retry_live.jsonl` cond `veto`:
soft 0.4865, binary 0, elim True, authority_hit True, 5/5 concepts. The
manifest's whole `per_qid` block was built from the wrong file and has been
rebuilt from `ab_model.laguna_base()`; A/B deltas were never affected.)*

### Result — full N=40 paired run

```powershell
python -m evaluation.ab_model --qids <medium-40 from the manifest> `
  --models z-ai/glm-5.3-flash --max-calls 45 --delay 3
```

**40/40, zero errors, zero empty answers.** Paired vs cached laguna:

* **soft 0.3838 → 0.3923 (Δ +0.0085)** — beats laguna, but the **median Δ is
  −0.0087**: the mean is carried by a handful of large gains (variance, not a
  uniform shift)
* binary **6 → 6**; elim **11 → 9** (loses Q023/Q052/Q084, gains Q123)
* answer len 3179 → **3793 chars (+19%)** — best length discipline of any
  candidate (minimax +57%, nemotron +30%)
* latency avg 10.4s / median 8.8s / max 37.0s
* **every veto passes**: Q015/Q047/Q002 binaries held, Q060 abstention credit
  held (credit 1→1, bin and elim True→True), Q137/Q138 fills held
* Soft-swing tails: gains Q031 +0.149, Q040 +0.137, Q016 +0.110, Q055 +0.072,
  Q076 +0.069; losses Q025 −0.072, Q060 −0.051, Q127 −0.045, Q037 −0.044,
  Q014 −0.039

**Actual cost: $0.0306** for the probe + 40 calls, measured as the
`/api/v1/credits` `total_usage` delta (1.684274 → 1.714881) ≈
**$0.00075/call** — ~1.2× the headline estimate (longer answers than the
probe + provider mix).

### Verdict

**Best paid candidate, and the closest runner-up overall**: beats laguna on
the paired mean with every veto held, at **~$0.00075/call** (~$0.0015/question
in production's 2-call flow; ≈ $0.23 per 150-question benchmark). It does not
displace minimax — which scores higher (+0.0111), keeps full elim parity and
costs **$0** — but it is the obvious contingency if minimax is ever
rate-limited or retired, and on paper it is the most intelligent slug tested
(AA ii ≈41.8 per the earlier candidate scan; OpenRouter does not expose the
index, not re-verified here).

Caveats: single run per QID (no repeats), same as every other candidate; and
the positive mean sits on a negative median, so a confirm run would be
worthwhile before relying on the GLM advantage.

`z-ai/glm-5.3-flash` was added to `ALLOWED_MODELS` for the run (marked as the
paid eval candidate) — remove it if the allow-list should stay free-only.

## 9. Frontier paid candidates: `claude-sonnet-5.5` + `gpt-6.1-sol` (2026-10-19)

Cost-first, per user instruction; 1 call each probed **before** any full run.

### Pricing (identical headline, different real cost)

| slug | in $/M | out $/M | cache-read | ctx | moderated |
|---|---|---|---|---|---|
| `anthropic/claude-sonnet-5.5` | 2.00 | 10.00 | 0.10 | 1M | no |
| `openai/gpt-6.1-sol` | 2.00 | 10.00 | 0.10 | 1.05M | yes |

No `:free` or cheap-route variant. `gpt-6.1-sol-pro` is the same model with
`reasoning.mode: pro` ("spends far more"); `gpt-6.1-sol` also doubles to
$4/$15 once a prompt exceeds **272k tokens** (not reachable here —
`RAG_CONTEXT_MAX_CHARS=24000` ≈ 6k tokens). The `:batch` variants ($1/$5) are
async-batch only and unusable by this harness.

Estimate at the measured token basis (5,101 in / 1,024 out per call):
**~$0.82 each, ~$1.64 both**.

### Probe — 1 call each, Q052, harness body (temp 0.1, max_tokens 1024, no reasoning param)

| | `claude-sonnet-5.5` | `gpt-6.1-sol` |
|---|---|---|
| HTTP / runtime | 200, 11.1s | 200, 20.7s |
| routed provider | Claude Platform on AWS | OpenAI |
| prompt tokens | **7,834** | 5,154 |
| completion tokens | 1,024 (**at cap**) | 1,024 (**at cap**) |
| …of which reasoning | **791 (77%)** | 191 |
| finish | `length` / `max_tokens` | `length` / `max_output_tokens` |
| content | 768 chars, **cut mid-sentence** | 3,562 chars, **cut mid-sentence** |
| CoT leak into content | no (separate `reasoning` field) | no |
| billed cost | **$0.025908** | **$0.023124** |
| Q052 grade (laguna baseline 0.4865) | 0.413 — **worse than laguna**; 2 concepts never reached | **0.501 · bin 1 · elim True — the only model tested to beat laguna here** |

Findings:

* **Both truncate at the repo's `max_tokens=1024`**: both reason by default
  with no reasoning param, and the cap covers reasoning + answer. Claude spent
  **791 of 1,024 tokens** thinking.
* **Claude's tokenizer counts the identical prompt at 7,834 tokens vs 5,154
  (GPT-6.1) and 5,221 (GLM)** — ~50% more input tokens, so Sonnet is the more
  expensive of the two despite identical headline pricing.
* GPT-6.1 wrote the full prompt to its cache (`cache_write_tokens=5151`); at one
  call per QID in this eval that write is mostly unrecoverable overhead.
* Neither hit a gate: both returned content; GPT is `is_moderated: true` but
  passed.
* Revised full-run cost from measured per-call billing: **~$1.04 (Sonnet) /
  ~$0.92 (GPT-6.1 Sol) / ~$1.96 both**, ~20% above the pre-probe estimate.
  Probe spend: **$0.049**.
* Both slugs were added to `ALLOWED_MODELS` for the probe series and removed
  again when the run was declined (below).

### Decision (2026-10-19): not run

The user reviewed the probe and declined the full run — **both frontier slugs
are poor fits for the answer path under the current config**, and ~$2 would buy
a predictably handicapped result:

* At `max_tokens=1024` both spend the budget on default-on reasoning and are
  cut mid-answer (Claude: 791/1,024 tokens thinking, 768 chars of answer), so a
  paired run would measure the cap, not the models.
* Sonnet costs **~12% more per call than GPT-6.1** ($0.025908 vs $0.023124)
  despite identical headline pricing, purely through tokenizer inflation on the
  same prompt (7,834 vs 5,154 prompt tokens).
* Production cost would be **~$0.046–0.052/question** (≈$7 per 150-question
  benchmark) against **$0** for the current default (minimax) and $0.23 for
  GLM — for no demonstrated gate advantage. The n=1 probe did favour GPT-6.1
  Sol on Q052 (0.501, bin 1, elim True vs laguna 0.321 / 0 / False), but that
  is one truncated sample.

The two slugs were therefore **removed from `ALLOWED_MODELS`** (re-adding is a
one-line change). Probe spend: **$0.049**.

If a frontier run is ever revisited: raise the output cap for it (`max_tokens`
≥ 4096) and treat it as a **separate, non-comparable arm** — the repo's 1,024
cap is structurally hostile to reasoning-by-default models, and raising it in
production would raise per-question cost for every model.

## 10. Q052 five-way: laguna vs glm vs minimax vs gpt-6.1 vs sonnet (2026-10-19)

Q052 is a stage-1 must-hold sentinel (§3): *"Under the Water (Prevention and
Control of Pollution) Act, 1974, what is the maximum penalty for carrying out a
polluting process in contravention of an order, and is the offence cognizable?"*
Gold: imprisonment ≤ 3 months (5 months on repeat), fine ≥ ₹2,000/day for
continuing offences, and cognizable. `gold_concepts` =
`[penalty, imprisonment, fine, continuing offence, cognizable]`,
`gold_authorities = ["court"]`, `insufficient_evidence = false`.

All five saw the same top-20 `C_hybrid` chunks and the same prompt; the three
candidate rows come from `model_ab.jsonl`, the frontier rows from the §9 probes,
and laguna from `retry_live.jsonl` cond `veto` (the record `ab_model.laguna_base()`
returns, i.e. the actual paired baseline).

| model | source | Δ soft vs laguna | bin | elim | auth hit | concepts | answer len | latency | truncated at 1024 |
|---|---|---|---|---|---|---|---|---|---|
| **laguna-s-2.1** (baseline) | `retry_live:veto` | — | 0 | **True** | ✓ | 5/5 | 2,765 | 13.9s | no |
| **gpt-6.1-sol** (paid) | §9 probe | **+0.014** | **1** | **True** | ✓ | 5/5 | 3,562 | 20.7s | **yes** ($0.023) |
| **glm-5.3-flash** (paid) | cache | −0.021 | 0 | False | ✗ | 5/5 | 1,926 | 4.4s | no |
| **minimax-m3.1-flash** (default) | cache | −0.070 | 0 | False | ✗ | 5/5 | 4,635 | 19.8s | no, but **CoT leaked into the answer body** |
| **claude-sonnet-5.5** (paid) | §9 probe | −0.074 | 0 | False | ✗ | 3/5 | 768 | 11.1s | **yes** ($0.026) |

Ranking on this QID: **gpt-6.1 > laguna ≈ glm > minimax ≈ sonnet**. The default
model is the second-worst of the five here.

### What each answer actually did

* **laguna** — quoted s.45A verbatim in full (3 months / ₹10,000 / both, plus
  ₹5,000/day after first conviction), plus s.46 and the s.43 heading from two
  sources. Correctly refuses to call the offence cognizable: *"the actual text
  of Section 3 [43] is not provided in the context."* Gets every concept keyword
  (including "cognizable" inside the hedge) and mentions "court" in the s.46
  quote → authority hit → elim True.
* **glm-5.3-flash** — same correct penalty, same correct hedge on cognizance,
  **plus a correction laguna misses**: s.45 (enhanced penalty on repeat
  conviction) was omitted by the 2024 amendment, so the escalation no longer
  applies. 34% shorter than laguna and **3× faster (4.4s)**. Substantively the
  strongest answer on substance, but the scorer cannot see it — it never says
  "court", so `authority_hit` is False and elim drops to False. Δ −0.021.
* **minimax-m3.1-flash (the default)** — the right figures are present, but the
  answer body is **raw chain-of-thought**: it opens *"We need answer legal. Need
  parse question. Need go through every numbered source…"* and then spends the
  budget re-deriving the statute from model memory, including an **unretrieved
  guess** ("s.41 … imprisonment up to six months"). It never reached its own
  "Step 2 — answer" before the cap. 5/5 concepts, no "court" → elim False.
  This is precisely the length/CoT-leak failure mode §5 names as gate-hostile,
  and Q052 is where it costs an elim flag.
* **gpt-6.1-sol** — **the only model to score bin=1** (it affirms cognizable)
  and, with laguna, the only one to reach elim True; highest soft of the five
  (+0.014). Achieved on an answer **truncated at the 1024-token cap**, so it is
  a genuine positive signal that never got a closing paragraph.
* **claude-sonnet-5.5** — never left Step 1: **791 of its 1,024 completion
  tokens went to reasoning**, so the scored text is quotations only and two
  concepts (continuing offence, cognizable) were never reached. Lowest soft of
  the five (−0.074) and the clearest single illustration of why the cap
  disqualifies it.

### Scorer artifacts this QID exposes

1. **`authority_hit` on Q052 is a one-word keyword test.** `gold_authorities`
   is `["court"]` and `authority_coverage` fires at ≥50% token overlap, so any
   answer containing the word *court* qualifies. laguna's elim=True is carried
   by a stray mention inside a quote from **s.46 (publication of offenders) —
   a provision irrelevant to a penalty question**; glm's more precise answer
   fails the flag for not mentioning it. Here elim measures a word, not quality.
2. **laguna's own cached Q052 records disagree by more than any candidate
   delta:** `retry_live:veto` 0.4865 bin 0 (used as the paired baseline),
   `kg_live_verify:base` 0.5157 bin 1, `kg` 0.5234 bin 1. Same model, same
   question — a 0.037 spread with the binary flipping. Every Δ in the table
   above is smaller than that spread except sonnet's; single-QID conclusions
   here are inside run-to-run noise, and the five-way is a *quality read*, not
   a selection basis.
3. **The repo's own pipeline scores 0.3209 on Q052** (`gen_C_hybrid.jsonl`,
   3,942 chars, 3 concepts missing) — the same record that was mistaken for
   laguna's baseline (§8). Every model in the table clears it, but by 0.09–0.20.

### One criteria ambiguity to resolve

§5 lists Q052 among the must-hold passes whose loss "kills a swap", and
minimax **lost Q052's elim flag** (laguna True → minimax False), yet §7 recorded
veto PASS on the grounds that the loss was elim, not binary. Either
(a) restate the must-hold as **binary-only** (Q052 is bin 0 on both sides, so
nothing was lost), or (b) record the Q052 elim loss as a **documented veto
waiver** accepted because overall elim was 11→11 parity. The decision should
not rest on an unstated reading of its own criterion.

## 11. Delivery-readiness findings (2026-10-19): the candidates never emit an answer

Found by an offline delivery audit run while the full-150 extension (§12) was in
flight. These findings come before any sign-off decision and are the shortlist of
optimisations the final delivery needs.

### 11.1 Probe: `content` is null and the whole token budget goes to reasoning

One call per model on Q002 through the harness's *exact* path
(`GroundedGenerationService.generate` → `SSLBypassLLMClient` → temp 0.1,
`max_tokens=1024`, no reasoning param), with the raw response body captured:

| model | `message.content` | `message.reasoning` | `reasoning_tokens` | finish | completion tok |
|---|---|---|---|---|---:|
| `poolside/laguna-s-2.1:free` | **str, 1670 chars** | absent | 0 | **stop** | 407 |
| `minimax/minimax-m3.1-flash:free` (3 probes) | **None** | 4935–5178 chars | **1024 / 1024** | **length** | 1024 |
| `z-ai/glm-5.3-flash` (billed $0.00095) | **None** | 4657 chars | **1024 / 1024** | **length** | 1024 |

Both candidates spend **100 % of the completion budget on reasoning** and are cut
off before emitting a single answer token. laguna — the model being replaced —
answers directly with zero reasoning tokens. Prompt tokens on the same text:
minimax 3995, glm 3862, laguna 4116.

### 11.2 Root cause: a six-link chain, every link in shipped code

1. `GroundedLLMClient.call()` defaults `max_tokens: int = 1024` —
   `app/rag/generation/llm_client.py:143`.
2. `GroundedGenerationService._call_llm` calls
   `self.llm_client.call(system_prompt, user_prompt)` with **no override** —
   `grounded_service.py:276-278`. 1024 is therefore not a default, it is the only
   value any answer path can use.
3. `_real_call` builds the body from `model` / `messages` / `temperature` /
   `max_tokens` only — `llm_client.py:220-229`. No `reasoning` parameter is ever
   sent, although `/api/v1/models` reports `reasoning`, `reasoning_effort` **and**
   `include_reasoning` as supported on minimax, glm-5.3-flash *and* laguna.
4. Reasoning-by-default models burn the cap on thinking: `finish_reason=length`,
   `content=None`.
5. `llm_client.py:256-261` — *"Some OpenRouter reasoning models may return
   content=None while emitting the response in the reasoning field — fall back to
   it"* — promotes the **truncated** reasoning trace to `text`.
6. `ResponseSanitizer.sanitize` passes the text through unchanged (*"removing
   markers could corrupt readability"*, `sanitizer.py:84-85`, `:134`), so the raw
   trace reaches the user verbatim.

Net effect for reasoning models: production serves **truncated internal reasoning
as the answer** — no Step 1 / Step 2 structure, no final conclusion.

### 11.3 Completion signature over the fixed 40-QID sample

If §11.2 is right, graded text should rarely contain its own "Step 2" and should
end mid-thought. Measured on the same cache as §7 (no new API calls):

| cond | n | has "Step 1" | has "Step 2" | ends on punctuation | mean soft | mean len |
|---|---:|---:|---:|---:|---:|---:|
| `laguna-s-2.1` | 7 | 7 | 7 | 6 | 0.4156 | 3331 |
| `minimax-m3.1-flash` | 40 | 21 | **3** | 6 | 0.3949 | 4995 |
| `glm-5.3-flash` | 40 | 15 | **14** | 15 | 0.3923 | 3794 |
| `nemotron-3.5-super-vl` | 39 | 6 | 6 | 2 | 0.3841 | 4162 |
| `apodex-1.1-mini` | 13 | 13 | 13 | 1 | 0.4243 | 3785 |

The adopted default reaches its own answer phase in **3 of 40** questions; GLM in
14/40; laguna in 7/7. (nemotron and apodex show the same text signature — their
response channel was *not* probed, only the graded text was inspected.)

### 11.4 What this does to the scoreboard

§7/§8 deltas (**minimax +0.011, glm +0.0085**) are *reasoning-text vs.
complete-answer* deltas, not answer-vs-answer deltas. The gate fires on concept
mentions, authority keywords and quoted passages — all of which occur inside a
reasoning trace — so a model can win the delta while never producing a
conclusion. That does not invalidate the ranking: all 40 QIDs were graded under
one config, and the `answer_correctness` judge read the same kind of partial text
for every arm. It does mean **the swap cannot be signed off on delivery quality
from these numbers alone** — what scores is not what the user receives.

### 11.5 Other delivery gaps found in the same pass

- **Timeout mismatch hides latency risk.** Production uses
  `httpx.Client(timeout=30.0)` (`llm_client.py:237`); the eval client uses
  `TIMEOUT_S = 60.0` (`evaluation/llm_ssl_client.py:20`). Calls above 30 s fail in
  production but score fine in the eval. Share of calls over 30 s: minimax 0/40,
  **glm 2/40**, **nemotron 10/39** (p90 94.7 s, max 129.8 s), and **laguna's own
  baselines 12/51** (mean 26.8 s, max 72.9 s — n grows with the §12 run). A
  timeout is retried 3× with exponential backoff (`llm_client.py:300-304`) then
  returns an empty error response, i.e. ~95 s of user waiting before nothing.
- **No cost telemetry in production.** `_real_call` reads `usage` but drops
  `usage.cost` (`llm_client.py:262-276`), so per-answer spend is unobservable in
  production even though OpenRouter returns it on every response.
- **No configuration seam.** `.env.example` exposes exactly two LLM knobs —
  `LLM_ENABLED` (line 221) and `RAG_LLM_MODEL` (line 239). `max_tokens`, the
  timeout and reasoning behaviour are all hardcoded, so the fixes below are code
  changes, not settings changes.

### 11.6 Fix options — costed, not yet spent

All three models accept `reasoning` / `include_reasoning` / `max_tokens`, so
neither fix requires changing providers. GLM list price is $0.048/M in, but the
route actually billed $0.1312/M in and **$0.4375/M out** (measured), and prompt
cost (~$0.0005/call) dominates at these sizes:

| option | mechanism | effect on the two failure modes | GLM cost / 150 calls |
|---|---|---|---:|
| **A — raise the answer cap** | `max_tokens` 1024 → 4096 | reasoning *and* answer both fit; thinking preserved; latency up (fights the 30 s timeout, so A should ship with a timeout setting) | **~$0.20–0.32** |
| **B — disable reasoning** | send `reasoning` off for answer calls | mirrors laguna (0 reasoning tokens, `finish=stop`); answers stay inside 1024 *and* inside 30 s; quality may drop — which is what the run would measure | **~$0.11–0.13** |

minimax stays $0 either way (free). Proposed sequence, probe-first as usual:
1. **fix probe** — 1 call per model per option (≈ **$0.004**): confirm
   `content` becomes non-null and `finish=stop`;
2. **N=40 validation** under the winning option (≈ **$0.03–0.06**) — re-check the
   veto set and the §5 criteria against *real answers*;
3. **full 150** (≈ **$0.11–0.32**) only if step 2 holds.

**The laguna fills collected in §12 are not invalidated by any of this** — laguna
stops naturally at 407 tokens with no reasoning field, so its answers do not
depend on the cap. Those fills remain the baseline asset after 10/31.

### 11.7 Lever probe (2026-10-20): reasoning cannot be disabled; the budget is the lever

The option-B probe answered a different question than it asked. Four levers were
sent to minimax on the Q031 prompt (free, 4 calls):

| lever | observed |
|---|---|
| `max_tokens=4096` | `finish=length`, **4096/4096** reasoning tokens, `content=null` |
| `reasoning={"enabled": false}` | **HTTP 400 — "Reasoning is mandatory for this endpoint and cannot be disabled."** |
| `reasoning={"effort": "low"}` | ignored: 1024/1024 reasoning, `content=null` |
| `include_reasoning=false` | trace hidden, thinking unchanged: `finish=length`, `content=null` |

So **option B does not exist for minimax on OpenRouter**, and raising the cap
alone does not produce an answer: at 4096 the model spends the whole budget
thinking. 8192 is not a fix either — one probe finished at 6654 tokens (5355
answer chars, 54 s) and the next hit 8192 reasoning tokens with `content=null`
in 72 s. Both breach the 30 s production deadline.

The lever that works is a thinking **budget**: `reasoning={"max_tokens": N}`.
With N=512/1024/2048 the model emits ~300–700 reasoning tokens, then
`finish=stop` and a real answer, in 5–15 s. GLM accepts the same field.

Consequence: the P0 is neither "disable reasoning" nor "raise the cap". It is
**cap the thinking, leave the rest of the completion budget for the answer, and
fail loudly when `content` is still null**. Implemented in §13.

## 12. Full-150 extension run (2026-10-19)

**Status: complete, 150/150 paired.** 110 QIDs × 3 models plus the original 40
re-run, `--max-calls 400 --delay 4`: 330/330 calls, minimax and glm 0 errors,
laguna 12 HTTP 429 rows refilled in two `--requeue-errors` passes at a longer
delay. Laguna baselines: **150/150** (40/40 original, 110/110 extension).

### Splits (paired, vs the cached laguna baseline)

| slice | n | laguna → minimax | *p* | laguna → glm | *p* |
|---|---:|---|---:|---|---:|
| **all paired** | 150 | 0.3695 → 0.3824 (**+0.0129**, 81W/66L) | **0.0013** | 0.3695 → 0.3768 (+0.0073, 73W/76L) | 0.127 |
| original 40 | 40 | 0.3838 → 0.3949 (+0.0111, 19W/20L) | 0.233 | 0.3838 → 0.3923 (+0.0085, 16W/24L) | 0.368 |
| extension 110 | 110 | 0.3643 → 0.3778 (+0.0135, 62W/46L) | 0.004 | 0.3643 → 0.3711 (+0.0068, 57W/52L) | 0.300 |
| cohort: failure | 88 | 0.3575 → 0.3763 (**+0.0189**, 50W/37L) | — | 0.3575 → 0.3632 (+0.0058, 41W/46L) | — |
| cohort: control | 61 | 0.3886 → 0.3913 (+0.0026, 30W/29L) | — | 0.3886 → 0.3965 (+0.0079, 31W/30L) | — |
| IE (15) | 15 | 0.4078 → 0.3929 (**−0.0149**, 3W/12L) | — | 0.4078 → 0.4008 (−0.0070, 5W/10L) | — |
| veto: must-hold 4 | 4 | 0.5407 → 0.4992 (**−0.0415**, 0W/4L; bin 3→3) | — | 0.5407 → 0.5343 (−0.0065, 1W/3L; bin 3→3) | — |
| veto: Q060 | 1 | 0.4890 → 0.4253 (−0.0637; credit 1→1) | — | 0.4890 → 0.4375 (−0.0515; credit 1→1) | — |
| structural Q084/Q023/Q123 | 3 | 0.4454 → 0.4371 (elim 2→1) | — | 0.4454 → 0.4264 (elim 2→1) | — |

### What the run establishes

* Minimax's pooled edge is **+0.0129 and survives** (t=+2.86, p=0.0013), but the
  sign test is a coin flip (82W/67L, p=0.251). The mean is doing the work, not
  consistency — see the sensitivity check below.
* The edge is entirely the **failure cohort** (+0.0189). On the control cohort it
  is +0.0026. On the 15 insufficient-evidence questions it is **negative
  (−0.0149)**: minimax loses one abstention credit (13→12) and reduces 12 of 15.
* The original 40 alone never reaches significance (p=0.233); the 110-question
  extension does (p=0.004), so the pooled result is not carried by one slice.
* **Sensitivity:** dropping the 6 largest gains moves p from 0.0013 to 0.072.
  Six questions carry the pooled result.
* **Veto:** as written, the must-hold set holds on binary (3→3) for both
  candidates and Q060 keeps its abstention credit (1→1). Minimax loses the soft
  comparison on all four must-hold questions (0W/4L) and one elimination flag
  (Q023). The §10 Q052 ruling is still open and now decides whether the veto
  passes or is waived — **and §13.1 shows this veto pass is partly an artifact
  of scoring truncated traces, so it must be re-measured under the fixed
  channel.**
* **GLM does not pass any test**: +0.0073 pooled (p=0.127), +0.0068 extension
  (p=0.300), 73W/76L.
* **Delivery:** minimax reaches its own Step 2 in **10/110** extension questions
  (9 %) and is at the 1024 cap in 109/110. GLM: 46/110, at cap 68/110. Laguna:
  92/110 (84 %), and **36/110 (33 %) take longer than 30 s** — the incumbent
  already misses the production deadline on a third of calls. This is the §11
  defect, measured at full scale: the challenger scores were produced by
  truncated reasoning traces, not answers.

## 13. Optimisations required for final delivery (2026-10-19)

Ordered by how close they sit to a user receiving a wrong answer. Every item
cites evidence already in this doc or in `evaluation/CONCEPT_SHORTFALL_PLAN.md`
§1's segmentation of the 85 concept-shortfall QIDs (RETRIEVAL 37 / MIXED 15 /
GATE_EDGE 12 / GENERATION 11 / CORPUS 10).

### P0 — the delivered text is wrong

1. **Fix the answer channel (§11) — DONE (2026-10-20).** `answer_text()` in
   `app/rag/generation/llm_client.py` serves `message.content` only. Null or
   blank content returns an `llm_empty_content` error instead of the reasoning
   trace; the trace fallback survives behind `RAG_LLM_REASONING_FALLBACK`
   (default off). `evaluation/llm_ssl_client.py` applies the same rule, so the
   gate measures what production serves.
   The lever is a thinking **budget**, not "reasoning off" (§11.7): minimax
   rejects `reasoning={"enabled": false}` with HTTP 400. Measured on 24 QIDs
   (8 top-gain + 4 must-hold + 6 IE + 6 failure, free): **4096 completion /
   1024 thinking** gives non-null content and `finish=stop` on **24/24**, 465
   reasoning tokens mean, 10.1 s mean, 0 calls >30 s. Paired against the
   truncated baseline it is **−0.0011 (11W/13L)** — the fix does not lose the
   measured gain. `think512` is −0.0047 with 2/24 truncated; `think2k` is
   −0.0019 but loses more must-hold soft score (−0.025 vs −0.001). **1024 is
   the shipped default.**
2. **Make the budget configurable and assert the fix — DONE.** New settings
   `RAG_LLM_MAX_TOKENS` (4096), `RAG_LLM_REASONING_MAX_TOKENS` (1024),
   `RAG_LLM_TIMEOUT_S` (30), `RAG_LLM_REASONING_FALLBACK` (false) in
   `app/shared/config.py`, documented in `.env.example`. Resolution happens in
   `GroundedLLMClient.call()`, so every caller inherits it (agent graph,
   research sessions, eval harnesses), not only `_call_llm`.
   `tests/test_llm_answer_channel.py` (26 tests) pins the rule, including a
   recorded default-model response
   (`tests/fixtures/llm/minimax_grounded_q052.json`) that must have non-null
   content and a conclusion.
3. **Reconcile the production and eval timeouts — DONE.**
   `evaluation/llm_ssl_client.py` no longer defines `TIMEOUT_S = 60.0`; it reads
   `RAG_LLM_TIMEOUT_S` like the production client. The gate now scores calls at
   the deadline production enforces.

### 13.1 Re-run required: the veto set changes under real answers

The 150-run paired truncated traces. Under the fixed channel (think1k), on the
same must-hold QIDs and the same laguna baselines:

| QID | soft laguna → think1k | binary | credit | elim |
|---|---|---|---|---|
| Q052 | 0.486 → 0.486 | 0 → 0 | 0 → 0 | T → T |
| Q015 | 0.585 → **0.628** | 1 → 1 | 0 → 0 | F → T |
| Q047 | 0.539 → **0.489** | **1 → 0** | 0 → 0 | T → T |
| Q002 | 0.552 → 0.556 | 1 → 1 | 0 → 0 | T → **F** |
| **must-hold** | 0.541 → 0.539 | **3 → 2** | — | 3 → 3 |
| Q060 | 0.489 → **0.350** | **1 → 0** | **1 → 0** | T → F |

So the 150-run's veto pass (binary 3→3, Q060 credit 1→1) is **partly an artifact
of scoring truncated traces**. Q047 flips at the 0.5 threshold (0.489 is inside
sampling noise of 0.5) and Q060 loses a real abstention. Each cell here is n=1
at temperature 0.1, so this must not be decided from one sample: run **5 samples
per must-hold QID and Q060** under the fixed config (free for minimax) before
the §10 ruling.

The IE lane is not the problem: on the 6 IE QIDs think1k is **+0.0085 (4W/2L)**
and gains an abstention credit (4→5). The top-gain 8 are **+0.0931 (8W/0L)** —
which is the same concentration the 150-run sensitivity showed (4 questions
carry the pooled result), so the fixed-channel full run is expected to keep the
edge and move the veto, not erase the edge.

### P1 — the quality ceiling (largest measured lever is retrieval, not the model)

4. **Wire the targeted-retry second pass into the answer path.** 37 of the 85
   shortfall QIDs are RETRIEVAL (gold outside top-10; 11 of them at rank 11–20,
   reachable by a cap-20 retry). The machinery is built and measured —
   `TargetedRetryPlanner` + `targeted_retry_node`, Stage-1 A/B **+0.147
   gold-in-window (CI 0.09–0.21), 22 recovered / 0 lost**, and an offline retry
   already recovered 15 QIDs with **0 regressed across 150**. The path the
   benchmark measures — `GroundedGenerationService.generate`, the same call
   production makes at `tasks.py:727` — is single-pass. No model change has moved
   soft score by more than **+0.011**; this lever attacks the largest segment.
5. **Read the corrected-config re-run against the GENERATION segment (11 QIDs)** —
   *"context covers, answer drops"* is the only bucket a model swap can move
   (Q014 Q031 Q032 Q053 Q054 Q058 Q059 Q073 Q074 Q075 Q112). Overall soft delta
   across 150 will otherwise be dominated by segments no model can fix.
6. **Leave GATE_EDGE alone.** 8 of the 12 are already resolved/eliminated on the
   base arm and fail only `binary`; only Q080/Q089/Q124/Q123 need action. The
   scoped arm was measured to *harm* three of them (Q100, Q123, Q023).

### P2 — measurement integrity

7. **Rule the Q052 / must-hold ambiguity (§10).** Binary-only restatement or a
   documented veto waiver — currently the verdict rests on an unstated reading of
   its own criterion.
8. **Stop treating `authority_hit` as a quality signal on single-word
   authorities.** Q052's `gold_authorities=["court"]` fires on any occurrence of
   the word; laguna's elim=True was carried by an irrelevant quote from s.46.
   With elim parity (11→11) being the veto counter, this artifact sits directly
   under the decision.
9. **Persist `usage.cost` in production telemetry — DONE at the response level.**
   `_usage_dict()` keeps `cost` and `reasoning_tokens`; they ride on
   `RAGResponse.token_usage["cost"|"reasoning"]` and
   `debug["cost_usd"|"reasoning_tokens"]`. Still open: a `rag_generation_log`
   column so the $3/day guardrail can be enforced from stored rows rather than
   from responses/logs.
10. **Surface prompt-cache hits.** Prompt cost (~$0.0005/call) dominates GLM's
    bill, and `prompt_tokens_details.cached_tokens` ranges 0 → 3993 across calls
    on identical prompts (measured: minimax 187 then 3993). Prompt tokens also
    vary ~7 % across models on the same text (minimax 3995 / glm 3862 / laguna
    4116), so per-model token accounting belongs in the cost model.

### P3 — outside model scope

11. **CORPUS (10 QIDs)** — even gold provisions cover <0.30 (Q010 Q018 Q044 Q062
    Q070 Q094 Q107 Q118 Q120 Q130). Needs documents, not code or models.
12. **Allow-list hygiene before 10/31** — drop `poolside/laguna-s-2.1:free` after
    retirement, and settle whether `z-ai/glm-5.3-flash` (paid, $0.048/$0.16 per
    Mtok list) stays in an allow-list that until now was free-tier-only.


