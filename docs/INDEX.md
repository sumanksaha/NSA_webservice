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
- [PROMPT_CONDITIONING_IRAC_RESEARCH.md](PROMPT_CONDITIONING_IRAC_RESEARCH.md) — Prompt conditioning for the grounded legal-QA generator: surface inventory, failure-mode map, evidence per option, and a land-and-test path from the current stage
- [RESTAURANT_FSSAI_DATA_ACCESS_RESEARCH.md](RESTAURANT_FSSAI_DATA_ACCESS_RESEARCH.md) — Feasibility of building a licensed-FBO lookup (address + FSSAI licence no. by locality): FoSCoS data-access surface and hardening, Zomato/Swiggy API and ToS constraints, Google Places caching rules, and the dish-matching gap. **Read before scoping any FBO-discovery feature.**
- [SCHEDULE_PLANNING_AGENT_RESEARCH.md](SCHEDULE_PLANNING_AGENT_RESEARCH.md) — Planning an FSO's day from a free-text intent: what already exists, the three blockers (locality is unresolvable, no programmatic diary write, no audit trail), and two build options
- [SCHEDULE_PLANNING_AGENT_IMPLEMENTATION_PLAN.md](./SCHEDULE_PLANNING_AGENT_IMPLEMENTATION_PLAN.md) — Project report for the above: 10 numbered assumptions, a 6-phase breakdown, exact files to create/modify, test plan, risks, and the decisions that gate Phase 5
- [SCHEDULE_PLANNING_AGENT_SPEC.md](./SCHEDULE_PLANNING_AGENT_SPEC.md) — Normative specification for the above: requirement IDs, data contracts, 9 invariants, 8 validation rules, route state machine, error taxonomy, security requirements, and 19 testable acceptance criteria
- [NOTEPAD_LEGAL_RAG_PLAN.md](./NOTEPAD_LEGAL_RAG_PLAN.md) — Turning the Notepad into a legal-Q&A thread: the AI button routes through the legal RAG pipeline (`run_generation_pipeline`) instead of the generic LLM; append-only Answer turns, deterministic query rewriting for follow-ups, user-driven query fragmentation, statement editing with stale-answer markers
- [NOTEPAD_LEGAL_RAG_SPEC.md](./NOTEPAD_LEGAL_RAG_SPEC.md) — Normative specification for the above: requirement IDs (FR/NFR/INV/V/SEC), the `NoteAnswer` data contract, fragmentation and query-rewrite rules, pipeline-result mapping, route state machine, error taxonomy, 16 acceptance criteria, and traceability

## Implemented

- `app/services/premises_resolver.py`, `app/services/diary_planner.py`, `app/schedule.py` — **Duty scheduling** ("Plan a duty"): free-text premises search against the FSSAI registry with road-alias expansion and confusable-road separation, then a confirm step that writes the Monthly Diary slot and one draft Inspection per selected premise. Glossary entries: `CONTEXT.md` §`PremisesResolver`, §`DiaryPlanner`, §Duty scheduling.
