# NLP-based Provision Extraction — Research & Implementation Reference

Status: **research complete** (input to the implementation kickoff).
Date: 2026-09-28. Supersedes nothing; complements `docs/provision-extraction-plan.md`
(the plan is the scope decision — this document is the technique evidence).

Every in-repo fact is cited `file:line`. Every external fact carries a verified URL
(all URLs fetched 2026-09-28; unverifiable items are flagged inline as
**UNVERIFIED**). Doc naming follows `docs/INDEX.md` (research docs live in `docs/`,
e.g. `RAG_UPGRADE_RESEARCH.md`, `QUERY_DECOMPOSITION_PROMPT_RESEARCH.md`).

---

## 1. Executive summary — recommendations at a glance

| # | Recommendation | One-line rationale |
|---|---|---|
| 1 | **Rules-first candidate generation + scikit-learn `LogisticRegression` disambiguator** (P0) | Deterministic fallback, explainable coefficients, runs in the numpy/scipy-only venv-adjacent system Python (sklearn 1.9.1), directly targets the four documented failure modes. |
| 2 | **CRF sequence labeling (BIO) via `sklearn-crfsuite`** (P1) | Models label transitions (monotonic section numbering) that per-candidate LR cannot; verified pip-installable (0.5.0, 10 kB wheel); minutes-scale CPU training on 27k chunks; joblib-serializable. |
| 3 | **LLM-assisted review only (OpenRouter free tier), never batch extraction** (P2) | Free tier is 20 req/min, 50 req/day → one corpus pass (27,361 chunks) would take ~25 days minimum; viable only for adjudicating a few hundred borderline candidates. |

Transformers (LEGAL-BERT et al.): **optional P2 candidate ranker, not core** — the
pretraining corpora contain zero Indian statutes (§4.3), and the repo already has a
sentence-transformers embedding stack (`.env.example:240`) that covers the ranking
need at lower risk.

spaCy/Stanza: **skip** — not installed, jurisdiction-mismatched trained models, and
the transition-based NER objective (whole-entity accuracy) is the wrong loss for
boundary detection (§4.2).

---

## 2. Current-state assessment (verified in-repo)

### 2.1 What exists today

- **Rule-first detection, three layers:**
  1. `LegalParagraphEngine` section patterns — `SECTION_PATTERNS`
     (`legal_paragraph_detection_engine/src/parsers/section_parser.py:51-109`;
     main/subsection/subsubsection/paragraph/roman/combined marker-chain forms),
     title patterns (`:112-118`), hierarchy markers (`:121-135`). Marker chains
     (`(1)(a)`) correctly carry **no** section number (`:273-289`, spec F-06a).
  2. L4 any-position header regex — `_L4_HEADER_RE`
     (`app/rag/chunker.py:215`), applied by `_l4_section_headers`
     (`app/rag/chunker.py:218-238`), **fail-closed**: unknown act → `[]` via
     `sections_for_act` (`app/rag/legal_sections.py:64-83`).
  3. Dotted clause numbers for regulations/rules — `_DOTTED_CLAUSE_RE`
     (`app/rag/chunker.py:361`), validated against the corpus to exclude dates,
     measurements, ranges (`:351-361` comment).
- **Act-range gating:** `ACT_SECTION_RANGES` (`app/rag/legal_sections.py:31-52`) —
  15 acts; unknown acts are never guessed (`:10-13` docstring).
- **Payload schema:** `PAYLOAD_INDEX_FIELDS` (`app/rag/chunker.py:29-43`) —
  `section_number`, `section_title`, `subsection`, `clause_number`,
  `hierarchy_level`, `sections_covered` (`:96-100`), `confidence` (`:205`).
- **Corpus:** `evaluation/out/cache/payload_index.jsonl` — 27,361 chunks (verified
  `wc -l`); payloads already carry `instrument_id`, `legal_domain`, `status`
  (stamped per `kg/payload_identity.py:49`).
- **KG:** `build_provisions` (`kg/corpus_ingestion.py:572-628`) groups chunks by
  validated `section_number` into `{instrument_id}_SEC_{n}` provisions
  (`:618`), text capped at 2000 chars (`:621`), confidence 0.9 corpus / 0.6 stub
  (`:626`). `write_provisions` uses `MERGE ... ON CREATE/ON MATCH`
  (`kg/corpus_ingestion.py:862-878`) against the `provision_id` **uniqueness
  constraint** (`kg/schema.py:37-43` — LegalProvision/Section/Subsection/Clause/
  Schedule/RuleProvision/RegulationProvision all unique by `provision_id`).
- **Gold standard:** `benchmark/gold_provisions_v1.0.json` — 97 records
  (`evaluation/benchmark.py:143-151`); grammar `<family>:s<section>[(<sub>)]`
  parsed by `_section_from_id` (`evaluation/benchmark.py:234-241`); family
  resolution via `FamilyMap`/`_FAMILY_ALIASES` (`evaluation/resolution.py:54-78`,
  `:81-137`); chunk↔gold matching via `matches_gold`
  (`evaluation/resolution.py:223-238`) consulting `act_name` **and**
  `document_title` (`:199`, V5 fix) and `sections_covered` (`:213-219`, V7 fix).
- **Backfill patterns to mimic:** `QdrantPayloadStamper.plan()` (dry-run,
  `kg/payload_identity.py:198-243`) and `.stamp()` (idempotent, groups points by
  identical field-set, `set_payload` batches of 500, `:245-284`); CLI with
  `--dry-run/--collection/--limit/--no-indexes/--out-dir`
  (`scripts/stamp_qdrant_payload_identity.py:44-53`). Layered `source` stamps
  (`L4`, `L5_propagation`, `L7_correction`) in
  `scripts/backfill_payload_identity.py:770,805,830-831,851` — the provenance
  convention the plan's `source` field (`candidate_l4|engine|ml`,
  `docs/provision-extraction-plan.md:80-81`) extends.
- **Ingestion integration point:** `IngestionPipeline.ingest_text()` →
  `self.indexer.chunker.chunk_text(cleaned, meta)`
  (`app/rag/ingestion.py:258,300`).

### 2.2 Known failure modes (the design targets)

