# The KG question: what is measured, what is blocked

Written 2026-10-11. No LLM calls. Two facts arrived in the wrong order and need
separating.

## Correction: the earlier "KG has no path" claim was not a measurement

`CURRENT_STAGE_ASSESSMENT.md` concludes that a knowledge graph has no
demonstrated path to binary/soft correctness, on the evidence that retrieval
headroom is +0.004 and corpus word presence is non-discriminative.

**Both numbers came from arm `C_hybrid`, which does not use the KG.**

`evaluation/arms.py` defines the arms: C is dense+sparse hybrid, and only D, E
and F touch the graph. The KG is a *generation-time* stage —
`_generate_apply_kg_context` RRF-fuses expanded provisions into the LLM context
and does not re-rank the retrieval list at all. So the ranked list is
byte-identical with the KG on and off, and nothing measured on arm C can see it.

The claim "the KG adds nothing" was therefore never tested. It should be
restated as: *arm C without the KG shows no headroom, which says nothing about
what the KG adds at generation time.*

## The one existing KG measurement cannot detect a contribution

`evaluation/out/kg_contribution/report.md` reports `kg_injected_gold: 0` —
"retrieval already reached every gold clause on this benchmark".

It measures **5 questions**, all food-intent items (FI001–FI005), and
`retrieval_recall` was **1.0 on all five**. When retrieval already contains the
gold clause, a KG that *adds* clauses cannot be observed contributing one. The
zero is structural, not empirical.

The population where the KG's mechanism has room to act is the 89 model_wrong
failures, where gold-in-top10 is 45/89 (51%). That experiment has never been run.

## The blocker

`NEO4J_URI=neo4j+s://21090cf9.databases.neo4j.io`

* credentials are present in `.env` and well-formed;
* `neo4j.com` resolves from this environment, so DNS works in general;
* the instance host does **not** resolve — `getaddrinfo failed`, same as Qdrant's
  MITM-proxied situation;
* no local Neo4j is running (7687/7474/7473 all closed).

An unreachable graph and a reachable-but-empty graph both produce "the KG adds
nothing", and only the second is a statement about the KG. The harness refuses to
report either as a finding.

## What is built

`evaluation/kg_effect_on_failures.py` measures, per question and offline:

| field | meaning |
|---|---|
| `ctx_cov` | distinctive coverage of the reference by retrieved context |
| `kg_cov` | the same with KG-injected provision text added |
| `delta` | what the KG contributes to the context |

It drives the production `KGContextExpander` unchanged, checks reachability
*before* measuring, and exits with a diagnosis rather than a null result when the
graph is absent. It is ready to run; it needs a reachable Neo4j.

`delta` is not soft/binary correctness — that needs an LLM answer, and the LLM
budget is zero. It is the input to it: if the KG does not raise the context's
distinctive coverage, no model can convert it into correctness.

## Live verification (2026-10-12, 33 LLM calls of a 900-call day budget)

`evaluation/verify_kg_live_llm.py` ran the paired live test the offline number
could not substitute for: same retrieval pool, baseline chunks vs KG-fused
chunks (production `rrf_fuse_chunks`, 5 KG provisions into top-20), live LLM,
frozen scorer. Focused set: the 9 offline crossers + 3 high-delta + 3 zero-delta
controls = 15 QIDs x 2 conditions = 30 calls, plus 1 retry after a 429 and a
2-call replication = 33 total. Resumable cache at
`out/.../reeval_89/kg_live_verify.jsonl`.

| | base | KG-fused |
|---|---|---|
| soft mean (15 pairs) | 0.355 | 0.347 (delta **-0.008**) |
| binary | 1/15 | 1/15 (no change) |
| eliminated | 3/15 | 4/15 (**+1: Q016**) |
| positive soft delta | — | 6/15 |

The offline mean (+0.042, enriched set) did **not** convert. Live mean delta is
indistinguishable from zero; variance is Q&A idiosyncrasy (-0.157 to +0.064).

Two cases carry the whole story, and both were autopsied offline
(`inspect_kg_fusion.py`, 0 LLM calls):

- **Q016 (flip, replicated 2/2).** Gold is s60/s61/**s53**; the KG fused **s53**
  itself. Base never eliminates (0.339, 0.319); KG eliminates both times
  (0.403, 0.352). Mechanistically plausible, not just noise.
- **Q014 (-0.157).** Gold is s59; the KG fused s57/s48/s33/s1/s25 — *none of
  them gold* — as header-only stubs (`PROVISION fssai:s57 — Clause fssai:s57...
  Status: current...`, no statutory text). Five stubs displaced five vector
  chunks in the top-20 and the answer dropped 0.381 -> 0.224.

The mechanism is symmetric and content-free: KG provisions carry **identity**
(section number, instrument, authority) but **no statutory text**. When the
identity names gold, the model gets a pointer it can use (Q016). When it does
not, the stubs evict real context (Q014). This is the same stub-body defect
`ab_kg_fusion` and the `RAG_KG_FUSION` config comment already document
(citation recall 0.31 -> 0.10); it reproduces here as correctness variance,
not gain.

It also explains the offline/live gap: stub headers inject rare-but-contentless
tokens (instrument titles, `Ministry of Law and Justice`) that IDF weighting
treats as distinctive coverage. Offline +0.042 was largely header-token
overlap. Live, a section label is not substance — except as a pointer when it
names the right section.

Largest offline delta, Q076 (+0.44): live delta **-0.002**. Coverage gain with
no correctness gain, exactly as the stub mechanism predicts.

### Next level (2026-10-12, +3 LLM calls): corpus backfill for stub bodies

`provisions_to_retrieved_chunks` gained an opt-in `backfill` index,
`(norm_act, norm_section) -> longest chunk text`, default `None` (existing
callers byte-identical). Two key-format bugs caught offline before spending:
`s57`-vs-`57` needed exact-first + digits-fallback lookup order, else 0/5
stubs fill against a 2176-entry index. Verified: Q014 2->1 stubs (+495 chars),
Q016 3->1 (+695 chars); unfillable remainders are genuine corpus gaps
(Q016 s52 operative text — the known procurement case).

Live (`kgb` cond vs cached bases): Q014 0.381 -> **0.241** (backfill mitigates
plain-kg's 0.224 slightly; off-gold substance still displaces working context
— backfill upgrades the filler, it does not change what was selected).
Q016 0.319 -> **0.397, eliminated** — backfilled-KG eliminates 3/3 alongside
plain-kg 2/2. Verdict unchanged (do not optimise graph for correctness), but
the backfill is now the standing fix for the stub-body defect whenever KG
fusion is used at all: stubs that must displace context should at least carry
text.

## Verdict on the KG question

- **Do not optimise the graph for correctness.** Mean live effect ~0 on an
  enriched set; one replicated flip against one substantial harm; the channel
  carries identity pointers, not substance.
- **Do not expand to a full 89x2 sweep** (~178 calls). It would spend 20% of a
  day budget to tighten the CI around zero. Poor value.
- The one legitimate KG work item is the **stub-body defect**: populating
  `LegalProvision.provision_text` so fused provisions carry statutory text
  instead of headers. That is a KG *content* fix, not structure, and it should
  be re-verified with this same 30-call harness before any claim is made.
- Remaining budget (~867): hold for the gold-labelling check and any
  concision/prompt experiment, which target larger measured gaps.
