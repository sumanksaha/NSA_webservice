# ADR-0011: Probabilistic photo-evidence assessment

- **Status**: Accepted
- **Date**: 2026-10-15
- **Supersedes/relates to**: ADR-0005 (photo pipeline collapse), ADR-0008 (calibratable confidence)

## Context

A non-sample **Adjudication** carries photographic evidence: the FSO photographs
the premises and the image is stored (Cloudinary by default, R2/B2 otherwise) as
an `Evidence` row, then embedded in the generated petition under
"Photographic Evidence".

That image was inert. Nothing read it. The twelve **Inspection Checklist** items
were still filled in by hand, one yes/no per item, and each `"no"` becomes a
**violation** in the petition and can drive a penalty under FSS Act §§55/56/58/63/64.

The question this ADR settles: *may a model score the photograph, and what may
that score do?*

## Decision

### 1. A decision model scores the photograph; it never decides the violation

`app/inspection/photo_scoring.py` sends the photo to a decision model and records
a probability **per candidate answer**. The scores are **advisory**. Nothing in the
path writes a checklist value; the FSO accepts or overrides on the adjudication
form.

This follows the pattern already set for the FSO advisory, where
`DeterministicActSelector` decides and "the LLM never picks the Act"
(ADR-0003/0006). A probabilistic score that can end a penalty schedule on its own
is not a defensible position on appeal, and the accuracy that would justify it
does not yet exist — there is no labelled corpus of inspection photographs in this
repo to have measured it against.

### 2. Scoring runs before the photo is persisted, and is repeatable afterwards

Two entry points, because scoring is wanted both at capture and later:

- **At capture** — `InspectionPhotoService.upload_adjudication_photo` uploads to
  storage, then scores, then inserts the `Evidence` row **and** the
  `EvidenceAssessment` in one transaction (`db.session.flush()` between them).
  Evidence is therefore never stored without an assessment, and no photo is lost
  to a provider failure: a failed or gated-off pass still commits the row with the
  reason recorded.
- **Later** — `POST /inspection/photos/<evidence_id>/assess` re-scores a stored
  photo, reading it back from Cloudinary/R2 by URL.
  `POST /inspection/<adjudication_id>/photos/assess` does the same for every photo
  of a case, for backfill.

Re-running appends a new row (§5), so a model or prompt change adds evidence
rather than erasing it.

### 3. Only the photo-visible subset is scored

Scored (10): `clean_premise`, `refrigerator_clean`, `proper_attire`,
`proper_covered_utensil`, `date_tag`, `veg_nonveg_separation`,
`food_segregation`, `license_display`, `Expired_item`, `artificial_colour`.

Excluded (2): `Pest_report`, `Water_report`. These ask whether a *document* was
produced. A photograph of a premises cannot answer that, and scoring them would
manufacture violations from nothing — the failure mode most dangerous here,
because it looks exactly like a finding.

The set is **derived**, not re-declared: it is
`CHECKLIST_RULES ∪ SPECIAL_VIOLATION_RULES` minus `PHOTO_UNOBSERVABLE_FIELDS`,
taken from the tables `derive_violations` already reads. Adding a checklist item
gets it scored without editing the scorer.

### 4. One polarity inversion, in one place

The checklist disagrees with itself. A `CHECKLIST_RULES` item is a violation when
its stored value is `"no"`; `artificial_colour` and `Expired_item` are violations
when it is `"yes"` (`SPECIAL_VIOLATION_RULES`).

The scorer normalises to a single `p_violation` that always means *"probability
this violation is present"*, and converts back to a stored form value in
`form_value_for_violation()` — the only place that inversion exists. A scorer that
assumed one polarity would silently invert two of twelve items and propose the
exact opposite finding for them.

### 5. Assessments are append-only, and record what produced them

`evidence_assessment` gains a row per run and never overwrites. What was known at
filing time stays knowable.

Each row stores `provider_model` **and** `resolved_model`. The requested id is
`inception/mercury-decide:free` but the endpoint served
`inception/mercury-decide-20260930`; without both columns a score could never be
reproduced against a dated model. `prompt_version` pins the questions for the same
reason.

### 6. Fail closed, and record the failure

An unparseable response, a provider error, an oversized image, a non-image
content type, or a missing API key produces a failed assessment — every
probability `None`, gate closed — and is **still written to the table**.
`POST .../assess` answers `502` in that case. A broken scoring pass is auditable
rather than invisible, and can never be displayed as a clean premises.

## Model choice: measured, not assumed

Both candidates were run against the same photographs and the same ten candidate
answers (a `noul` P(true) is directly `p_violation`).

