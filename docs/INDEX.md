# NSA Webservice Documentation Index

This is the central index for the NSA Webservice project documentation. Living documentation is organized below.

## Core Documentation

- [README.md](../README.md) - Project overview, architecture, technology stack, and getting started
- [CONTEXT.md](../CONTEXT.md) - Domain & architecture glossary (the single source of truth for terms)
- [AGENTS.md](../AGENTS.md) - Agent skills, triage labels, and domain documentation guidelines
- [CLAUDE.md](../CLAUDE.md) - Additional context for AI agents
- [agent-reference.md](./agent-reference.md) - Full agent/developer reference (architecture, phases, deletion history; formerly root `agents.md`)
- [CODING_STANDARDS.md](../CODING_STANDARDS.md) - Judgement-call rules applied at review time

## Agent configuration

Files the engineering skills read before acting:

- [agents/issue-tracker.md](./agents/issue-tracker.md) - Where issues live and how to file them
- [agents/triage-labels.md](./agents/triage-labels.md) - The five canonical triage roles mapped to tracker labels
- [agents/domain.md](./agents/domain.md) - How to consume `CONTEXT.md` and the ADRs

## Evaluation

- [../evaluation/README.md](../evaluation/README.md) - Schemas for the training/eval cache files under `evaluation/out/`

## Architectural Decisions

- [ADR-0001: Atomic bill issuance](./adr/0001-atomic-bill-issuance.md) - Ensuring bill issuance is one atomic transaction
- [ADR-0002: RetrievalCache Injection Contract](./adr/0002-retrieval-cache-injection.md) - Making the RAG retrieval cache injectable and testable
- [ADR-0003: FSO strategic advisory agent](./adr/0003-fso-strategic-advisory-agent.md) - Game-theory + Talebian advisory layer over the FSO ladder
- [Inspection Module Architecture Deepening](./adr/0004-inspection-module-deepening.md) - Deepening the inspection module (file is `0004-`; its heading still reads ADR-0003 — known numbering collision, see `FSO_ADVISORY_IMPLEMENTATION_PLAN.md` §issues)
- [ADR-0005: Collapse the photo pipeline fork](./adr/0005-photo-pipeline-collapse.md) - One photo seam instead of two parallel pipelines
- [ADR-0006: Statutory floor as admissibility constraint](./adr/0006-advisor-floor-constraint-maximin.md) - Computed zero-sum maximin over admissible acts
- [ADR-0007: Sequential escalation game](./adr/0007-advisor-sequential-escalation-game.md) - Escalation over the FSO ladder
- [ADR-0008: Calibratable confidence](./adr/0008-advisor-calibratable-confidence.md) - Isotonic/PAVA confidence for the FSO advisory
- [ADR-0009: Tiered provision extraction engine](./adr/0009-tiered-provision-extraction-engine.md) - Tiered statutory provision extraction
- [ADR-0010: Shadow verifier threshold calibration](./adr/0010-shadow-verifier-threshold-calibration.md) - Threshold calibration for the shadow verifier

## Additional Resources

### Improvement Plans

- [INSPECTION_IMPROVEMENT_PLAN.md](INSPECTION_IMPROVEMENT_PLAN.md) — Deepening opportunities for the inspection module (3 candidates, prioritized backlog)
- [CODEBASE_REVIEW_2026-09-12.md](CODEBASE_REVIEW_2026-09-12.md) — Full two-axis code review (Standards + Spec), findings, remediation log, and open follow-ups