| Failure mode | Evidence |
|---|---|
| Page numbers, definition-list numbers, cross-references stamped as sections | `scripts/strip_reg_section_noise.py:1-12` — 1,518 regulation + 36 notification chunks stripped (2026-08-17); 298 rule chunks (2026-08-18) |
| Cross-reference numbers mistaken for sections | `benchmark/gold_provisions_v1.0.json` — `bda:s480` `gold_fix_note`: "480 was a CrPC cross-reference (s.480/482) … not a BDA section" |
| Year-like numbers | `_valid_section` junk rejection (`kg/corpus_ingestion.py:594`, via `app/rag/legal_sections.py:86-99`) |
| Merged/inline headings missed by line-anchored engine | L4 regex introduced for exactly this (`app/rag/chunker.py:211-215`); V7 gap analysis `evaluation/v7_gap_metadata.py:33-45` (G1 DOC_ABSENT … G11 GOLD_MAPPING) |
| Coverage ceiling | 82.4% of substantive chunks post-P1/P2 (`docs/archive/COVERAGE_COMPLETENESS.md:6,40,90`) |
| No sub-clause granularity in KG | `scripts/backfill_kg_provision_types.py:14-22` — 0/3,158 FSS chunk rows with sub-clause shapes; all 1,861 `provision_number` values digits-only |

### 2.3 Environment constraints (verified)

- Project venv: numpy/scipy only (`docs/provision-extraction-plan.md:22`).
- System Python: torch 2.14.0+cpu, sentence-transformers 6.1.0, **scikit-learn
  1.9.1**, transformers 5.17.0, qdrant-client, joblib; CPU only (`plan:23-25`).
- Test runner `/home/suman_saha/.venv-nsa` (`--system-site-packages`, pytest)
  (`plan:26-28`).
- No spaCy anywhere (`plan:29`). `OPENROUTER_API_KEY` is **unset**
  (`.env.example:198-201`).
- Raw statute PDFs absent; corpus-scale text exists only as payloads + live Qdrant
  (`plan:30-33`).
- `pip download sklearn-crfsuite` **succeeds from this build machine** (0.5.0,
  10 kB pure-Python wheel; `python-crfsuite` C-extension wheels exist for
  linux x86_64) — but the deploy venv is numpy/scipy-only, so any new dependency
  is a venv-change decision, not a given (§4.1, §9).

---

## 3. Technique comparison (decision matrix)

| Technique | Strengths for THIS project | Weaknesses for THIS project | Dependencies | Runtime (CPU) | Determinism | Fallback story |
|---|---|---|---|---|---|---|
| **Rules (today + extended)** | Zero deps; deterministic; already 82.4% coverage; fail-closed act gating; every failure mode above is a rule | Brittle to unseen numbering grammars; no confidence calibration | none | ~0 (regex) | Full | IS the fallback |
| **sklearn LogisticRegression on handcrafted features** | Runs in system Python today; coefficients = explainable evidence for legal review; `predict_proba` gives confidence tiers; joblib artifact | Per-candidate independence misses numbering-monotonicity signal; needs silver labels | scikit-learn 1.9.1 (present) | ms/1k candidates | Full (lbfgs, fixed seed) | Auto-fallback to rules if artifact absent/corrupt (`plan:68-70`) |
| **CRF (BIO sequence labeling, sklearn-crfsuite)** | Models transitions (B-SEC→I-SEC→O; monotonic numbers); native per-line sequence = exactly the unit the engine already parses; sklearn-compatible CV; joblib | New dependency in venv; needs BIO labels (derivable from silver stamps); slower than LR | sklearn-crfsuite 0.5.0 (pip-verified) | seconds–minutes train; ms/doc predict | Full (lbfgs) | Falls back to LR → rules |
| **Transformer fine-tune (LEGAL-BERT-small, 35M)** | Strongest published results on legal structure segmentation (Annotares: BERT/LLM > CRF > BiLSTM); 35M params ≈ 4× faster than base | Pretraining corpus = EU/UK/US law, **zero Indian statutes** (HF model card); needs head + training loop; overkill vs 97-record gold; license cc-by-sa-4.0 | torch + transformers (system Python only) | minutes–hours fine-tune on CPU | Full given seed | Not in critical path; optional ranker |
| **sentence-transformers candidate ranking** | Repo already runs `all-mpnet-base-v2` (`.env.example:240`); no new model; good for title↔candidate semantic match | Embedding similarity ≠ boundary evidence; adds latency | sentence-transformers (present) | ~ms/chunk | Full | Optional enhancer only |
| **LLM few-shot (OpenRouter free)** | No training data needed; strong on merged-heading disambiguation; natural-language rationales for review queue | 20 req/min, 50 req/day free tier → 27,361-chunk pass ≈ 25 days; nondeterministic; no API key currently set; legal hallucination risk | API key + internet | n/a (network) | **No** (temperature, model drift) | Review/adjudication only — never batch extraction |
| **spaCy/Stanza transition-based NER** | Mature transition-based NER; blackstone shows PROVISION entity precedent | Not installed; transition-based NER optimizes **whole-entity accuracy** — exactly the wrong loss when boundaries are hard (spaCy EntityRecognizer docs); blackstone is E&W case law, NER F1 ≈ 70%, jurisdiction-mismatched | spaCy + thinc (heavy venv change) | fast | Full | Skip — revisit only if P0/P1 labels exist |

---

## 4. Technique deep dives (external primary sources)

### 4.1 Sequence labeling for legal structure segmentation (BIO + CRF)