| | `inception/mercury-decide:free` | `typesafe/jev-router` |
| --- | --- | --- |
| Well-formed answers | **6/6** | 4/6 |
| Within-image separation | +0.451 | **+0.850** |
| Latency | **1.8–2.4s** | 2.4–5.6s |
| Tokens | **613–680** | 878–2013 |
| Cost | **$0** | ~$0.0004–0.0018 |
| Probability provenance | **read out of the model** | prose the model wrote |

"Within-image separation" is measured on one photograph (a grimy sink) as
mean(target items in frame) − mean(items not in frame), so a model cannot pass by
emitting a constant.

`mercury-decide` wins on reliability, latency, cost, and — decisively — on where
the probability comes from. That last point removes what would otherwise be this
ADR's "not calibrated" caveat: a `noul` is a probability the model emits, not a
number it wrote into a JSON body beside its own prose.

`jev-router` was rejected as the primary despite the better separation, because
it failed to produce a parseable answer in 2 of 6 calls, intermittently returning
an empty body. That is exactly what the fail-closed path exists for, but a scorer
that abstains when there is a real finding to make is not useful.

### The abstention gate

A `noul` **must** answer true/false; a decision model has no "cannot tell".
Ungated, `mercury-decide` fed a synthetic diagram returned
`proper_covered_utensil=0.867` — inventing a violation from a drawing.

So every call carries one extra question first, `premises_photograph`, asking
whether the image even depicts a premises interior. Below `GATE_THRESHOLD` (0.50)
no candidate is read at all. Measured:

| Image | Gate | Verdict |
| --- | --- | --- |
| real grimy premises interior | **0.984** | scored |
| blank document scan | **0.000** | abstained |
| synthetic diagram (was scoring 0.867) | **0.001** | abstained |

Separation **+0.983**, so the threshold sits in a wide empty band rather than near
either cluster. This is how a `noul`-only model gets the "cannot tell" it
structurally lacks, in one extra question inside the same call — the gate and the
candidates cannot disagree about which image was scored.

### Absent is not zero

A probability is stored `None`, never `0.0`, whenever the gate closed, the run
failed, or a field came back without one. `0.0` is a **compliance finding** — it
would read as "the premises were clean". `None` is a gap in the evidence. On a
blank document scan (a water test report photographed as evidence) the correct
output is a recorded abstention; anything else invents a finding.

## Alternatives considered

**A trained multi-label classifier.** Real calibrated probabilities, and the only
option that genuinely earns the right to be authoritative. Rejected *now* because
it needs labelled inspection photographs, of which the repo has none; it is the
follow-on, not the starting point. `p_violation` is the interface it would plug
into, which is why the schema is shaped around a probability rather than a boolean.

**`inception/mercury-2.5` / `mercury-2`.** Both declare `input_modalities:
["text"]` — text-only, so they cannot read a photograph.

**`deepseek/deepseek-v4.1-flash`.** Vision-capable and cheap, but returned
malformed JSON (`Expecting ',' delimiter: line 1 column 635`) on the one probe
where a real finding was available.

**A remote image URL passed straight through.** The decisions endpoint takes
base64 data URLs only — remote URLs are not supported — so the stored Cloudinary
URL is fetched and inlined, bounded by `PHOTO_SCORING_MAX_BYTES`.

**Advisory that never pre-fills.** Rejected as needlessly passive: the scores are
shown per item with a suggested form value, which is what makes them actionable,
while the FSO still holds the pen.

## Consequences

- **Blocking on capture.** A capture-time score adds the decision round-trip
  (~1–2s measured) to the upload. Acceptable now that the call is fast and free;
  if it becomes a problem, move scoring to the QStash dispatch the OCR path already
  uses (`photo_service._dispatch_ocr`) and let the later endpoint write the row.
- **Not yet calibrated against labels.** The probabilities are model-intrinsic, not
  fitted. ADR-0008's isotonic/PAVA machinery is the hook once there is real
  labelled data. Treat `0.70` as a display threshold, not a measured one.
- **Kill switch.** `PHOTO_SCORING_ENABLED` (default **off**) gates the whole path
  on spend; `503` when off.
- **Credentials.** Reuses `AI_ASSISTANT_API_KEY` (the existing OpenRouter key) — no
  new secret to provision. Inception's own
  `https://api.inceptionlabs.ai/v1/decisions` is the alternative endpoint via
  `PHOTO_SCORING_ENDPOINT`.
- **Local-path evidence is rejected.** Inspection photos keep a temp path in
  `Evidence.filepath`, which a remote provider cannot fetch. Scoring is
  adjudication/cloud-backed evidence only, and says so rather than sending a
  broken image that would score as "unobservable".
