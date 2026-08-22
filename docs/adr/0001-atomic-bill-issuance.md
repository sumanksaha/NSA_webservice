# ADR-0001: Atomic bill issuance

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Architecture review session (Candidate 3, grilling loop)

## Context

Before this decision, issuing a bill performed **two separate database
commits**: the `Bill` row was committed first, then `mark_samples_as_billed()`
committed the `billed` flags and `BillSample` links in a second transaction.
A crash or error between the two commits left a persisted Bill whose Samples
were still marked unbilled — those Samples remained billable, so a retry could
double-bill them.

PDF generation (via QStash) and Sheets/Airtable sync run *after* persistence
and are deliberately non-blocking / recoverable: a missing PDF can be
regenerated from the persisted Bill.

## Decision

Bill issuance is **one atomic transaction**: the `Bill` row, the `billed` flags
on its Samples, and their `BillSample` links are committed together or not at
all. Sync and PDF dispatch happen strictly after that commit and never roll it
back. When PDF dispatch fails after a successful commit, the result reports the
failure *with* the persisted `bill_id`, so callers can distinguish "bill exists,
PDF missing" from "nothing happened".

## Consequences

- The invariant **no Bill exists unless its Samples are marked billed** is
  enforceable and testable at the issuance interface.
- A conflict (`StaleDataError`) now rolls back the whole issuance instead of
  only the first phase.
- Historical data written by the old two-phase path may contain Bills without
  billed Samples; no migration is planned (the window was tiny), but audits
  should not assume the invariant held before 2026-08-22.
- Anyone reading pre-change git history will see two commits per issuance;
  that pattern is superseded, not correct.
