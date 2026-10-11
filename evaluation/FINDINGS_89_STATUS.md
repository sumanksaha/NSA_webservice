"""Present status of the 89 model_wrong failures — measured live, 2026-10-11.

What was run
------------
Full 150-question sweep against live services, both retrieval arms, one LLM
call per question per arm (300 calls):

* ``evaluation/reeval_89.py --phase retrieval`` — live Qdrant, 0 LLM
* ``evaluation/reeval_89.py --phase generate``  — live LLM, frozen scorer
* ``evaluation/reeval_89.py --phase report``    — 0 LLM

Headline: the scoped structural arm improves retrieval recall substantially and
converts almost none of it into correct answers.

    retrieval, gold in the packed top-10 window
      failing cohort   45/89  ->  56/89   (+12, -1)
      control cohort   36/61  ->  42/61   (+6,  -0)

    end-to-end elimination (frozen scorer, benchmark's own requirements)
      failing cohort   11/89  ->  10/89
      control cohort   14/61  ->  13/61
      control questions regressed by the arm: 3

Why the gain does not convert — three independent measurements
-------------------------------------------------------------
1. **The gate is not the ceiling.** ``concept_gate_reachability`` scores the
   benchmark's own maximally-complete answer through the frozen rule: 59/60 of
   the gold-in-prompt failures are satisfiable. So 0/7 on the causal A/B was a
   model failure, not an unreachable criterion.

2. **The newly-arrived gold is the wrong text.** ``audit_stamp_content``: for
   19 of 34 FSSAI provisions the benchmark has a registered title, the longest
   corpus chunk stamped with that ``section_number`` shares no vocabulary with
   it. Live Qdrant, the six chunks stamped ``63`` are all gazette amendment
   footnotes — *"In section 63, for the words ..."*, *"63. Unit of assessment"*
   (a different statute) — and the phrase *"carrying on a food business
   without"* occurs **0 times** in 12,842 points. Q025's window is the clean
   case: stamp 52 yields ``"52. Penalty for misbranded food."`` while the
   benchmark titles ``fssai:s52`` **"Punitive damages"**.

3. **Recall therefore cannot be the lever.** ``probe_text_vs_stamp_rank``:
   scoring by the provision's operative language instead of its stamp finds a
   better chunk on 0 of 87 failing questions. The text is not in the pool to be
   ranked up.

Where the remaining 78 actually sit
-----------------------------------
``answer_quality_headroom`` on the live scoped run:

* **49 have gold in the window** — evidence-limited by chunk quality, not by
  ranking. 38 miss one or two gold concepts, 11 miss three or more.
* **30 have no gold in the window at all** — evidence-limited outright.
* Mean soft score of the still-failing answers is 0.324, i.e. near-misses with
  gaps, not empty answers. 150 gold concepts go unanswered across the cohort
  (1.69 per question); 25 answers never name the gold authority.

Census of the mirrored corpus (34,745 chunks, 8.2M chars): 27.3% are under 40
characters, 50.8% under 120. Operative-probe hits for "s63 body", "s63 body
alt" and "s52 body" are **0**.

Conclusion
----------
Retrieval ranking has been solved and is no longer the constraint: the scoped
arm is a real +11pp recall win with no control regressions, and it is worth
shipping on those grounds. But the binding constraint is upstream of both
retrieval and prompting — the ingested FSSAI corpus is dominated by amendment
notifications and stubs, with the principal Act's operative text largely
absent. Prompt conditioning cannot recover a provision that is not in the
evidence, and neither can re-ranking.

Two independent work streams follow, and neither is a prompt change:

* **corpus** — re-ingest the FSSAI principal Act so provisions carry their own
  body text, then re-stamp. Every "never_retrieved" and "stub_gold" bucket
  depends on this. Blocked on source PDFs (``RAG_CORPUS_DIR`` is empty, the
  same blocker as P0-2).
* **answer completeness** — for the 49 questions whose evidence *is* present,
  the loss is 1-2 named concepts and an unnamed authority. That is addressable
  by prompt conditioning, and is the only lever that can move the number
  without new corpus.

Do not read the 0/7 causal result as "context cannot help". Read it as: the
context that was added was not the provision the question asked about.

## Negative result: prompt conditioning (tested, not shipped)

A completeness directive (enumerate set members, name the deciding authority,
do not infer through cross-references) was A/B'd against the shipped template
on the same live pools, same packer, same scorer: 300 LLM calls per arm.

    C_hybrid        failure  13/89 -> 12/89   (+3 / -4)   McNemar exact p = 1.000
    C_hybrid        control  13/61 -> 14/61   (+1 / -0)   McNemar exact p = 1.000
    C_hybrid_section failure 12/89 -> 10/89   (+5 / -7)   McNemar exact p = 0.774
    C_hybrid_section control 17/61 -> 15/61   (+2 / -4)   McNemar exact p = 0.688

Nothing clears significance, and mean soft score falls on 3 of 4 cells
(0.336 -> 0.308, 0.388 -> 0.375, 0.394 -> 0.378). Abstentions roughly double on
the failing cohort (5 -> 10 on ``C_hybrid``) — the "say the operative text is
not reproduced" clause pushes the model toward qualifying rather than answering.

**Calibration first.** Two runs of the *unchanged* system gave the identical
headline (12/89 both times) while the gate flipped on **8 of 89** questions
between them, with soft sd 0.122 against a mean delta of 0.024 and only 4/89
soft scores reproducing exactly. McNemar on that pair gives p = 1.000 — the
test cannot distinguish two runs of a system that did not change. So a
candidate delta below that band means nothing, which is where all four cells
land. Reproduce with ``python -m evaluation.score_significance``.

The block was **not** merged into ``prompt_template.py``. Ship nothing that
cannot be distinguished from resampling.

## What would actually move the number

1. **Re-ingest the FSSAI principal Act** (30 questions have no usable evidence;
   27.3% of chunks are under 40 chars; s63/s52 operative text absent entirely).
   Blocked on source PDFs — ``RAG_CORPUS_DIR`` is empty, same as P0-2.
2. **Fix the stamping pipeline** (19/34 provisions carry another statute's text
   or an amendment footnote under their gold ``section_number``). This is what
   makes both the metric and the structural arm chase the wrong chunks, and it
   is a code fix, not a procurement one.
3. Only then revisit prompting: until the operative text is present, the model
   cannot be complete about a provision it cannot see.

## Root cause, located

`legal_paragraph_detection_engine/src/legal_engine.py:298`

```python
section_match = re.search(r"Section\s*(\d+)", text, re.IGNORECASE)
```

That is a **mention** detector, not a **containment** detector. It returns the
first number following the word "Section" anywhere in the paragraph, so
*"(C) In section 63, for the words X, the words Y shall be substituted"* — a
gazette amendment note that *refers to* s63 — is stamped 63. `Chunk.from_paragraph`
(`app/rag/chunker.py:207`) trusts that value for `section_number`, which is the
one field the structural arm filters on and `matches_gold` credits.

Measured on the shipped detectors (`compare_section_detectors`):

| prose class | `_L4_HEADER_RE` correct | `Section NN` correct |
|---|---|---|
| amendment footnotes (8) | 2 | 8 |
| operative marginal notes (5) | 5 | 0 |
| cross-reference fragments (3) | 0 | 3 |

Both rules are stamping, in competition, which is why the corpus is a mixture.
Consequences, all confirmed live: 141 `section_number` values are backed *only*
by amendment prose; `matches_gold` credits those as gold; the structural arm
promotes them (Q015's rank-1 "gold" is *"contravention under section 63 ... fine
which may extend to ten thousand rupees"*).

### Fix options, measured (`gate_fixes`)

| fix | stamps changed | stamped after | gold units unresolved | gold newly lost | poisoned stamps |
|---|---|---|---|---|---|
| shipped | 0 | 15,212 | 2 | 0 | 141 |
| **blank amendment stamps** | **683** | **14,529** | **2** | **0** | **0** |
| own-only-what-you-open-with | 15,097 | 190 | 6 | 4 | 0 |

**Recommended: blank the stamp on amendment notifications.** It is the narrow
fix — it touches only the 683 chunks that both carry a stamp and are amendment
prose — takes the poisoned stamps from 141 to zero, and loses no gold unit,
because no gold unit ever resolved through amendment prose alone.

The `own_only` rule is semantically the most honest (a chunk owns only the
provision it opens with) but blanks continuation chunks that genuinely belong to
a provision, costing 4 gold units. A positional variant that re-derives stamps
from document order (`stamp_allocation`) does eliminate the mislabel entirely
but blanks 85% of chunks.

Pinned by `tests/test_stamp_audit.py` (18 tests): the defect is characterised,
and the fixed stamper's contract is asserted independently of how it is written.

### Implemented

`legal_engine._extract_section_from_text` now applies the narrow fix:

```python
if AMENDMENT_NOTIFICATION_RE.search(body):
    return None                      # refers to a provision, owns none
leading = LEADING_SECTION_RE.match(body)
return leading.group(1) if leading else None
```

`tests/test_section_stamping.py` (28 tests) asserts it against
`LegalParagraphEngine` directly, using corpus strings. The characterisation
tests deliberately still assert what the *old* regex did, so the defect stays
visible and a regression to it fails loudly. 300 tests pass across the engine
unit suite, chunker, stamping and section-scope tests.

`psutil>=5.9` is declared in `pyproject.toml` but was not installed, failing
`test_integration.py::test_memory_usage`. Installed; unrelated to this change.

### Why this alone will not move the failure count

The fix makes the metric honest and stops the arm promoting footnotes. It does
not supply missing text. Expect measured recall to *fall* slightly while
correctness improves — the metric was crediting amendment notes.

## Re-ingest, sized exactly

`size_reingest_exactly` scopes the procurement by testing each gold provision's
**operative phrase** against chunks restricted to the **gold Act**. That
restriction is essential: the mirror holds six statutes, so `section_number ==
63` also matches Companies Act s63 and the Bharatiya Nyaya Sanhita. Two earlier
passes got this wrong — one reported 2 provisions absent by searching the whole
mirror, and a cross-check then found s31's text *was* present, stamped 31,
attributed to FSSAI (*"(1) No person shall commence or carry on any food
business except under a licence."*). A section number is not an identity
without its Act.

| verdict | count | meaning |
|---|---|---|
| PRESENT | 11 | operative text in the corpus |
| STAMP_EXISTS_TEXT_DOES_NOT | 2 | s52, s63 — stamp exists, longest chunks are `'section 52 due, subject to a'` and `'63'` |

**Re-ingest scope: 2 provisions, 2 questions.** Q015 (`fssai:s63`) and Q025
(`fssai:s52`) are the only failing questions whose gold names a provision
absent from the corpus; no control question does. Their live windows are
entirely fragments — Q015's ten gold-stamped chunks include `'63'` (2 chars)
and `'(C) In section 63,'` (19 chars).

**So the re-ingest buys 2 of 89 (2%).** That is much smaller than the earlier
"30 questions have no gold in the window" suggested, and the reason matters:
that 30 counted questions whose *window* lacked gold, which for most of them
means ranking, not a corpus gap. Domain coverage confirms it — every gold family
except `pcra` resolves at 100% (`measure_reingest_scope`), and `pcra` at 50% is
the known OCR-blocked P0-2.

Nothing about a 2-question procurement is worth a source-PDF request on its
own. The remaining 76 are stamping, ranking and generation work, all of which
can proceed without it.

## Corpus remediation (applied)

The code fix governs future ingestion; stored chunks keep the stamps they were
built with. `stamp_corpus_remediation` applies the measured narrow fix to the
live corpus: blank `section_number` on any point that both carries a stamp and
is amendment prose.

Two things went wrong on the first run and both are worth recording.

### The apply was scoped to one collection

`--collection` defaulted to `fssai_legal_768`. The 683 candidates are
**corpus-wide**; only 453 live in FSSAI. The remaining 230 were reported as
"absent from the collection", which was true and misleading — they are in the
other five collections, unremediated. The default is now `all`.

This surfaced only because the script re-fetches every candidate and
re-checks the predicate against the live payload before writing. A tool that
had trusted the cached mirror would have reported "453 of 683 applied" as if
the job were finished.

### A scroll loop that never terminated

Several earlier probes reported point counts *above* their own cap —
`audit_operative_presence` logged 40,062 points for FSSAI against a 40,000
limit. Qdrant returns `offset=None` on the final page; looping on that instead
of stopping re-reads the collection from the start. Corrected counts:

| collection | points | stamped |
|---|---|---|
| fssai_legal_768 | 12,842 | 3,084 |
| env_legal_768 | 8,909 | 2,353 |
| wb_state_legal_768 | 1,884 | 1,766 |
| criminal_legal_768 | 1,260 | 121 |
| commercial_legal_768 | 7,584 | 6,679 |
| animal_legal_768 | 2,266 | 756 |
| **total** | **34,745** | **14,759** |

`collection_census.py` now asserts page termination explicitly and checks
`scrolled == points_count` per collection, so a broken loop fails loudly instead
of inflating a count. With the corrected loop, scrolled matches declared
exactly for all six.

Consequence for earlier numbers: the mirror is a faithful snapshot of all six
collections (34,745 entries, zero drift either way against live), so the
mirror-derived figures stand. The figures that came from **scrolling** —
including FSSAI's size and any phrase count taken over a concatenated scroll
blob — were inflated and should not be quoted. The scoped s52/s63 absence
result is unaffected: it counted chunks restricted to the FSSAI Act, not a
scroll total.

Also: `upsert` is the wrong API here — `PointStruct` requires a vector, and
re-sending vectors risks replacing any that drifted. `set_payload` with
`payload={"section_number": None}` is a payload-only write.

### A third defect: the sidecar was overwritten

Each apply run touches a different subset of collections, and the sidecar was
written as a flat replace. A second run would have orphaned the stamps the
first run erased — 453 chunks un-restorable, which is corruption rather than
rollback. Now merged on `(collection, id)`, with the first run's
top-level-`collection` records normalised to per-record. `tests/test_corpus_remediation.py::test_removal_is_reversible_from_the_sidecar`
was red against the real file before this fix and green after.

### Verified against live Qdrant, all six collections

`verify_remediation_live` re-scrolls everything and re-resolves every gold unit
against live payloads:

| | before | after |
|---|---|---|
| stamped-but-amendment points | 683 | **0** |
| poisoned stamps (amendment-only backing) | 141 | **0** |
| points carrying any stamp | 14,759 | 14,529 |
| gold units unresolved | 2 | **2** |

**VERDICT: CLEAN.** Scroll integrity holds — scrolled == `points_count` for all
six, 34,745 live points, zero drift against the mirror.

Two things worth noting. First, the stamp total falls by exactly 683 to 14,529,
which is the figure `gate_fixes` predicted offline from the mirror — the live
result matches the offline measurement to the unit. Second, **gold resolution is
unchanged**: the same 2 units fail to resolve, `Q034 pcra:s63` and
`Q042 pcra:s63`, both in the OCR-blocked `pcra` family (P0-2). That was the
claim most at risk, since blanking 453 `section_number` fields removes exactly
the field `matches_gold` credits. It holds.

Those 2 unresolved units are *not* the FSSAI s52/s63 provisions. Those resolve
by stamp but fail the operative-phrase test, which is the distinction
`size_reingest_exactly` draws and the one that made the 2-question procurement
figure worth stating separately from this 2-unit figure.
"""
