# Prompt conditioning for the grounded legal-QA generator: options, evidence, and a land-and-test path from the current stage

**Question.** From where this repo stands now, **how can prompt conditioning be done** — what conditioning surfaces
exist, which of the audited failure modes does each one plausibly target, what does the evidence (repo measurements +
primary sources) say about each, and what is the lowest-risk way to land and test them from this stage?

The "Strategy A: Enforce Dual-Tier Reasoning" IRAC scratchpad is treated here as **one candidate option** inside that
survey (see §6.7 for its own verdict and strongest objections), not as the organising question.

**Date:** 2026-10-10. **Status:** research only — no code, tests, or config changed.
**Scope note:** primary sources only (source code, first-party vendor docs, original papers, peer-reviewed
legal-education literature). Blog/summary characterisations of papers were not used as evidence; where a repo-internal
secondary doc characterises a paper, the paper itself was re-read (see §5.3, which corrects one such claim). Web
sources already vetted in the previous revision of this note are carried forward with their verbatim quotes; nothing
was re-researched.

---

## 1. "This stage" — the exact current state, verified from code

Each item below was read from the source, not inferred from prose docs.

### 1.1 The shipped user template is measured, and pinned byte-identical

`app/rag/generation/prompt_template.py:113-118` carries two directives: the legacy "First quote the passages that
bear on the question, then answer … citing specific sources with `[n]` markers" line, plus the **shipped SPEC-1 scan
line** ("Before answering, go through every numbered source in the context and note the ones that bear on the
question, including any that add a condition, exception, threshold or definition the answer depends on"). The
template is pinned to the exact measured candidate by
`tests/test_baseline_citation_enforcement.py:101-108` (`_CANDIDATES["cand_scan"] == GROUND_QA_USER_TEMPLATE`), must
keep the quote line (`:86-99`), and every `cand_*` candidate must keep it too (`:127-131`) while never re-introducing
the banned gate phrases (`:65-68`, `:133-145`). Conditioning work is therefore an *additive-candidate* problem, not a
template rewrite problem.

### 1.2 A proposed conditioning template exists and has never been measured

`docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:281-304` contains a concrete `GROUND_QA_USER_TEMPLATE_V2`
(documented-roles preamble → statutory scrutiny → final determination), row `7b` at `:326` ("Generation prompt
conditioning"), and the failure-mode preamble at `:245`. No A/B, harness entry, or test references it — it is a
proposal only. That is the entry point this note is about.

### 1.3 Output budget is the binding constraint on any emitted reasoning

`GroundedGenerationService._call_llm` calls the client with defaults
(`app/rag/generation/grounded_service.py:225-228`), and those defaults are
`temperature=0.1, max_tokens=1024` (`app/rag/generation/llm_client.py:124-132`). On the main path the scratchpad and
the answer **share ~1024 output tokens**. A mandatory three-tier scratchpad that lists every proviso, threshold and
trigger across ten sources competes with the answer for that budget; a truncated answer is the plausible failure
mode, and the repo already scores degenerate output as a hallucination rather than a length problem
(`docs/adr/0010-shadow-verifier-threshold-calibration.md:457-464`).

### 1.4 `is_padded` is already outside its own gate

The shipped scan line pushed answers citing ≥9 of 10 sources from **0.075 to 0.175** with precision on that subset
down ~0.07 (`docs/adr/0010-shadow-verifier-threshold-calibration.md:347-351`;
`app/rag/generation/prompt_template.py:111-112`), against a `not_citing_everything` ceiling of
`candidate <= 0.10` (`evaluation/compare_citation_variants.py:256-258`). Any conditioning that adds breadth pressure
starts from an already-breached ceiling. The harness gates are `GATE_RECALL = 0.50`, `GATE_PRECISION = 0.25`,
`GATE_FALSE_ABSTENTION = 0.15` (`evaluation/compare_citation_variants.py:31-33`), plus
`beats_baseline_recall`, `no_false_abstention_regression`, `no_hallucination_regression` (`:236-252`).

### 1.5 The five measured generation failure modes conditioning is aimed at

From the completed 150/150 human audit, 60 of 89 `model_wrong` failures have the gold provision in the prompt and
still answer wrong (`docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:245`), decomposing into five modes
(`:250-254`): **proviso blindness** (operative section quoted, trailing *"Provided that…"* ignored), **definition
anchoring** (anchors to general definitions instead of the operative duty/offence clause), **conjunction drift**
(A AND B read as A OR B), **penalty incompleteness** (base penalty stated, continuing/daily fines and escalation
omitted), **polarity inversion** (exception misread as permission). §3 maps conditioning options onto these, and §7.2
cross-tabulates the same failures against gold-in-prompt to bound which slice conditioning can reach.

---

## 2. The conditioning surface inventory (what can actually be conditioned today)

| # | Surface | Where it lives | Reachable on the default path today? | What it can condition |
|---|---|---|---|---|
| **S1** | User-template directive — the shipped scan line | `prompt_template.py:116-118` | **Yes, shipped** | Merge/synthesis behaviour only: it names condition/exception/threshold/definition as things to *notice*. It does not name rule vs exception as separate tiers, does not force an output shape, and does not require the exception to appear in the answer. |
| **S2** | User-template **additive** directive | `evaluation/ab_baseline_citation.py:_CANDIDATES` `:164-216` | Yes — this is the A/B seam | Any wording that adds instruction while keeping the pinned quote line. `cand_scan_enumerate` (mandatory trailing "Sources: [a],[b],[c]", `:181-196`) and `cand_scan_capped` (selectivity cue, `:201-216`) are the two precedents. |
| **S3** | Per-domain system prompts | `prompt_template.py:31-84` (`DOMAIN_SYSTEM_PROMPTS`), selected at `:156-160` | Yes, but only when a caller passes `domain`; the grounded path passes none (`grounded_service.py:216-219`), so production always renders `fssai` (`:32`) | Domain vocabulary + the citation contract. **Inconsistency worth conditioning work:** only `fssai` carries the two-step "Step 1 — quote / Step 2 — answer" structure (`:14-25`); `env` (`:33-41`), `commercial` (`:42-50`), `animal` (`:51-59`), `wb_state` (`:60-67`), `criminal` (`:68-75`) and `general` (`:76-83`) are single-paragraph prompts with no quote-first step and no two-step contract. `general` and `criminal` are multi-domain defaults, so a de-FSSAI deployment loses the quote-then-answer structure entirely while keeping the user-template quote line. |
| **S4** | Context-side **role conditioning** (`role="…"` on `<document>`) | `context_builder.py:341-354` (`_format_entry`), `:35-45` (`_PACK_ORDER`), `:300-321` / `:323-339` (role extraction + role-ordered packing); role vocabulary from `retrieval/evidence_selector.py:36-44`, priorities `:343-353` | **Code exists, off by default.** `ENABLE_EVIDENCE_SELECTOR` defaults **False** (`app/shared/config.py:195-201`), and `run_generation_pipeline` honours `evidence_set` only when enabled (`tasks.py:525-528`). With `evidence_set=None` the `role` attribute is omitted entirely, so "the default prompt is unchanged" (`context_builder.py:346-348`). | Structural: makes exception/definition/penalty chunks self-describing to the model, and reorders the window so definitions cannot crowd out the primary provision (`:25-34`). This is the *only* conditioning surface that does not spend output tokens. |
| **S5** | Query-type conditioned directives | 14 canonical types at `retrieval/legal_query_classifier.py:42-57` (`penalty`, `exception`, `definition`, `prohibition`, `procedure`, `authority`, `cross-reference`, `direct provision`, `obligation`, `offence`, `enforcement`, `insufficient-evidence`, `temporal`, `ambiguous`) | **The type exists and reaches generation, but not the prompt.** `generate()` takes `query_type` (`grounded_service.py:106`, `:126`) and `ContextBuilder` uses it for budgets and the answerability check (`context_builder.py:110-112`, `:153`, `:222-281`), yet `_render_prompt(query, built)` (`:129`, `:216-219`) passes neither type nor evidence set into `PromptTemplate.render_default`. So the rendered prompt is **query-type-blind**: identical wording for a penalty question and an exemption question. | Targeted: penalty queries could require base + continuing fine + escalation; exception queries could require quoting the proviso; procedure queries could require authority/deadline/Form. |
| **S6** | Negative constraints / guardrails | proposal at `docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:277-279` | Not present; S2 is how it would land | Transcribe exact amounts/periods without rounding; never convert unproven prohibition into permission. |
| **S7** | Two-pass **out-of-band** conditioning (structured reasoner) | `app/rag/generation/structured_reasoner.py:24-73` (IRAC-shaped IR: issue → provisions → definitions → rules → `exceptions_considered` → `condition_evaluations` with satisfied/not_satisfied/**unknown** → conclusion → citations → uncertainties) | **Code exists, flag off.** `ENABLE_STRUCTURED_REASONER` defaults False (`app/shared/config.py:202-208`), as does `ENABLE_LEGAL_AUDITOR` (`:209-215`) | Full IRAC-style reasoning *outside* the answer, removing the §1.3 token-budget conflict, at the cost of one extra LLM call per query. |
| **S8** | **IRAC scratchpad inside the answer** (Strategy A) | proposal text at `docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:259-263`, template `:281-304` | Not present; would land via S2 | Prescribed three-tier emitted reasoning: rule / exceptions & provisos / application. |

**Two structural facts follow from the inventory:** (a) the repo has **no way today to tell the model which source is
the primary provision and which is an exception** in the default configuration (S4 exists but is off, and the
proposal's "Document Roles" preamble at `:265-270` is therefore currently unsupported by the prompt content); and
(b) **the prompt is the only conditioning surface that is both live and free of feature flags and extra LLM calls** —
which is exactly why it is where the measured effects in §1.1 and §1.4 came from.

---

## 3. Failure-mode × conditioning-option map

"Mechanism" = a one-line causal path from the conditioning change to the failure mode. "no mechanism" = the option
cannot reach that mode.

| Option | Proviso blindness | Definition anchoring | Conjunction drift | Penalty incompleteness | Polarity inversion |
|---|---|---|---|---|---|
| **S1 scan line (shipped)** | partial: names "condition, exception, threshold", so exceptions are surfaced as *material to notice*, not as a required output tier | none — it does not rank definitions below the operative rule | no mechanism | no mechanism | no mechanism |
| **S2 additive directive** (e.g. "state the proviso in the same breath as the rule, cite it too") | **primary mechanism**: forces the qualifier into the answer text with its own citation | partial (could add "prefer the operative clause over a general definition") | partial (could require restating cumulative "and" requirements) | **primary** (could require base + continuing + escalation) | partial (could require restating whether a clause permits or prohibits) |
| **S3 domain-prompt conditioning** (restore the two-step quote-then-answer structure to non-FSSAI prompts, §9.1) | none beyond S1 — it adds no proviso wording | partial: the model must name the passage it answers from, so the operative clause competes with the definition | partial: answering strictly from quoted text restates the quotes' own connectives | partial: quoting the penalty passage preserves its daily-fine/escalation clauses | partial: verbatim restating preserves the statute's own negations |
| **S4 context role annotation** | structural: an `role="exception"` chunk is *identifiable*, so a rule-first directive can be conditioned on it | **primary mechanism** by design — `_PACK_ORDER` puts definitions after primary+exceptions and the tag names the role (`context_builder.py:25-34`, `:341-354`) | partial (an `exception`-adjacent proviso becomes addressable) | partial (`penalty_provision` role + penalty tier) | partial (an exception tag disambiguates the narrowing clause) |
| **S5 query-type directives** | **primary** for `exception`-typed queries | primary for `definition`-typed queries | partial (`prohibition`/`obligation` types restate conjunctive elements) | **primary** for `penalty`-typed queries (base + daily/continuing fine + escalation) | partial (`prohibition`/`offence` types) |
| **S6 negative constraints** | no mechanism | no mechanism | no mechanism | **primary** (exact amounts/periods, no rounding) | **primary** (absence of a prohibition is not a permission) |
| **S7 two-pass reasoner** | **primary** — `exceptions_considered` is a dedicated field (`structured_reasoner.py:56`), and `condition_evaluations.status ∈ {satisfied, not_satisfied, unknown}` with "never assume it satisfied" prevents silent satisfaction (`:46-58`) | strong (`definitions_applied` separated from `legal_rules`, `:54-55`) | partial (per-condition evaluation forces conjunctive checks) | strong (a penalty obligation is a rule with conditions) | strong (`unknown` status blocks a polarity guess) |
| **S8 IRAC scratchpad (Strategy A)** | intended primary (tier 2) | intended primary (tier 1 names the governing section, which is the anti-anchor move) | no mechanism beyond restating the rule | no mechanism — tier 2 as written names "thresholds", not penalty escalation structure | partial (tier 3 "apply … to the query facts") |

Note that the audit's own target counts are `abstention_gate` 31, `provision_check` 35, `needs_deeper_reasoning` 23
(`docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:182`), so `provision_check` (definition anchoring + primary-provision
identification) is the single largest bucket — the bucket that **S4 context role annotation targets structurally and
S8/S2 target only rhetorically**. §7.2 cross-tabulates the same buckets against whether gold survived into the window
(20 / 16 / 24 of the 60), which is what bounds the slice conditioning can reach at all.

---

## 4. What the repo has already measured about conditioning moves

### 4.1 SPEC-1 attempt #1 — hard quote gate + abstention escape — REVERTED

`docs/adr/0010-shadow-verifier-threshold-calibration.md:292-299` (baseline-gold slice; identical retrieval pool,
system prompt and context; only the user template differs):

| Metric | shipped (legacy) | SPEC-1 v1 | delta |
|---|---|---|---|
| citation_recall | 0.659 | 0.466 | **−0.192** |
| citation_precision | 0.659 | 0.596 | −0.063 |
| has_marker | 0.527 | 0.709 | +0.182 |
| **false_abstention** | **0.073** | **0.436** | **+0.364** |
| groundedness_score | 0.982 | 0.782 | −0.200 |
| hallucination_detected | 0.018 | 0.218 | +0.200 |

"The hard quote precondition made the model abstain on 44% of answerable questions; fewer answers means fewer
citations, so recall *fell* even though marker use rose" (`:301-304`); the same warning is carried inline in the
template (`app/rag/generation/prompt_template.py:102-105`).

### 4.2 SPEC-1 attempt #2 — the shipped scan line — SHIPPED

`docs/adr/0010-shadow-verifier-threshold-calibration.md:329-339` (3 candidate replicates vs 4 legacy):

| Metric | legacy | scan | delta | t | p |
|---|---|---|---|---|---|
| citation_recall | 0.6388 | **0.7147** | +0.0759 | +4.37 | **<0.0001** |
| citation_precision | 0.6180 | 0.6090 | −0.0090 | −0.28 | 0.78 |
| abstained | 0.1080 | 0.0686 | −0.0394 | −1.10 | 0.27 |

Paired within-qid (n=55): recall **+0.082, p=0.026**; **complete replicate separation** (worst candidate 0.695 >
best legacy 0.661); mechanism `n_citations` 3.75 → 4.61 (`:337-339`; mirrored at
`app/rag/generation/prompt_template.py:91-100`). Diagnosis of *why* it worked, and the boundary of this surface:
"Prompt wording cannot fix which available source is chosen — but it *can* make the model look at the rest of the
window" (`:313-318`).

### 4.3 Four further anti-results that bound any conditioning move

1. **The one measured format-forcing variant was rejected on abstention.** `cand_scan_enumerate` lifted recall
   "+0.067 paired, p=0.017" but "significantly *worsened* abstention (p=0.004), so it was rejected"
   (`docs/adr/0010-shadow-verifier-threshold-calibration.md:352-353`). A *prescribed output format after reasoning*
   is precisely where the abstention damage appeared.
2. **A structured reasoner already exists, is flag-gated off, and its measured lift is inside noise.** Exp D:
   "~+1.3pp soft / +0.8pp binary" (`docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:215`), summarised as
   "Structured reasoning (+1.3%), auditor (−2.3%), citation repair (+0.1%). All within noise / not meaningful per
   spec §24", under the heading **"Prompt / generation changes (low measured impact; do NOT lead with this)"**
   (`docs/RAG_UPGRADE_RESEARCH.md:87-92`).
3. **A quote-first wording change on another population moved abstention 8/21 → 19/21**
   (`docs/adr/0010-shadow-verifier-threshold-calibration.md:124-151`) — prompt wording reliably moves refusal
   behaviour, in both directions.
4. **Single-replicate adoption is explicitly forbidden by this ADR.** `cand_scan_capped` showed the best raw recall
   of anything tested (+0.110 paired, p=0.017) but "was **not adopted** … A single usable replicate cannot clear a
   null this large, and swapping the shipped prompt on that basis would repeat the mistake this ADR already
   documents once — adopting on an underpowered read" (`:349-351`, `:470-486`; `is_padded` tuning is still an
   unchecked adoption-gate item, `:505`).

---

## 5. What the primary sources establish (claim → source → what it supports)

| # | Claim | Source | What it actually supports |
|---|---|---|---|
| C1 | Quote extraction before answering is first-party-endorsed and improves accuracy | Anthropic, *Prompt engineering for Claude's long context window*, https://www.anthropic.com/research/prompting-long-context (2023-09-23) | Their `scratchpad` arm is *exactly* "instruct Claude to pull relevant quotes" — no legal tiers. Verbatim: "Pulling relevant quotes into the scratchpad is helpful in all head-to-head comparisons. It comes at a small cost to latency, but improves accuracy." This is the strongest first-party scratchpad evidence, and it is for the **quote tier the repo already has** (§1.1), not for rule/exception/application tiers. |
| C2 | Grounding guidance is "quote first", not "reason in tiers" | Anthropic, *Prompting best practices*, https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices | "Ground responses in quotes: For long document tasks, ask Claude to quote relevant parts of the documents first before carrying out its task. This helps Claude focus on the relevant content and ignore the rest of the document." Its worked long-document example uses `<quotes>` then `<info>` — no IRAC. Also: "Structure document content and metadata with XML tags … wrap each document in `<document>` tags with `<document_content>` and `<source>`" — the pattern S4 extends with `role=`. |
| C3 | Ordering reasoning before conclusions is vendor guidance | OpenAI, *Prompt generation* meta-prompt guidance, https://developers.openai.com/api/docs/guides/prompt-generation | "Reasoning Before Conclusions: Encourage reasoning steps before any conclusions are reached … Conclusion, classifications, or results should ALWAYS appear last." Supports **ordering only** — no tier content, no exceptions tier, no legality. |
| C4 | General instruction beats a hand-written step plan | Anthropic best-practices page (C2 URL) | "Prefer general instructions over prescriptive steps. A prompt like 'think thoroughly' often produces better reasoning than a hand-written step-by-step plan. Claude's reasoning frequently exceeds what a human would prescribe." The closest first-party statement **against** a prescribed three-tier script. |
| C5 | Preambles/scratchpads carry a latency and verbosity price | Anthropic best-practices page (C2 URL) | "Thinking adds latency and should only be used when it will meaningfully improve answer quality"; verification instructions "can cause over-verification, adding tokens and latency". Also from C1: added prompt length can **degrade** recall of material at the end of the input ("performance on the end can be degraded … it does emphasize the importance of putting the instructions at the end of the prompt"). |
| C6 | Model-specificity and the GPT/reasoning split | Anthropic best-practices page (C2 URL) and OpenAI *Prompt engineering*, https://developers.openai.com/api/docs/guides/prompt-engineering | Anthropic: "Where a technique names a specific model, treat that as measured on that model and re-check it against your own evals before applying it to another model." OpenAI: "reasoning models will provide better results on tasks with only high-level guidance. This differs from GPT models, which benefit from very precise instructions." The repo's A/B model is free-tier `poolside/laguna-s-2.1:free` (`docs/adr/0010-shadow-verifier-threshold-calibration.md:352-354`; `docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:38`), so **all of this is single-model, non-generalising evidence**. |
| C7 | The failure mode is *selection*, not *presence* | `docs/adr/0010-shadow-verifier-threshold-calibration.md:313-318` | "Legacy cites in 98% of answers with ~4 citation objects. The gap was **selection**: gold averages 4.54 of the 10 window chunks, the model cited only ~4 distinct sources, and 25-30% of answers cited none of the gold." A scratchpad does not address selection; an S4 role tag does, structurally. |
| C8 | Exceptions belong with the rule, as pedagogy | Columbia Law Writing Center, *Organizing a Legal Discussion: IRAC / CRAC / CREAC*, https://www.law.columbia.edu/sites/default/files/2024-09/WC%20Handout%20IRAC%2C%20CRAC%2C%20CREAC.revised%209.24.pdf | "Your rule section should resemble a funnel: set out the broadest principles first, with the smaller, secondary components, or **exceptions to the rule** following afterwards." Direct support for the proposal's tier 2 as a *reading of standard pedagogy* — and it places exceptions **inside** the Rule tier, i.e. the proposal is a legitimate transplant, not a novelty. |
| C9 | IRAC is a pedagogy, not an output-quality finding | K. Burton, "'Think Like a Lawyer' Using a Legal Reasoning Grid and Criterion-Referenced Assessment Rubric on IRAC", *Journal of Learning Design* 10(2):57-68, 2017 — https://www.jld.edu.au/article/download/229/229-771-1-PB.pdf (also ERIC EJ1134620) | "This paper identifies a number of acronyms used to teach traditional 'legal reasoning'"; recommends "an incremental development approach to learning, teaching and assessing IRAC … whereby first year law students use a legal reasoning grid". About **teaching/assessing humans**; no LLM experiment. Corroborated by Burton 2016 (ERIC EJ1124439, *JUTLP* 13(5)): "The dominant approach for problem-based questions in the discipline of law over the last 40 years is IRAC." |
| C10 | IRAC-family prompts beat generic CoT **on binary legal entailment** | F. Yu, L. Quartey, F. Schilder, *Legal Prompting: Teaching a Language Model to Think Like a Lawyer*, arXiv:2212.01326 (NLLP 2022 @ EMNLP) — https://arxiv.org/abs/2212.01326 , https://arxiv.org/html/2212.01326v2 | COLIEE task 4 **binary YES/NO entailment** on the Japanese Civil Code, GPT-3 `text-davinci-002`, temperature 0, greedy. Table 4: TRRAC 0.8148 (2021) / 0.6881 (2022); IRREAC and IRRAC 0.7156 (2022); canonical IRAC 0.7284 / 0.6881. Test sets are **81 and 109 questions**. Details and the four non-transfers in §6.6. |

### 5.1 Carried-forward CoT origin claim

Wei et al., *Chain-of-Thought Prompting Elicits Reasoning in Large Language Models*, arXiv:2201.11903 — abstract:
"chain of thought prompting improves performance on a range of **arithmetic, commonsense, and symbolic reasoning**
tasks", headline result GSM8K at 540B parameters (https://arxiv.org/abs/2201.11903). It is the origin of the
reasoning-scratchpad idea; it tests **no legal task, no retrieval, no citations**.

### 5.2 Primary-source absences (findings, not gaps to paper over)

- **No first-party vendor guidance prescribes IRAC-style tiering for legal QA.** Anthropic's prompt-engineering
  reference (C2) covers XML structure, quote-grounding, thinking, tool use — no IRAC. Anthropic's citations material
  notes citations and structured outputs currently conflict, which is why a citation-marked answer pass is kept
  separate from a JSON reasoning pass (as argued in `docs/QUERY_DECOMPOSITION_PROMPT_RESEARCH.md:79`).
- **No peer-reviewed or first-party paper was found measuring a rule+exceptions dual-tier scratchpad on grounded
  legal answers or citation metrics.** The repo notes cite LegalBench (NeurIPS 2023 D&B track,
  https://proceedings.neurips.cc/paper_files/paper/2023/file/89e44582fd28ddfea1ea4dcb0ebbf4b0-Paper-Datasets_and_Benchmarks.pdf)
  for the IRAC-shaped task taxonomy (`docs/QUERY_DECOMPOSITION_PROMPT_RESEARCH.md:36`, `:113`); a prompting ablation on
  it was **not verified here**, so "LegalBench shows IRAC-style prompting lifts legal QA" is unsubstantiated for this
  decision.
- **No measurement exists in this repo of the proposed V2 template or of any S2/S5 conditioning on proviso
  application.** The only measured conditioning effects on this prompt are the two in §4.1-4.2 and the three
  rejected/underpowered candidates in §4.3.

### 5.3 Corrigendum to a repo-internal secondary claim

`docs/QUERY_DECOMPOSITION_PROMPT_RESEARCH.md:36` characterises arXiv:2212.01326 as: "Yu et al. 'Legal Prompting' …
found IRAC-derived zero-shot prompts beat generic chain-of-thought on COLIEE entailment (2021: 0.7037→0.8148; 2022
SOTA beaten 0.6789→0.7431)". Re-reading the paper: those comparisons are against the **COLIEE competition winners**,
not the paper's own ZS/ZS-CoT baselines; the 0.8148 is **TRRAC**, not IRAC; and the paper's own best plain ZS scored
**0.7407** in 2021, above canonical IRAC's **0.7284** (`S4.T1`, `S4.T4`). Directionally fair, materially imprecise —
cite the paper's Table 4 and §5, not that summary line.

---

## 6. The IRAC scratchpad as one option in the survey

### 6.1 What it conditions
S8 = an S2 directive that prescribes three emitted tiers (rule / exceptions+provisos / application) before the
answer, replacing the generic "quote then answer" framing.

### 6.2 Why it is plausible
Tier 2 is a defensible reading of standard pedagogy (C8, C9), and the repo already mirrors IRAC in
`structured_reasoner.py:49-62`, so the ontology is not the gap. Tier 1 (name the governing section) is aimed at
definition anchoring, the largest audit bucket (`docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:182`).

### 6.3 Why it is not established
C1/C2 show the vendor-endorsed scratchpad is quote extraction (already implemented). C3 supports ordering only.
C4 argues a hand-written step plan can be *worse* than a general instruction. C10 is the only on-point paper and
does not transfer (§6.6). And the repo's own record is negative on this class: §4.1 (structure + escape clause →
false abstention 0.436), §4.3.1 (a prescribed format → abstention p=0.004), §4.3.2 (structured reasoning inside
noise, "do NOT lead with this").

### 6.4 Its specific costs in this codebase
- Output-budget conflict (§1.3): three tiers plus the answer inside 1024 tokens.
- Breadth conflict (§1.4): tier 2 asks for *all* provisos across ten sources — stronger breadth pressure than
  `cand_scan_capped`, which was itself the remedy for `is_padded`.
- It does nothing for definition anchoring on the default path, because the prompt has no role information to
  anchor against (S4 off, §2).
- Metric mismatch (§8.2): the cheap harness cannot observe whether the exception was *applied*.

### 6.5 Strongest objections
1. Its target metric (proviso application) is not measured by any instrument available at this stage.
2. The one measured structural variant in this repo regressed abstention (p=0.004) and was rejected.
3. The proposal's tier 2 is already present in weaker form in the shipped scan line (§1.1), and that weaker form
   is the configuration that produced the only positive prompt measurement this repo has.
4. The strongest objection of all is sequencing: it asks for a large prompt change in the area the repo has
   already labelled "low measured impact; do NOT lead with this" (`docs/RAG_UPGRADE_RESEARCH.md:87-92`).

### 6.6 The on-point paper, and the four ways it does not transfer
1. **Plain zero-shot beat canonical IRAC in 2021** — 0.7407 vs 0.7284 (`S4.T1`, `S4.T4`). The headline win came from a
   *different* acronym (TRRAC), i.e. the paper's own result is that tier composition matters and is unstable.
2. **Few-shot beat the IRAC prompts in 2022** — 8-shot 0.7432 vs best LR prompt 0.7156; conclusion: "The zero-shot
   with legal reasoning approach shows the best result for one year only and may be more prone to overfitting to a
   specific test set indicating that further research of those prompting approaches is needed" (§5).
3. **Generic CoT was not a robust baseline** — ZS-CoT scored 0.6296 on 2021, "underperform[ing] ZS … by 17.65%"
   (`S4.SS3`), so "IRAC beats CoT" is measured against a CoT that is weaker than a well-worded instruction.
4. **It is classification, not grounded QA** — YES/NO entailment with accuracy as the only metric: no citation
   recall/precision, no groundedness, no abstention, no free-text answer, no retrieval. It cannot license an
   expected direction on any metric this repo gates on.

The paper also shows a named scheme does not guarantee exception surfacing: in Appendix A example ID R02-24-O the
TRRAC explanation restates Article 566's general one-year rule without mentioning that article's closing "provided,
however, that this does not apply if the seller knew or did not know due to gross negligence the non-conformity"
(https://arxiv.org/html/2212.01326v2). The authors' own caveat closes the paper: "it is questionable whether
prompting actually teaches a LM to 'think like a lawyer'".

---

## 7. Why do the 60/89 failures happen — and which of them conditioning can actually touch

A claim recurs in the repo — "60 of 89 failures have gold in the prompt … lost downstream in generation"
(`docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:84-85, :245`) — together with the inference drawn from it
("…without explicit chain-of-thought / proviso quoting directives in the prompt", `:246`. This section evaluates
that inference against the attribution data rather than repeating it.

### 7.1 What the 60 actually are

`evaluation/failure_attribution.py` attributes each of the 89 audit `model_wrong` questions by asking `ContextBuilder`
*binary* whether a gold chunk survived into the window (arm `C_hybrid`). Result
(`evaluation/out/ceiling_v5/failure_attribution.json`): **generation 60 (67.4%) / retrieval_rank 26 (29.2%) /
never_retrieved 3 (3.4%)**. The script states its own limit: "Measures presence of gold evidence, not whether the
model USED it — a question can have gold in the window and still fail for reading reasons."

Two consequences before any remedy is chosen:

- The 26 `retrieval_rank` failures are **not conditioning's**: gold depth when found is median 8.0, p90 105 — gold
  ranked just below the effective window. Effective windows are per query type
  (`app/rag/generation/context_builder.py:96-113`): 8 chunks for `prohibition`/`definition`, 10 for
  `penalty`/`general`/`procedure`, 12 for `cross_reference`/`case_law`, and the configured ceiling for every type
  the budget table never matches. That is a ranking/packing problem (S4 territory).
- The 60 are "gold *admissible* to the window and the answer still wrong". Admission is measured, use is not: a
  gold chunk can sit in the window unread, which is precisely an attention/selection failure, not a presence one.

### 7.2 The 60 are three different failures — the audit's cross-tab

`failure_attribution.json`'s `by_model_action` cross-tabs the generation bucket against the human audit's component
targets (`docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:182`; action names are the audit's own):

| Audit target (of 89 `model_wrong`) | Total | Gold-in-prompt (the 60) | What actually failed | Conditioning reach |
|---|---:|---:|---|---|
| `provision_check` (F2 gate list) | 35 | **20** | The governing provision was not quoted/verified | **Direct** — quote-first + role identity (S3, S4, S6); the strongest fit |
| `needs_deeper_reasoning` | 23 | **16** | Interpretation/application depth: proviso blindness, conjunction drift, penalty incompleteness, polarity inversion | **Plausible but unmeasured** — targeted directives (S2/S5) with zero measured effect on this slice |
| `abstention_gate` (F1 gate list) | 31 | **24** | The reference says the right move was a qualified unknown; the model asserted instead | **Wrong direction** — the remedy is answerability control, not more applied reasoning |

So among the 60: **20 (33%)** sit in the slice conditioning directly targets, **16 (27%)** in the slice with a
plausible but wholly unmeasured mechanism, and **24 (40%)** — the largest single block — are abstention failures
where conditioning that pushes "state the rule and every proviso" pulls against the audit's verdict. "The 60 are all
prompt failures" is therefore true only in the downstream sense (none is a retrieval-rank or corpus failure); as a
statement about remedy it is wrong for 40 of 60 and unproven for 16 of 60.

### 7.3 The repo's measured record points away from the 24

Every answer-shaping conditioning experiment in the repo's history regressed an abstention metric; none improved
one: the reverted quote gate (false abstention 0.073 → 0.436, §4.1), the prescribed trailing "Sources:" format
(`cand_scan_enumerate`, abstention p=0.004, §4.3.1), and a quote-first wording change that moved missed-abstentions
8/21 → 19/21 on another population
(`docs/adr/0010-shadow-verifier-threshold-calibration.md:124-151`). Given 24 of the 60 are `abstention_gate`
targets, conditioning the whole 60 predictably trades part of the `provision_check` slice for regressions on the
largest slice — the exact trade on which `cand_scan_enumerate` was rejected.

### 7.4 Residual uncertainty in the 60

- **Presence ≠ use** (§7.1): `provision_check` failures with gold admitted are partly attention failures the window
  cannot explain away, so their count is an upper bound on conditioning's addressable share.
- **Effective windows are 8 chunks** for the `definition` and `prohibition` types, so definition anchoring is first
  a packing question (which chunks occupy those 8 slots) and only then a wording question.
- **The denominator is itself under audit**: alongside the 89 `model_wrong` sit 57 `evaluator_miss` (19 fixed by
  evaluator v2, 38 unresolved as v3 candidates) and 4 `reference_narrow`
  (`evaluation/out/ceiling_v5/full_review_tabulation.md`; `docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:182`).
  V3 reclassification would move the 60's composition in either direction.

### 7.5 Verdict on "they are all prompt-related"

Read the 60/89 preamble as *"the loss is in generation"*, not *"prompt changes will recover it"*: 33% directly
addressable, 27% plausible-but-unmeasured, 40% pointed the other way. That is why the sequence in §9 starts with the
zero-token structural moves (S4) and treats prompt wording as item 3 — and why the F1 gate list (31 abstention
targets) is reported as its own workstream rather than absorbed into conditioning. It also independently reproduces
the repo's own verdict, "Prompt / generation changes (low measured impact; do NOT lead with this)"
(`docs/RAG_UPGRADE_RESEARCH.md:87-92`).

---

## 8. How to land and test conditioning from this stage

### 8.1 The mechanics of registering a candidate
1. Add a new entry to `evaluation/ab_baseline_citation.py:_CANDIDATES` (`:164-216`), following the existing
   `cand_scan*` pattern: same `<legal_context>` block, same `{context}`/`{query}` placeholders, and **must** keep
   "First quote the passages that bear on the question" (`tests/test_baseline_citation_enforcement.py:127-131`).
2. Keep it free of the banned phrases — "at least one verbatim", "you must follow these steps", "zero relevant
   passages", "produced zero quotes", "cannot find the answer" (`:65-68`, `:133-145`).
3. Run it as a variant: `python -m evaluation.ab_baseline_citation --variant <name> --tag <replicate-suffix>`
   (`:342-349`), then compare with
   `python -m evaluation.compare_citation_variants --baseline <baseline> --candidates <names>` (`:274-281`).
4. Do **not** edit the shipped template to match a candidate. The pin
   `_CANDIDATES["cand_scan"] == GROUND_QA_USER_TEMPLATE` (`tests/test_baseline_citation_enforcement.py:101-108`)
   means replacing the shipped text requires a deliberate new pin, after the A/B clears.
5. There is a **free, no-LLM instrument for context-side conditioning** already in the tree:
   `evaluation/bench_p0_1_packing.py` replays cached retrieval arms through `ContextBuilder` with
   `evidence_set=None` vs supplied, and states its own scope: "Deterministic, offline replay … No LLM calls: this
   measures the PROMPT EFFECT of packing, not answer quality. Answer quality needs a paid A/B run"
   (`:239-243`); the live-LLM counterpart is `evaluation/ab_p0_1_packing.py:8-11` (`packing_off` / `packing_on`).

### 8.2 The gates, and the blind spot
Gates (`evaluation/compare_citation_variants.py:236-259`): `recall_at_least_gate` (≥0.50),
`precision_at_least_gate` (≥0.25), `beats_baseline_recall` (delta > 0 — the binding criterion, `:23-25`),
`false_abstention_within_gate` (≤0.15), `no_false_abstention_regression` (delta ≤ 0),
`no_hallucination_regression` (delta ≤ 0), `not_citing_everything` (`is_padded` ≤ 0.10).
Metric definitions/implementations: `docs/adr/0010-shadow-verifier-threshold-calibration.md:209-211` (formulas) and
`evaluation/ab_baseline_citation.py:295-320` — including
`is_padded = int(distinct_cited >= max(len(pool) - 1, 3))` (`:308`) and
`false_abstention = int(abstained and len(gold) > 0)` (`:317`). Note that `evaluation/README.md` documents the
artefact schemas, **not** these metrics.

**The blind spot, stated plainly:** the cheap harness measures citations, refusal behaviour and groundedness. It
does **not** measure whether the answer applied the proviso, identified the primary provision, or kept the penalty
complete — i.e. none of the five failure modes in §1.5. Consequences for validation:
- Prompt conditioning can be cheaply validated **only for indirect effects** (distinct sources cited, refusal rate,
  padding, hallucination flag).
- The effect conditioning exists to produce (correct application) needs the live-LLM path with gold references
  (`evaluation/eval_e2e_v2.py`, dual scorecard per `docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:316`), and that
  path is rate-limit-blocked — the ADR records that regenerating a tracked artefact took "0 of 44 slots after 14
  invocations" (`docs/adr/0010-shadow-verifier-threshold-calibration.md:524-533`) and that `is_padded` tuning is an
  unchecked adoption-gate item (`:505`).
- Therefore a candidate that clears every citation-side gate is **not** evidence that the conditioning worked. The
  honest report format is: citation-side gates passed/failed + an explicit "application correctness unmeasured"
  line.

### 8.3 Statistical bar
≥3 independent replicates with replicate-level comparison, per the ADR's own reasoning that one run cannot resolve
an effect this size (`docs/adr/0010-shadow-verifier-threshold-calibration.md:354-356`) and that underpowered
adoption is the documented mistake (`:478-482`). The shipped line's standard is the reference: complete replicate
separation, paired within-qid n=55, p<0.0001 (`:329-339`).

---

## 9. Recommended conditioning sequence from this stage

**Execution status (2026-10-10, loop run against the gold-in-prompt 60).**
Instrument: `evaluation/rerun_gold_in_prompt.py` replays the 60 gold-in-prompt qids (audit
`model_wrong` ∩ `failure_attribution.json` bucket `generation`) through the production generation path and
adjudicates each answer against the benchmark's own `gold_concepts` / `gold_authorities`
(`--score-only`, `out/ceiling_v5/rerun_gip/<tag>_adjudication.json`; `--cohort control|all`,
`--legacy-template`, `--no-drop-index` provide the regression control).

Results (eliminated / 60, adjudicated):

| run | config | eliminated |
|---|---|---|
| baseline | scan-only template, index chunks admitted | 5 |
| cond_v1 | + conditioning block (`cand_scan_cond`, shipped, pinned) | 9 |
| cond_v2 | + 40k char window | 10 |
| cond_v3 | 24k chars + index-chunk dropping (`is_index_chunk`) | **11** |
| cond_v4 | 40k chars + index dropping (reverted to cond_v3 config) | 8 |

Landed in code: the conditioning block in `GROUND_QA_USER_TEMPLATE` (quote line + scan line verbatim,
pin moved to `cand_scan_cond` in `tests/test_baseline_citation_enforcement.py`), index-chunk dropping
(`is_index_chunk` + `RAG_CONTEXT_DROP_INDEX_CHUNKS`, default on, 29 tests in
`tests/test_rag_generation.py`), and the harness. Regression control on the other 90: **18/90 before
→ 18/90 after** (`control_pre` vs `control_post`) — no regression. 112 tests pass on the two affected
suites; ruff clean on all touched files.

**Why 0 was not reached (causes per qid, all measured, in `cond_v3_adjudication.json` + `gip_causes.json`):**
of the 49 remaining, 23 carry none of the required content in the arm's top-60 (retrieval-side: the
operative provision never enters the candidate pool this harness replays — fixing them needs live-Qdrant
reranking work, untestable here), 7 are corpus-blocked (the admitted chunk is an OCR fragment whose
text cannot support any answer), 16 sit just outside the 20-chunk window (ranks 21-40) but of those only
2-4 carry the *gold* provision rather than coincidental concept tokens — opening the char window further
was measured and *reduced* eliminations 11 → 8 — and 3 are terminology-only. Nine of the original 60 also
turn out to be misattributed: their "gold in prompt" was an arrangement-of-sections/index chunk, so with
substantive packing they are no longer gold-in-prompt failures at all (`gold_dropped_from_prompt` in the
run summary).

Ordered by mechanism coverage per unit of risk. Each item: what it conditions · why here · evidence · metric risk ·
test.

1. **Restore the two-step structure to the non-FSSAI domain prompts (S3).**
   The structure is the FSSAI system prompt's workflow (`prompt_template.py:14-25`), verbatim in substance:
   "Work in two steps. Step 1 — quote: first extract the short passages that bear on the question, quoting them
   verbatim. Step 2 — answer: give a concise, legally precise answer derived strictly from those quotes." — plus the
   three-rule citation contract (every material legal claim carries ≥1 `[n]`; each `[n]` maps to a source shown;
   never fabricate) and the qualified-unknown clause ("state so clearly and qualify what is unknown instead of
   guessing"). The other six domain prompts (`:33-83`) are single-instruction paragraphs — role, "Answer using ONLY
   the provided context… Cite sources using `[n]` markers… If the answer is not in the context, state so clearly…
   Never fabricate facts… Keep answers concise and legally precise" — with **no quote step, no verbatim-extraction
   instruction, and no numbered contract**.
   Conditions: domain vocabulary + quote-then-answer for `env`/`commercial`/`animal`/`wb_state`/`criminal`/`general`.
   Advantages vs the existing implementation:
   - **Mechanism parity with the only measured configuration.** Every SPEC-1/ADR-0010 experiment ran with the FSSAI
     two-step system prompt present; the *shared user template* carries the scan line to all domains, but
     "extract verbatim, answer strictly from the quotes" exists only under `fssai` — six of seven domains run the
     generator without the quote-first primitive the measurements validated.
   - **First-party grounding primitive.** Quote-before-task is the vendor-endorsed grounding pattern (C1: pulling
     quotes "is helpful in all head-to-head comparisons … improves accuracy"; C2: "quote relevant parts of the
     documents first before carrying out its task"). The current domain prompts omit it.
   - **Direct mechanism on the `provision_check` slice (§7.2):** requiring the relied-on passages to be named before
     answering attacks "governing provision not quoted/verified" at its source, and it is the anti-definition-anchor
     move available without the evidence selector — the model must state which passage it answers from.
   - **Zero new output tokens, no mandated format.** It reorders the single answer rather than emitting a scratchpad,
     so it does not compete for the 1024-token budget (§1.3) and adds no prescribed closing format — the shape whose
     abstention cost sank `cand_scan_enumerate` (§4.3.1).
   What it does not fix: nothing in the 24-question `abstention_gate` slice (§7.2); no legal content beyond the
   shared scan line; `abstained`/`false_abstention` are still the metrics to report (a quote-first *wording* change
   moved missed-abstentions 8/21 → 19/21 in ADR-0010 §2.5, `:124-151`).
   Why first: it is a *consistency* fix with zero new instruction risk — the structure already measured positive in
   the FSSAI domain, and six domains silently lack it (`prompt_template.py:33-83` vs `:14-25`).
   Evidence: C1/C2 (quote-first is first-party-endorsed), §4.2 (the FSSAI measurement).
   Risk: none measured; it touches no template on the default path (production renders `fssai`).
   Test: render every domain and assert the two-step contract is present; no LLM call needed.

2. **Turn the context-side role annotation on behind its existing flag, and run the free instrument (S4).**
   Conditions: role metadata (`primary_provision` / `exception` / `definition` / `penalty_provision` /
   `cross_reference`) and role-ordered packing, which is the anti-definition-anchoring mechanism by design
   (`context_builder.py:25-34`, `:341-354`).
   Why here: it is the **only conditioning that spends no output tokens** (§1.3), it targets the largest audit
   bucket (`provision_check`, n=35 — `docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:182`), and it is a
   prerequisite for any role-conditioned directive (the proposal's "Document Roles" preamble, `:265-270`, currently
   has no roles to reference).
   Evidence: C2 (per-document XML metadata pattern), C7 (the gap is *selection*, which roles address structurally),
   §4.2 (the scan line's mechanism is "look at the rest of the window", which role tags reinforce).
   Risk: `citation_precision` (narrowing the window can drop gold), `is_padded` (reordering is breadth-neutral), plus
   the flag's blast radius — "Land behind `ENABLE_EVIDENCE_SELECTOR` (default false). This changes every live prompt
   when on" (`docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:314`).
   Test: `python -m evaluation.bench_p0_1_packing` first (no LLM), then
   `python -m evaluation.ab_p0_1_packing` for the live arm (`evaluation/ab_p0_1_packing.py:8-11`).

3. **One additive, non-formatting directive aimed at proviso+penalty completeness (S2 + S6).**
   Conditions: a single sentence requiring the qualifier and the full penalty structure to be stated with their own
   citations — no emitted tiers, no mandatory closing line.
   Why here: it is the smallest change with a mechanism against **two** audit modes (proviso blindness, penalty
   incompleteness, plus polarity inversion via "an unproven prohibition is not a permission" —
   `docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md:277-279`), and it stays inside the surface that produced the
   only positive prompt measurement in this repo.
   Evidence: C2/C3 (quote + reasoning-before-conclusion ordering), §4.2 (additive wording lifted recall +0.076 with
   flat precision).
   Risk: **`abstained`/`false_abstention`** is the binding risk — this is exactly where `cand_scan_enumerate`
   regressed (p=0.004); keep it additive, never a precondition.
   Test: §8.1 registration + ≥3 replicates; gates per §8.2.

4. **Query-type conditioned directives, gated by the existing 14 types (S5).**
   Conditions: per-type instructions (penalty → base + continuing/daily fine + escalation; exception → quote the
   proviso; procedure → authority/deadline/Form), selected on the `query_type` that generation already receives
   but the template never sees (`grounded_service.py:126`, `:129`).
   Why here, not first: it requires plumbing `query_type` (and optionally the role map) into
   `PromptTemplate.render` — a code change, hence after the flag-backed context lever — but it is the highest
   *targeting* value per token because it changes wording only where the type demands it.
   Evidence: C3 (ordering/targeting), C4 (prefer targeted general instruction over one prescriptive script for all
   queries), §4.2.
   Risk: low per-query-type abstention risk (fewer queries get new wording), plus the new one-shot risk that
   `insufficient-evidence`/`ambiguous` types get wording that invites refusal.
   Test: register one candidate per type, or one candidate parameterised on type; run the same gates; report
   per-type, not pooled.

5. **Only then consider the IRAC scratchpad (S8), and prefer its out-of-band form (S7) over an emitted scratchpad.**
   Conditions: full IRAC reasoning — either inside the answer (S2 with prescribed tiers) or as a separate
   `StructuredReasoner` pass feeding the answer (`structured_reasoner.py:24-73`).
   Why last: it is the largest change to the only live surface, it conflicts with the 1024-token budget when
   emitted (§1.3), and its target metric is unmeasurable cheaply (§8.2). The out-of-band form removes the budget
   conflict and reuses existing code, at +1 LLM call/query; the emitted form is preferred only if latency/cost
   forbids a second call.
   Evidence: C10 is the only supporting paper and does not transfer (§6.6); C4 argues against prescribing steps;
   §4.3.1/§4.3.2 are the repo's own negative results on this class.
   Risk: `abstained`, `is_padded` (tier 2 asks for *all* provisos across ten sources), `citation_precision`.
   Test: same gates **plus** an explicit "application correctness unmeasured" note; do not adopt on one replicate.

**Do not lead with:** the IRAC/dual-tier scratchpad (S8), the auditor, or any emitted-structure variant — per
"Prompt / generation changes (low measured impact; do NOT lead with this)"
(`docs/RAG_UPGRADE_RESEARCH.md:87-92`), the abstention history (§4.1, §4.3.1), and the fact that the largest audit
bucket (`provision_check`) is better addressed structurally by S4 than rhetorically by S8.

---

## 10. Sources

### Primary — repo (read first; the measured ground truth for this decision)
- `app/rag/generation/prompt_template.py` — `:14-25` FSSAI system prompt with the two-step contract; `:31-84`
  `DOMAIN_SYSTEM_PROMPTS` (only `fssai` is two-step); `:86-121` user template + the SPEC-1 measurement comment
  `:91-112`; `:156-160` / `:164-189` domain and `extra_vars` selection.
- `app/rag/generation/grounded_service.py` — `:98-132` generate path (`query_type`/`evidence_set` in, prompt render
  out); `:209-211` `_build_context` forwarding; `:216-219` `_render_prompt` (no type, no role, no domain);
  `:225-228` `_call_llm` with default budget.
- `app/rag/generation/context_builder.py` — `:25-45` `_PACK_ORDER` and the anti-definition-anchoring rationale;
  `:96-113` per-query-type budgets; `:174-177` role extraction and packing; `:300-321` `_roles_from_evidence_set`;
  `:323-339` `_pack_by_role`; `:341-354` `_format_entry` role attribute (omitted when no evidence set).
- `app/rag/retrieval/evidence_selector.py` — `:36-44` evidence-type constants; `:343-353` `_EVIDENCE_TYPE_PRIORITY`.
- `app/rag/retrieval/legal_query_classifier.py` — `:42-57` the 14 canonical query types.
- `app/shared/config.py` — `:195-201` `ENABLE_EVIDENCE_SELECTOR` False; `:202-208` `ENABLE_STRUCTURED_REASONER`
  False; `:209-215` `ENABLE_LEGAL_AUDITOR` False.
- `app/rag/tasks.py` — `:505-514` `run_generation_pipeline(query, chunks, query_type, …, evidence_set)`;
  `:525-528` evidence_set honoured only when the selector flag is on.
- `app/rag/generation/structured_reasoner.py` — `:24-36` reasoner system prompt; `:39-47` `ConditionEvaluation`
  (satisfied / not_satisfied / unknown); `:49-62` the IRAC-shaped IR; `:70-73` prompt builder.
- `app/rag/generation/llm_client.py` — `:124-132` `temperature=0.1, max_tokens=1024`.
- `docs/adr/0010-shadow-verifier-threshold-calibration.md` — §7.1 `:284-360` (reverted quote gate, shipped scan line,
  honest caveats); §2.5 `:118-166`; §3 `:196-243`; §9.4 `:470-486`; §10 `:490-506`; §9.5 `:508-545`.
- `docs/RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md` — §5.13 `:242-304` (five failure modes `:250-254`, the four
  strategies `:257-279`, the V2 template `:281-304`); P1 row 7b `:326`; P0-1 row `:314`; audit targets `:182`;
  §5.8 `:212-216`; flags `:146-162`; dual-metric reporting `:316`.
- `docs/RAG_UPGRADE_RESEARCH.md` — `:87-92` "Prompt / generation changes (low measured impact; do NOT lead with
  this)"; `:26-30` Experiments E/F.
- `evaluation/ab_baseline_citation.py` — `:150-224` candidate templates; `:227-231` abstention regex;
  `:295-320` metric implementations; `:342-349` CLI.
- `evaluation/failure_attribution.py` + `evaluation/out/ceiling_v5/failure_attribution.json` — the 60/26/3
  attribution and the `by_model_action` cross-tab used in §7; `evaluation/out/ceiling_v5/full_review_tabulation.md`
  — verdicts (89 `model_wrong` / 57 `evaluator_miss` / 4 `reference_narrow`) and audit targets (31/35/23).
- `evaluation/compare_citation_variants.py` — `:23-33` gates and their rationale; `:50-51` lower-is-better set;
  `:236-259` gate logic; `:274-281` CLI.
- `evaluation/bench_p0_1_packing.py` — `:239-243` the no-LLM prompt-effect instrument and its stated limit.
- `evaluation/ab_p0_1_packing.py` — `:8-11` the live-LLM `packing_off`/`packing_on` arms.
- `tests/test_baseline_citation_enforcement.py` — `:60-76` and `:127-181` the candidate constraints;
  `:86-108` the shipped-template pins.
- `evaluation/README.md` — artefact schemas (note: does not define the metrics).
- `docs/QUERY_DECOMPOSITION_PROMPT_RESEARCH.md` — secondary; `:36` corrected in §5.3, `:63-79` and `:113` reused
  for the XML/structured-output context.

### Primary — web (first-party only)
- https://www.anthropic.com/research/prompting-long-context — Anthropic, *Prompt engineering for Claude's long
  context window* (2023-09-23): `<scratchpad>` = quote extraction; "helpful in all head-to-head comparisons. It
  comes at a small cost to latency, but improves accuracy"; end-of-input recall can degrade with added examples.
- https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices —
  Anthropic, *Prompting best practices*: "Ground responses in quotes … before carrying out its task";
  per-document `<document index>` + `<source>` structuring; "Prefer general instructions over prescriptive steps";
  "Thinking adds latency and should only be used when it will meaningfully improve answer quality";
  model-specific-guidance caveat.
- https://developers.openai.com/api/docs/guides/prompt-engineering — OpenAI, *Prompt engineering*: developer-message
  ordering (identity → instructions → examples → context); reasoning-vs-GPT-model guidance.
- https://developers.openai.com/api/docs/guides/prompt-generation — OpenAI, *Prompt generation* meta-prompt
  guidance: "Reasoning Before Conclusions … Conclusion, classifications, or results should ALWAYS appear last."
- https://arxiv.org/abs/2201.11903 — Wei et al., *Chain-of-Thought Prompting Elicits Reasoning in Large Language
  Models*: arithmetic / commonsense / symbolic gains; no legal task, no retrieval.
- https://arxiv.org/abs/2212.01326 ; https://arxiv.org/html/2212.01326v2 — Yu, Quartey & Schilder, *Legal Prompting*:
  COLIEE task 4 binary entailment; Table 4 (TRRAC 0.8148/0.6881, IRAC 0.7284/0.6881, IRREAC/IRRAC 0.7156);
  `S4.T1` ZS 0.7407 (2021); `S4.T2` 8-shot 0.7531/0.7432; `S4.T3` ZS-CoT 0.6296/0.7064; 81 and 109 test questions;
  §5 overfitting caveat; Appendix A worked explanations.
- https://www.jld.edu.au/article/download/229/229-771-1-PB.pdf (ERIC EJ1134620) — Burton, *"'Think Like a Lawyer' …
  IRAC"*, Journal of Learning Design 10(2), 2017: pedagogy/assessment of students; no LLM evidence.
- https://eric.ed.gov/?id=EJ1124439 — Burton, *JUTLP* 13(5), 2016: IRAC as the dominant 40-year legal-education
  structure.
- https://www.law.columbia.edu/sites/default/files/2024-09/WC%20Handout%20IRAC%2C%20CRAC%2C%20CREAC.revised%209.24.pdf
  — Columbia Law Writing Center: rule section as a "funnel" with exceptions after the broad principles; each
  discrete topic gets its own IRAC.
- https://www.law.berkeley.edu/wp-content/uploads/archive/2021/08/IRAC-Handout-ASP-website.pdf ;
  https://law.syracuse.edu/wp-content/uploads/IRAC.pdf — Berkeley Law ASP and Syracuse University legal-writing
  handouts (exam/memo structure; no LLM findings).

### Primary — sought and not found
- A peer-reviewed or first-party paper evaluating a rule+exceptions dual-tier scratchpad on grounded legal QA with
  citation metrics.
- A first-party (Anthropic/OpenAI) recommendation of IRAC-style tiering for legal answers; both authorise
  quote-grounding and reasoning-before-conclusion ordering only.
- A LegalBench prompting ablation establishing IRAC-style prompting gains (cited only as a task taxonomy).
- Any measurement, in this repo, of conditioning effects on the five audit failure modes themselves.
