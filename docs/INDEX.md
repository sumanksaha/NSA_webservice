# NSA Webservice Documentation Index

This is the central index for the NSA Webservice project documentation. Living documentation is organized below.

## Core Documentation

- [README.md](../README.md) - Project overview, architecture, technology stack, and getting started
- [CONTEXT.md](../CONTEXT.md) - Domain & architecture glossary (the single source of truth for terms)
- [AGENTS.md](../AGENTS.md) - Agent skills, triage labels, and domain documentation guidelines
- [docs/agent-reference.md](./agent-reference.md) - Full agent/developer reference (architecture, phases, deletion history; formerly root `agents.md`)
- [CLAUDE.md](../CLAUDE.md) - Additional context for AI agents

## Architectural Decisions

- [ADR-0001: Atomic bill issuance](./adr/0001-atomic-bill-issuance.md) - Ensuring bill issuance is one atomic transaction
- [ADR-0002: Retrieval2: RetrievalCache Injection Contract](./adr/0002-retrieval-cache-injection.md) - Making the RAG retrieval cache injectable and testable

## Additional Resources

See the [archive](./archive/) for historical research, plans, and investigation documents.

### Improvement Plans

- [INSPECTION_IMPROVEMENT_PLAN.md](INSPECTION_IMPROVEMENT_PLAN.md) — Deepening opportunities for the inspection module (3 candidates, prioritized backlog)
- [CODEBASE_REVIEW_2026-09-12.md](CODEBASE_REVIEW_2026-09-12.md) — Full two-axis code review (Standards + Spec), findings, remediation log, and open follow-ups