**What it is.** Each token/line gets a BIO label (`B-SEC`, `I-SEC`, `O`); a CRF
models P(labels | features) with transition features, so illegal sequences
(e.g. `I-SEC` without `B-SEC`, or a section number jumping 12 → 400) are
penalized. sklearn-crfsuite is a thin scikit-learn-compatible wrapper over
python-crfsuite: `CRF(algorithm='lbfgs', c1, c2, all_possible_transitions=True)`,
`fit/predict/predict_marginals`, joblib save/load, sklearn model-selection
utilities (verified: https://sklearn-crfsuite.readthedocs.io/en/latest/tutorial.html,
https://sklearn-crfsuite.readthedocs.io/en/latest/api.html,
https://github.com/TeamHG-Memex/sklearn-crfsuite).

**Why it fits.** The repo's unit of analysis is already the line/line-anchored
pattern (`section_parser.py:159-166` iterates lines; `hierarchy.py:210-266`
builds nodes per line). BIO labels fall out of existing stamps: a line whose
number lands in `sections_covered` = `B-SEC`, continuation lines = `O`. The CRF
captures what per-candidate LR cannot: **transition structure** — section
numbers are monotonic within an instrument, and `ACT_SECTION_RANGES`
(`app/rag/legal_sections.py:31-52`) bounds them.

**Published evidence it works on statutory text.** The Annotares benchmark
(German statutory texts, three codes) compares rule baseline vs CRF vs BiLSTM vs
BiLSTM-CRF vs BERT vs LLM on span segmentation of legal conditions/consequences:
CRF with token/POS/dependency features is the strong classical baseline; BERT
and LLM beat it, but CRF beats BiLSTM and the rule baseline; POS/DEP ablation
drops mF1 substantially (0.798→0.679 for mBERT) — i.e. **explicit structural
features matter even for transformers** (https://arxiv.org/html/2608.03898).
Semi-Markov CRFs (span-level) beat token CRFs on legal rhetorical-role
segmentation when spans are multi-sentence (https://arxiv.org/abs/2302.06448) —
relevant if P1 moves from line-level to span-level labels. Savelka & Ashley's CRF
segmentation of US court documents and Saravanan et al.'s CRF rhetorical-role
labeling are the classic precedents (cited in
https://aclanthology.org/2022.nllp-1.13.pdf).

**Cost.** Training: seconds–minutes on 27k short sequences, CPU. Model: joblib,
small. Determinism: lbfgs is deterministic given data order.

**Verdict:** P1 core. pip-installability verified from this machine; the only
question is whether the deploy venv may gain the dependency (§9).

### 4.2 spaCy/Stanza transition-based parsing — evaluated and set aside

**What it is.** Transition-based parsing maps structure prediction to a series
of state transitions (shift/reduce for dependencies; BIO-ish moves for NER) with
a neural state scorer (tok2vec + lower/upper subnetworks;
https://spacy.io/api/architectures). spaCy's `EntityRecognizer` is the NER
incarnation (https://spacy.io/api/entityrecognizer).

**Why it's out here.**
1. **Not installed**; spaCy pulls thinc + model wheels — a heavy venv change for
   a numpy/scipy-only runtime (`plan:22`).
2. **Wrong objective for boundaries:** spaCy's own docs warn the transition-based
   NER "loss function optimizes for whole entity accuracy, so if your
   inter-annotator agreement on boundary tokens is low, the component will
   likely perform poorly" (https://spacy.io/api/entityrecognizer). Provision
   boundary detection *is* a boundary-token problem (page numbers, merged
   headings — §2.2).
3. **Jurisdiction mismatch:** the only open legal PROVISION NER model,
   blackstone (ICLR&D), is trained on England & Wales case law (1865–2000),
   reports NER F1 ≈ 70%, and its data is proprietary
   (https://github.com/iclrandd/blackstone). It targets citations/instruments,
   not Indian statutory section segmentation.
4. **No training data:** a legal NER would need labeled spans we don't have yet —
   but P0/P1 silver labels (§8) would create them. Revisit spaCy only if a
   transition-based NER is wanted *after* labels exist.

Stanza: same verdict a fortiori (no install, no legal models for Indian
statutes).

### 4.3 Transformer approaches fine-tuned on legal structure

**What exists (verified):**
- **LEGAL-BERT** (Chalkidis et al., EMNLP-Findings 2020;
  https://arxiv.org/abs/2010.02559,
  https://aclanthology.org/2020.findings-emnlp.261/): family of BERT models
  pre-trained from scratch / further pre-trained on **12 GB of English legal
  text**. `legal-bert-small-uncased` = 35M params (33% of BERT-base), ~4× faster,
  license **cc-by-sa-4.0**
  (https://huggingface.co/nlpaueb/legal-bert-small-uncased). **Critical
  limitation:** the pretraining corpus is EURLEX (EU), legislation.gov.uk (UK),
  ECJ/ECHR (Europe), Case Law Access Project (US), EDGAR contracts (US) —
  **zero Indian statutes**. Transfer to Indian section numbering is unproven.
- **CaseHOLD / Custom Legal-BERT** (Zheng et al., ICAIL 2021;
  https://arxiv.org/abs/2104.08671, https://github.com/reglab/casehold): 53k+
  US holding-classification questions; domain pretraining helps **only when the
  task is domain-specific** (CaseHOLD +7.2% F1; Terms-of-Service ≈ 0). Lesson for
  us: pretraining corpus must match the task domain — US/EU case law ≠ Indian
  statutory structure.
- **Indian legal NLP (verified):** ILDC — 35k Indian Supreme Court cases
  (Malik et al., ACL-IJCNLP 2021; no stable URL verified — **UNVERIFIED**);
  NyayaAnumana — 702,945 preprocessed Indian cases + INLegalLlama
  (https://aclanthology.org/2025.coling-main.738/); LegalSeg — 7,000+ Indian
  judgments, 1.4M sentences, 7 rhetorical roles
  (https://aclanthology.org/anthology-files/pdf/findings/2025.findings-naacl.63.pdf).
  All are **judgment** corpora, not statute-structure corpora.
- **"LIVECourt":** **UNVERIFIED** — no model/paper by that name could be
  confirmed in search; do not cite.
- **SemEval-2023 Task 6 (LIVECourt-adjacent legal RRL):** winning systems use
  LEGAL-BERT + hierarchical BiLSTM + CRF
  (https://aclanthology.org/2023.semeval-1.286.pdf) — confirms the CRF-as-final-
 -layer pattern for legal sequence labeling.

**Headless CPU fine-tuning realism.** `legal-bert-small` (35M) fine-tuned as a
token classifier on a few hundred labeled sequences: feasible on CPU (minutes to
an hour) with torch 2.14.0+cpu present. But the label supply is the binding
constraint, not compute: gold = 97 records; silver = existing stamps (§8). A
transformer fine-tune on silver labels would mostly re-learn what LR/CRF learn
from the same features, without the Indian-domain pretraining to justify it.

**Verdict:** optional P2 ranker (candidate ↔ section-title semantic match) or
P2 review assistant; never the P0/P1 boundary decision-maker. If a transformer
is ever fine-tuned, prefer **span-level** formulation (semi-Markov CRF head or
token classification with BIO) per the Annotares/SemEval evidence.

### 4.4 Few-shot / LLM-based extraction + OpenRouter viability math

**What it is.** Prompt an LLM with a few labeled examples + the chunk text; ask
for structured JSON (boundary yes/no, section number, title, rationale).

**Verified rate limits (OpenRouter):** free-tier (`:free` variant) accounts
with <10 credits purchased: **20 requests/min, 50 requests/day**; ≥10 credits:
20 req/min, 1000 req/day; paid models: no platform cap
(https://openrouter.ai/docs/api_reference/limits,
https://openrouter.ai/docs/faq, https://openrouter.ai/pricing — 25+ free
models). 429s require exponential backoff; free capacity is provider-contributed
and can vanish.

**Viability math for this project:**
- Corpus-scale extraction: 27,361 chunks → at 50 req/day ≈ **547 days**; even at
  1000 req/day ≈ **28 days**, with nondeterministic output. **Not viable.**
- Review/adjudication: the disambiguator's borderline band (P ∈ [0.3, 0.7]) is
  realistically a few hundred candidates per corpus pass → 1–2 days at free-tier
  limits, or hours with ≥10 credits. **Viable as a review tool only.**
- No API key is currently set (`.env.example:198-201`) — a prerequisite, and a
  secrets-handling decision.

**Fit for constraints:** nondeterminism is disqualifying for the write path
(payload/KG must be reproducible — cf. `config_hash()` reproducibility convention,
`evaluation/config.py:138-141`). Hallucinated section numbers would violate the
fail-closed principle (`app/rag/legal_sections.py:10-13`).

**Verdict:** P2 review-queue adjudicator with structured-output prompts,
temperature 0, and every accepted LLM boundary re-validated against
`ACT_SECTION_RANGES` + monotonicity rules before any write. Never a batch
extractor.

### 4.5 Hybrid rule+ML architectures and open-source legal NLP tools

- **blackstone** (https://github.com/iclrandd/blackstone): spaCy pipeline +
  PROVISION entity (`section 1`, `art 2(3)`) + Legislation Linker coupling
  PROVISION→INSTRUMENT. Architecture precedent for provision NER, but E&W case
  law, F1 ≈ 70%, proprietary training data. Not reusable code; reusable idea
  (provision entity + instrument linker).
- **nyaayaIN/indian-laws-akns** (https://github.com/nyaayaIN/indian-laws-akns):
  Indian laws in Akoma Ntoso XML (Vidhi Centre). A **structure donor**: chapter/
  section/subsection hierarchy for Indian statutes, usable as a prior for
  candidate validation (not as corpus text — our corpus is fixed).
- **GSMS-B/indian-legal-sections-bns-bnss-bsa-2023**
  (https://huggingface.co/datasets/GSMS-B/indian-legal-sections-bns-bsa-2023):
  1,059 fully structured BNS/BNSS/BSA 2023 sections (358+531+170) with
  `section_number`, `section_title`, `chapter`, parsed via **rule-based PDF
  chunking** — a directly relevant training-data donor for the `bns` gold
  family (gold has `bns` provisions; `ACT_SECTION_RANGES` covers BNS 1–358,
  `app/rag/legal_sections.py:51`).
- **Hybrid architecture evidence:** Annotares' rule baseline vs CRF vs neural
  (§4.1) and the label-shift auxiliary-task results
  (https://aclanthology.org/2022.nllp-1.13.pdf — 88% label inertia between
  consecutive sentences) both support the repo's planned hybrid: rules propose,
  ML disambiguates, structure priors constrain. The "Law of Large Documents"
  work adds that **visual/layout cues** improve section splitting
  (https://ar5iv.labs.arxiv.org/html/2107.08128) — noted as out of scope (no PDFs,
  `plan:142-143`) but a reminder that text-only boundary detection has a ceiling.

### 4.6 Evaluation methodology for boundary detection

- **Boundary precision/recall/F1 at section level** — the repo's plan metric
  (`plan:113-114`); match on `(family, section)` after `_section_from_id`
  (`evaluation/benchmark.py:234-241`).
- **Exact-span boundary-F1** — span-level exact match, the Annotares convention
  (https://arxiv.org/html/2608.03898); stricter than section-level, catches
  merged-heading splits.
- **Pk metric** — segmentation error rate (Beeferman et al. 1999), adopted by
  legal structural segmentation work
  (https://arxiv.org/pdf/2012.03619, §3.3); useful as a document-level secondary
  metric, less interpretable for legal review.
- **Gold-resolution coverage** — `matches_gold`/`gold_in_corpus`
  (`evaluation/resolution.py:223-256`) over the 97-record registry; the plan's
  gate (`plan:115-117`).
- **Statistical rigor conventions already in-repo:** gains 2.0/1.0/0.0
  (`evaluation/config.py:39-41`), 10,000-iteration paired bootstrap with fixed
  seed (`:44-45`), McNemar α=0.05 (`:46`), implemented at
  `evaluation/metrics.py:396,420`. Any new boundary metric should report
  bootstrap CIs the same way.
- **Regression discipline:** no regression in the 350-test baseline + new
  fixture tests green (`plan:118`).

---

## 5. Error taxonomy → technique mitigation map

Structured from the repo's documented failure modes (§2.2) and the V7 gap
taxonomy (`evaluation/v7_gap_metadata.py:33-45`):

| Error class | Examples (repo evidence) | Mitigator | Layer |
|---|---|---|---|
| E1 Page/line numbers | `41`, `01 -` stamped as sections (`strip_reg_section_noise.py:1-12`) | Running-head detector (repeated token at fixed offset across chunks of a doc); page-number regex; position feature | rules + features |
| E2 Cross-references | `section 23 of FSS Act` mid-sentence; CrPC s.480 → `bda:s480` (`gold_fix_note`) | Cross-ref density feature; mid-sentence position penalty; `section \d+ of` context rule | features + LR/CRF |
| E3 Year-like numbers | 1900–2100 tokens (`_valid_section` junk rejection) | Year-range rule (keep — it works) | rules |
| E4 Merged/inline headings | `…coercion. 73. Compensation…` (`chunker.py:211-215`) | L4 regex (exists) + CRF transition model (number after sentence-final period) + LLM review for hard cases | L4 + CRF + P2 review |
| E5 Monotonicity violations | number regressions (12 → 5) | CRF transition features; delta-from-previous-accepted feature | CRF + features |
| E6 Unknown-act guessing | fail-closed `[]` (`chunker.py:228-230`) | Keep fail-closed; registry extensible per manifest (`plan:144`) | rules (unchanged) |
| E7 Wrong granularity (subsection vs section) | `subsection` marker chains (`chunker.py:339-348`); 0/3,158 sub-clause shapes (`backfill_kg_provision_types.py:14-22`) | Grammar-aware candidate typing (digits vs dotted vs marker-chain); `classify_provision_type` shape mapping (`backfill_kg_provision_types.py:70-83`) | rules + registry |
| E8 Noise inside provision spans | running heads, chapter banners | Span cleaner (strip + record banners as parents) — `plan:73-75` | isolator |

---

## 6. Recommended architecture — `app/rag/provision_extractor/`

Pipeline (per `plan:42-47`), with repo touchpoints:

```
candidates → features → disambiguator → isolator → payload/KG writers
   │             │              │            │
   │             │              │            └─→ ProvisionRecord (models.py)
   │             │              └─→ sklearn LR | CRF | rules (lazy imports)
   │             └─→ ~15 handcrafted features (§7)
   └─→ grammar-aware boundary proposals (reuse existing regexes)
```

| Stage | Module | Reuses (do not rewrite) | Emits |
|---|---|---|---|
| Candidate generation | `candidates.py` | `SECTION_PATTERNS` (`section_parser.py:51-109`), `_L4_HEADER_RE` (`chunker.py:215`), `_DOTTED_CLAUSE_RE` (`chunker.py:361`), `TEXT_BOUNDARY_PATTERNS` (`hierarchy.py:163-170`), marker-chain rules (`section_parser.py:242-271`) | `(doc_id, char_offset, source_pattern, raw_number, grammar_type)` |
| Feature extraction | `features.py` | `ACT_SECTION_RANGES` membership (`legal_sections.py:31-52`), payload fields (`chunker.py:29-43`), `sections_covered` (`chunker.py:100`), `hierarchy_level` (`chunker.py:200`), engine `confidence` (`chunker.py:205`) | feature vector per candidate (§7) |
| Disambiguation | `disambiguator.py` | sklearn LR (P0) → CRF (P1) → rules; `PROVISION_EXTRACTOR_MODE=rules\|hybrid` (`plan:66-70`); lazy sklearn import with auto-fallback (`plan:68-70`) | P(boundary) + confidence tier |
| Isolation | `isolator.py` | noise patterns from `strip_reg_section_noise.py`; banner-as-parent (`plan:73-75`); `classify_provision_type` shape map (`backfill_kg_provision_types.py:70-83`) | `ProvisionRecord` (`plan:76-79`) |
| Registry | `registry.py` | `FamilyMap`/`_FAMILY_ALIASES` (`resolution.py:54-78`), `DOCUMENT_TO_INSTRUMENT` (`corpus_ingestion.py:62-71`), `norm_act_name` (`resolution.py:34-39`) | family token per document; gold-grammar ids |
| KG feed | (wire into `build_provisions`) | `_valid_section`/`_clean_section` checks (`corpus_ingestion.py:588-594`); `MERGE` on `provision_id` (`:862-878`); uniqueness constraints (`schema.py:37-43`) | provision rows replacing the grouping heuristic (`plan:102-103`) |
| Payload writers | backfill adapter | `QdrantPayloadStamper.plan/stamp` pattern (`payload_identity.py:198-284`); `source` stamps (`backfill_payload_identity.py:770,805,851`); chunk-id stability (`plan:95-96`) | idempotent `provision_spans`/`provision_confidence` fields |

**Config flags** (declared in `app/shared/config.py` + `.env.example`, per
`plan:66-67`): `PROVISION_EXTRACTOR_ENABLED`, `PROVISION_EXTRACTOR_MODE`
(`rules|hybrid`), `PROVISION_EXTRACTOR_MODEL_PATH`
(`models/provision_boundaries.joblib`), `PROVISION_EXTRACTOR_REVIEW_THRESHOLD`
(borderline band for the P2 review queue).

---

## 7. Feature engineering (concrete, extractable from payloads + chunk text)

Per candidate, computable from `payload_index.jsonl` payloads and ordered chunk
text — no new data collection:

| # | Feature | Source / computation | Targets |
|---|---|---|---|
| 1 | `is_line_start` | match offset == 0 (or after `\n`) in `chunk_text` | E4 |
| 2 | `source_pattern` (categorical) | which regex produced it: `engine_main`, `engine_subsection`, `l4_header`, `dotted_clause`, `marker_chain` | all |
| 3 | `grammar_type` (categorical) | digits-only / dotted (`2.4.15`) / parenthetical (`31(2)`) / marker-chain (`(1)(a)`) | E7 |
| 4 | `in_act_range` | `is_known_section_for_act(n, act_name)` → True/False/None (`legal_sections.py:86-99`); None = unknown act → fail-closed | E6 |
| 5 | `number_value` | int(raw number) | E5 |
| 6 | `delta_from_prev_accepted` | n − previous accepted number in doc order (1 = textbook) | E5 |
| 7 | `is_year_like` | 1900 ≤ n ≤ 2100 | E3 |
| 8 | `page_number_likelihood` | running-head pattern (`Act 29 of 1986 … 269`); token repeated at similar offset in ≥3 chunks of same doc | E1 |
| 9 | `crossref_density` | count of `section \d+`/`rule \d+`/`regulation \d+` in ±200-char window, mid-sentence only | E2 |
| 10 | `title_score` | capitalized words / ALL-CAPS / `Title.—` em-dash form after the number (`TITLE_PATTERNS`, `section_parser.py:112-118`) | E4 |
| 11 | `first_occurrence_distance` | char offset of first candidate with this number (dedup vs re-mention) | E2 |
| 12 | `hierarchy_level` | engine stamp (`chunker.py:200`) | E7 |
| 13 | `sections_covered_agree` | candidate number ∈ payload `sections_covered` (`chunker.py:100`) | E4 |
| 14 | `document_type` (categorical) | act vs regulation/rule/notification — regulation/rule identity is `clause_number`, never section (`strip_reg_section_noise.py:13-19`) | E2/E7 |
| 15 | `engine_confidence` | payload `confidence` (`chunker.py:205`) | all |
| 16 | `following_text_chars` | chars until next candidate (short = heading-like) | E4 |

For the CRF (P1), per-line token features: word shape (lower/upper/digit/punct),
is-number, number value, casing, prev/next token features (±2 window), line
position in chunk, and the features above as sequence-level context.

---

## 8. Training-data strategy

**Label supply (in priority order):**
1. **Gold (97 records)** — `gold_provisions_v1.0.json`; expand each
   `(family, section)` to covering chunks via `payload_to_keys`/`matches_gold`
   (`resolution.py:173-238`). Small but authoritative; use for eval + final
   fine-tune slice, not for bootstrap training.
2. **Silver positives** — chunks with `section_number` present, `sections_covered`
   non-empty, and source ≠ `L4_override` (the high-confidence subset; the plan's
   criterion, `plan:105-107`). Expected precision: high (these stamps survived
   the G8 strip-noise remediation).
3. **Silver negatives** — the strip-noise corpus itself: 1,518 regulation + 36
   notification + 298 rule chunks with bogus `section_number`
   (`strip_reg_section_noise.py:1-19`); year-like tokens; page numbers.
4. **Weak supervision** — rules-derived soft labels for every candidate
   (e.g. in-range + line-start + monotonic ⇒ positive weight 0.9).
5. **Donor datasets** — GSMS-B BNS/BNSS/BSA 1,059 structured sections
   (https://huggingface.co/datasets/GSMS-B/indian-legal-sections-bns-bnss-bsa-2023)
   for the `bns` family; nyaayaIN Akoma Ntoso XML
   (https://github.com/nyaayaIN/indian-laws-akns) as a structure prior for
   candidate validation. Donor data trains/validates the *grammar*, never
   overwrites corpus text (corpus-truthful rule,
   `kg/corpus_ingestion.py:15-21`).
6. **Active learning loop** — the backfill review queue: candidates with
   P(boundary) ∈ [0.3, 0.7] (or CRF marginal in the band) go to human review;
   corrections append to the training file; retrain weekly. This is the
   plan's "optional hand-labeled correction file" (`plan:107`) made systematic.

**Split discipline:** group by `document_id` (never by chunk) to avoid
leakage; hold out whole documents for the eval set; report per-family metrics
(Acts / dotted regulations / EPA all-caps) per `plan:113`.

---

## 9. Model choices, concrete pipelines, fallback ladder

### 9.1 M0 — rules (P0, zero ML)
Weighted sum of §7 features with hand-tuned weights; threshold tuned for
precision on silver data. Deterministic; this is the runtime fallback and the
baseline the ML must beat (`plan:134`).

### 9.2 M1 — LogisticRegression (P0)
```python
# scripts/train_provision_boundaries.py (sklearn imported lazily)
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
model = Pipeline([
    ("scale", StandardScaler()),
    ("lr", LogisticRegression(class_weight="balanced", C=0.5,
                              solver="lbfgs", max_iter=1000,
                              random_state=20260928)),
])
```
- `class_weight="balanced"` — positives (true boundaries) are the minority.
- `predict_proba` → confidence tiers: ≥0.8 auto-accept; 0.3–0.8 review band;
  <0.3 reject (or rules-veto).
- Artifact: `models/provision_boundaries.joblib` (joblib, per `plan:68-69`).
- Determinism: lbfgs + fixed `random_state`; record artifact hash in run config
  (mirrors `config_hash()`, `evaluation/config.py:138-141`).

### 9.3 M2 — CRF (P1)
```python
import sklearn_crfsuite
crf = sklearn_crfsuite.CRF(algorithm="lbfgs", c1=0.1, c2=0.1,
                            max_iterations=200,
                            all_possible_transitions=True)
```
- Labels: `B-SEC`, `I-SEC`, `O` per line (P1a) or per chunk (P1b).
- Transition features learn monotonicity + grammar legality (E5).
- `predict_marginals_single` → per-line boundary probability for the review
  band.
- Dependency decision: `sklearn-crfsuite==0.5.0` pip-verified from this
  machine; **the deploy venv must be extended** (or the wheel vendored) —
  flag as an explicit P1 kickoff decision. If the venv cannot change, M2
  degrades to M1 and the roadmap slips.

### 9.4 M3 — transformer (P2, optional)
- Ranker: `sentence-transformers/all-mpnet-base-v2` (already the repo embedding
  model, `.env.example:240`) — cosine(candidate context, section-title
  candidates) as a feature, not a decision.
- Fine-tune (only if P1 labels exist): `nlpaueb/legal-bert-small-uncased`
  token classification, BIO, cc-by-sa-4.0 (compatible with this research repo;
  recheck before any redistribution).

### 9.5 Fallback ladder (write path)
```
CRF/LR artifact present & mode=hybrid → ML decision
  ↓ artifact absent/corrupt/ImportError
rules decision (never blocks ingestion — plan:68-70)
  ↓ P(boundary) in review band
human review queue (P2, LLM-assisted adjudication optional)
```
Every emitted `ProvisionRecord` carries `source` (`candidate_l4|engine|ml|review`)
+ `confidence`, mirroring the `L4_override` convention
(`backfill_payload_identity.py:770,805,851`) and the plan's provenance field
(`plan:80-81`).

---

## 10. Backfill execution plan — `scripts/backfill_provision_extraction.py`

Modeled on `scripts/stamp_qdrant_payload_identity.py` + `kg/payload_identity.py`:

1. **Inputs:** `evaluation/out/cache/payload_index.jsonl` (frozen cache) or live
   Qdrant scroll; group by `document_id`, order by `chunk_index` (`plan:98-99`).
2. **Extract + isolate** per document → `ProvisionRecord`s.
3. **Validate:** gold-grammar round-trip (`_section_from_id`,
   `benchmark.py:234-241`); `ACT_SECTION_RANGES` membership; monotonicity;
   `provision_id` uniqueness vs `schema.py:37-43`.
4. **Dry-run first (mandatory):** `plan()`-style diff report — per-collection
   stats, planned field updates keyed by point id (`payload_identity.py:198-243`),
   written to `reports/` (`stamp_qdrant_payload_identity.py:76-79`). Review the
   diff before any write.
5. **Idempotent writes:** only missing/different fields
   (`payload_identity.py:236,264`); group points by identical field-set;
   `set_payload` batches of 500 (`:271-278`); `chunk_id`s untouched
   (`plan:95-96`); vectors untouched.
6. **Provenance:** `source` + `confidence` on every write; confidence tiers
   0.9/0.6 mirroring `corpus_ingestion.py:626`.
7. **Reconciliation with Neo4j:** extracted provisions feed
   `build_provisions` (`corpus_ingestion.py:572-628`) keeping its
   `_valid_section` fail-closed checks; `MERGE` (not CREATE) on
   `provision_id` (`:865`); `NEO4J_ALLOW_WRITE=1` guard respected
   (`schema.py:196-200`); reversible via existing re-stamp scripts
   (`plan:145-146`).
8. **CLI:** `--dry-run/--live/--collection/--limit/--out-dir/--document-types`
   (same surface as `stamp_qdrant_payload_identity.py:44-53` +
   `strip_reg_section_noise.py`); exit codes 0/1/2 (verification failed).
9. **Post-write verify:** re-scroll, report residue (pattern:
   `strip_reg_section_noise.py --apply --verify`).

---

## 11. Evaluation plan — `evaluation/provision_extraction_eval.py`

Metrics (matching `plan:109-118` and repo conventions):

| Metric | Definition | Convention source |
|---|---|---|
| Boundary P/R/F1 | per `(family, section)` exact match, macro per family (Acts / dotted regulations / EPA all-caps) | `plan:113-114` |
| Exact-span boundary-F1 | char-span exact match per provision | Annotares convention (§4.6) |
| Pk (secondary) | document-level segmentation error | Beeferman via arxiv 2012.03619 |
| Gold-resolution | `_section_from_id` round-trip + `matches_gold` coverage of 97 | `plan:115-117`, `resolution.py:223-238` |
| Noise-stamp rate | bogus section stamps on the strip-noise corpus → target ≈ 0 | `plan:114` |
| Downstream | Recall@K / nDCG deltas on the frozen benchmark (arms A–F) | `metrics.py:131-135,142`, `config.py:39-41` |
| Significance | paired bootstrap 10k, seed 20260811; McNemar α=0.05 | `config.py:44-46`, `metrics.py:396,420` |

**Regression gate:** rules-mode and hybrid-mode both scored on the same
held-out document set; hybrid kept only if it beats rules on boundary-F1 with
non-overlapping bootstrap CIs (`plan:134`); no regression in the 350-test
baseline (`plan:118`). Fixture tests: verbatim payload texts in
`tests/fixtures/provisions/` (`plan:122-123`); rules-mode-with-sklearn-absent
test via monkeypatched import failure (`plan:125`).

---

## 12. Risks and open questions

**Risks**
- Scanned/image PDFs unaffected — extraction operates on already-extracted
  text; OCR is a separate path (`plan:142-143`).
- Unknown acts stay fail-closed; ranges extensible per manifest (`plan:144`).
- Live Qdrant writes are the risky step → dry-run diff first; reversible via
  re-stamp scripts (`plan:145-146`).
- CRF dependency vs numpy/scipy-only venv (§9.3) — the main P1 schedule risk.
- Gold set is small (97) — per-family metrics will have wide CIs; bootstrap
  reporting is mandatory, not optional.
- LLM nondeterminism + no API key — P2 only, review band only (§4.4).
- `build_provisions` text cap (2000 chars, `corpus_ingestion.py:621`) —
  isolated provisions should not inherit the cap silently; decide whether
  `ProvisionRecord.text` is capped or full (open question for kickoff).

**Open questions (for the implementation kickoff)**
1. KG wiring now or later? (plan:138 — still open).
2. May the deploy venv gain `sklearn-crfsuite` (or must the wheel be vendored)?
3. Does the review queue need a UI, or is a JSONL worksheet enough (pattern:
   `evaluation/build_full_review_worksheet.py`)?
4. `ProvisionRecord.text` cap: inherit 2000 or full-span?
5. Should donor datasets (GSMS-B) be used for training, or held out as an
   additional eval family?

---

## 13. Phased roadmap (each phase = one session-sized unit)

### P0 — rules + trivial ML (plan steps 1-3)
- `registry.py`, `ProvisionRecord`, config flags; candidates + features +
  rules-mode disambiguator → isolator; unit fixtures; eval script with
  rules-mode baseline numbers.
- **DoD:** rules-mode boundary P/R/F1 + noise-stamp rate reported on held-out
  docs; fixtures green; rules mode proven with sklearn import monkeypatched to
  fail; dry-run diff report generated for the full corpus (no live writes).

### P1 — sequence model (plan step 4)
- `scripts/train_provision_boundaries.py` (silver labels, §8); LR artifact;
  hybrid mode; CRF if venv decision is yes; re-run eval; keep only if it beats
  rules.
- **DoD:** hybrid beats rules on boundary-F1 with non-overlapping bootstrap
  CIs; artifact + metrics JSON committed; `PROVISION_EXTRACTOR_MODE=hybrid`
  documented in `.env.example`.

### P2 — LLM-assisted review + KG wiring (plan steps 5-6)
- Review-queue workflow (worksheet or UI); OpenRouter adjudication with
  structured output + rules re-validation; ingestion adapter
  (`app/rag/ingestion.py:258,300`); backfill CLI live run; `build_provisions`
  wiring.
- **DoD:** review band adjudicated with 100% rules re-validation; live backfill
  applied with post-write verify clean; KG provisions rebuilt with
  `provision_id` uniqueness intact; benchmark arms re-scored with no
  regression; open questions §12 resolved.

---

## 14. Bibliography

### 14.1 In-repo sources (primary for this project)
- `docs/provision-extraction-plan.md` — scope, architecture, env, gates (full read).
- `app/rag/chunker.py` — payload schema, L4 regex, clause regex.
- `app/rag/legal_sections.py` — act ranges, fail-closed gating.
- `app/rag/ingestion.py` — ingestion integration point.
- `kg/corpus_ingestion.py` — `build_provisions`, instrument registry, write paths.
- `kg/schema.py` — provision_id uniqueness constraints, write guard.
- `kg/payload_identity.py` — idempotent stamp plan/write pattern.
- `legal_paragraph_detection_engine/src/parsers/section_parser.py` — SECTION_PATTERNS, marker-chain semantics.
- `legal_paragraph_detection_engine/src/parsers/clause_parser.py` — clause grammar, special-text patterns.
- `legal_paragraph_detection_engine/src/core/hierarchy.py` — boundary patterns, look-ahead end-line heuristic.
- `evaluation/benchmark.py` — gold grammar, GoldUnit, registry loading.
- `evaluation/resolution.py` — FamilyMap, payload_to_keys, matches_gold.
- `evaluation/config.py`, `evaluation/metrics.py` — gains, bootstrap, McNemar conventions.
- `evaluation/v7_gap_metadata.py` — G1–G12 failure taxonomy.
- `benchmark/gold_provisions_v1.0.json` — 97-record gold standard.
- `scripts/stamp_qdrant_payload_identity.py` — backfill CLI pattern.
- `scripts/backfill_payload_identity.py` — layered source stamps (L4/L5/L7).
- `scripts/backfill_kg_provision_types.py` — provision-type shape map; no-sub-clause evidence.
- `scripts/strip_reg_section_noise.py` — noise corpus + failure-mode evidence.
- `docs/archive/COVERAGE_COMPLETENESS.md` — 82.4% coverage ceiling.
- `.env.example` — OPENROUTER_API_KEY unset; embedding model.

### 14.2 External primary sources (all URLs verified 2026-09-28)
**Sequence labeling / CRF**
- sklearn-crfsuite tutorial — https://sklearn-crfsuite.readthedocs.io/en/latest/tutorial.html
- sklearn-crfsuite API — https://sklearn-crfsuite.readthedocs.io/en/latest/api.html
- sklearn-crfsuite GitHub — https://github.com/TeamHG-Memex/sklearn-crfsuite
- Annotares (CRF/BiLSTM-CRF/BERT/LLM benchmark on German statutory text) — https://arxiv.org/html/2608.03898
- Joint Span Segmentation + Rhetorical Role Labeling (semi-Markov CRF) — https://arxiv.org/abs/2302.06448
- Structural Text Segmentation of Legal Documents (Pk metric, transformer topical change) — https://arxiv.org/pdf/2012.03619
- Semantic Segmentation of Legal Documents via Rhetorical Roles (LEGAL-BERT, BiLSTM-CRF, label shift) — https://aclanthology.org/2022.nllp-1.13.pdf
- YNU-HPCC at SemEval-2023 Task 6 (LEGAL-BERT hierarchical BiLSTM-CRF) — https://aclanthology.org/2023.semeval-1.286.pdf
- LegalSeg (Indian judgments, rhetorical roles, 7k docs) — https://aclanthology.org/anthology-files/pdf/findings/2025_findings-naacl.63.pdf
- The Law of Large Documents (visual cues for section splitting) — https://ar5iv.labs.arxiv.org/html/2107.08128

**Classical ML (repo's chosen stack)**
- scikit-learn LogisticRegression (1.9.1 docs) — https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html

**spaCy / transition-based parsing (evaluated, set aside)**
- spaCy model architectures (TransitionBasedParser) — https://spacy.io/api/architectures
- spaCy EntityRecognizer (whole-entity accuracy caveat) — https://spacy.io/api/entityrecognizer

**Transformers / legal domain**
- LEGAL-BERT (Chalkidis et al., EMNLP-Findings 2020) — https://arxiv.org/abs/2010.02559 ; ACL Anthology — https://aclanthology.org/2020.findings-emnlp.261/
- LEGAL-BERT-small model card (35M params, cc-by-sa-4.0, 12 GB EU/UK/US corpus) — https://huggingface.co/nlpaueb/legal-bert-small-uncased
- CaseHOLD / Custom Legal-BERT (Zheng et al., ICAIL 2021) — https://arxiv.org/abs/2104.08671 ; code — https://github.com/reglab/casehold
- NyayaAnumana & INLegalLlama (COLING 2025) — https://aclanthology.org/2025.coling-main.738/
- ILDC for CJPE (Malik et al., ACL-IJCNLP 2021) — **UNVERIFIED URL** (paper exists per NyayaAnumana citation; no stable URL confirmed)

**Open-source legal NLP tools / data donors**
- blackstone (ICLR&D; PROVISION entity; E&W case law) — https://github.com/iclrandd/blackstone
- nyaayaIN laws of India (Akoma Ntoso XML) — https://github.com/nyaayaIN/indian-laws-akns
- GSMS-B Indian Legal Sections BNS/BNSS/BSA 2023 (1,059 sections) — https://huggingface.co/datasets/GSMS-B/indian-legal-sections-bns-bnss-bsa-2023

**LLM routing / rate limits**
- OpenRouter API credit & rate limits (20 rpm; 50/day free tier; 1000/day ≥10 credits) — https://openrouter.ai/docs/api_reference/limits
- OpenRouter FAQ — https://openrouter.ai/docs/faq
- OpenRouter pricing (25+ free models) — https://openrouter.ai/pricing

**Embeddings (repo's existing stack)**
- Sentence Transformers semantic search docs — https://www.sbert.net/examples/applications/semantic-search/README.html

**Explicitly unverified / not found**
- "LIVECourt" transformer — no such model/paper could be confirmed; do not cite.
- ILDC stable URL — unverified; cite as Malik et al. 2021 (ACL-IJCNLP) without URL.
