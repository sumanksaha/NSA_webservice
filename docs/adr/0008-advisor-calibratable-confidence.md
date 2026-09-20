# ADR-0008: Calibratable confidence for the FSO advisory

- **Status:** Accepted (implemented 2026-09-20: `app/rag/advisor/confidence.py`
  + `app/rag/evaluation/advisor_metrics.py` + selector seam; default behaviour
  unchanged)
- **Date:** 2026-09-20
- **Context:** RAG subsystem (`app/rag/advisor/`), extending
  [ADR-0003](0003-fso-strategic-advisory-agent.md) (its `confidence` payload
  field is unchanged in shape)
- **Related terms in `CONTEXT.md`:** `Act`

## Context

V1 shipped a fixed evidence heuristic as `confidence` (base 0.7 + 0.1 per
extra anchor, cap +0.2, +0.1 with lab report, capped at 1.0). That number
is an *evidence score*, not a calibrated probability: no outcome labels
(adjudication upheld / FBO complied without successful contest) exist to
fit against, so no calibration claim can be made — yet the field is named
`confidence` and downstream consumers may read it as a probability.

## Decision

**1. Version the heuristic, don't change it.**

`HeuristicConfidence` (`param_set = "heuristic_v1"`) reproduces the
ADR-0003 formula exactly — the default selector output is bit-identical
to V1. Every assessment carries its `param_set` id in the payload
(`confidence_param_set`), so historical outputs stay reproducible after
future models land.

**2. Seam for a fitted model: isotonic (PAVA) calibration table.**

`IsotonicConfidence` is a drop-in `ConfidenceFn`
(`(n_anchors, lab_report_available) → ConfidenceAssessment`): a monotone
step table over the base model's raw score, fitted by
`fit_isotonic_confidence` (pool-adjacent-violators over `(raw, outcome)`
pairs; exact ties pooled; blocks merged until block means are
nondecreasing). Lookup is the last block with raw ≤ query, clamped to
the end blocks outside the fitted range. Deterministic, serializable
breakpoints, no new dependencies. Requires at least one record (raises
otherwise — fail loudly, like the rest of the seam).

**3. Offline scoring harness for any confidence stream.**

`app/rag/evaluation/advisor_metrics.py` (pure, dependency-free):
`brier_score`, `expected_calibration_error` (equal-width bins, last bin
includes 1.0), `reliability_bins` (empty bins omitted), `abstention_rate`
(share of `fso_act is None` payloads). Out-of-range confidences and empty
streams raise. The math is pinned by hand-derived unit tests on synthetic
streams so any model — `heuristic_v1` or a fitted table — can be
validated the day labels exist.

**4. Outcome-learning loop stays deferred.**

No outcome labels are collected yet; nothing in the graph writes them.
These modules are the fittable seam + the scoring harness the loop will
feed — not the loop itself.

## Consequences

- **Positive:** today's numbers are unchanged (payload contract preserved);
  tomorrow's calibration has a versioned, reproducible home. Pinned by
  `tests/test_advisor_confidence.py` (15 tests).
- **Positive:** mis-scaled scores fail loudly (`ValueError` outside
  [0, 1]) instead of silently "calibrating".
- **Risk / trade-off:** PAVA block x-coordinates are weight-averaged
  means, so step thresholds are approximate for continuous raw scores —
  harmless on the production 4-point raw domain (`0.7/0.8/0.9/1.0`), noted
  here for future continuous base models.
- **Deferred:** outcome-label collection, model registry/persistence, and
  recalibration cadence (the learning loop).
