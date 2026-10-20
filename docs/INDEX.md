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
- [../evaluation/MODEL_SWAP_FREE_PLAN.md](../evaluation/MODEL_SWAP_FREE_PLAN.md) - Two-stage (N=10 / N=40) plan + run log for replacing the retiring laguna-s-2.1:free (minimax-m3.1-flash provisional pick, nemotron-3.5-super-vl, inkling-small, gemma-4-26b-a4b-it) plus the cost-gated paid candidate z-ai/glm-5.3-flash (measured $0.00075/call), the costed-and-probed frontier pair claude-sonnet-5.5 / gpt-6.1-sol (both truncate at `max_tokens=1024`; run declined), and a Q052 five-way comparison with scorer-artifact notes; **§11 delivery findings (2026-10-19): both finalists return `content=null` with 1024/1024 reasoning tokens (`finish=length`), so `llm_client.py:256-261` serves truncated CoT as the answer - minimax reaches its own Step 2 in only 3/40**; §13 is the resulting P0-P3 delivery backlog (answer channel, 30 s prod vs 60 s eval timeout, targeted-retry second pass, latency/cost telemetry); **§11.7 (2026-10-20): minimax rejects reasoning-off with HTTP 400 ("Reasoning is mandatory for this endpoint"), so a thinking budget (`reasoning.max_tokens`) is the only lever**; **§12 complete: 150/150 paired, minimax +0.0129 (p=0.0013), concentrated in the failure cohort and carried by 6 questions; glm +0.0073 (p=0.127)**; **§13.1: P0 answer channel implemented behind `RAG_LLM_MAX_TOKENS` / `RAG_LLM_REASONING_MAX_TOKENS` / `RAG_LLM_TIMEOUT_S` / `RAG_LLM_REASONING_FALLBACK`, with a 26-test guardrail in `tests/test_llm_answer_channel.py` - and the must-hold veto partly reverses under real answers, so it needs 5-sample re-measurement**; QID samples + laguna fill list in `evaluation/model_swap_free_samples.json`
- [specs/experiment_e2_query_type_directives_spec.md](./specs/experiment_e2_query_type_directives_spec.md) - Spec for Experiment E2 (query-type conditioned prompts vs the Experiment B control arm): the single experimental variable, oracle routing, grader-weighted primary + McNemar exact on the paired binary, token-overlap secondary, power/MDE, and the canonical query-type registry that replaced five divergent vocabularies
- [../Evaluator_V4_Binary_Ruler_Design_Review.md](../Evaluator_V4_Binary_Ruler_Design_Review.md) - Design review of the binary-correctness ruler (evaluator v1→v3 lineage, measured constraints, v4 CREDIT/CLAIM design, acceptance criteria)
- [../Ruler_Fix_Workflow.md](../Ruler_Fix_Workflow.md) - Executable workflow (WF-0…WF-9) to build ruler v4 and eliminate the G1–G11 measurement gaps, with gates, 0-LLM sequencing, and the WF-9 composite index (reporting layer only)

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
- [ADR-0011: Probabilistic photo-evidence assessment](./adr/0011-photo-evidence-assessment.md) - A vision model scores a premises photo per checklist item; the score is advisory and the FSO confirms
- [ADR-0012: Shared RAG model runtime](./adr/0012-shared-rag-model-runtime.md) - One process-wide cache for sentence-transformer / cross-encoder weights across collections
- [ADR-0013: Qdrant construction/filter seam](./adr/0013-qdrant-construction-filter-seam.md) - One client-construction and filter rule; point → `RetrievedChunk` conversion lives in `result.py`

## Additional Resources

### Improvement Plans

- [INSPECTION_IMPROVEMENT_PLAN.md](INSPECTION_IMPROVEMENT_PLAN.md) — Deepening opportunities for the inspection module (3 candidates, prioritized backlog)
- [RAG_ARCHITECTURE_IMPROVEMENT_PLAN.md](RAG_ARCHITECTURE_IMPROVEMENT_PLAN.md) — Deepening pass over `app/rag/`: shared model runtime, Qdrant construction/filter seam, cycle removal, measured smells, and remaining backlog
- [CODEBASE_REVIEW_2026-09-12.md](CODEBASE_REVIEW_2026-09-12.md) — Full two-axis code review (Standards + Spec), findings, remediation log, and open follow-ups
- [PROMPT_CONDITIONING_IRAC_RESEARCH.md](PROMPT_CONDITIONING_IRAC_RESEARCH.md) — Prompt conditioning for the grounded legal-QA generator: surface inventory, failure-mode map, evidence per option, and a land-and-test path from the current stage
- [RESTAURANT_FSSAI_DATA_ACCESS_RESEARCH.md](RESTAURANT_FSSAI_DATA_ACCESS_RESEARCH.md) — Feasibility of building a licensed-FBO lookup (address + FSSAI licence no. by locality): FoSCoS data-access surface and hardening, Zomato/Swiggy API and ToS constraints, Google Places caching rules, and the dish-matching gap. **Read before scoping any FBO-discovery feature.**
- [SCHEDULE_PLANNING_AGENT_RESEARCH.md](SCHEDULE_PLANNING_AGENT_RESEARCH.md) — Planning an FSO's day from a free-text intent: what already exists, the three blockers (locality is unresolvable, no programmatic diary write, no audit trail), and two build options
- [SCHEDULE_PLANNING_AGENT_IMPLEMENTATION_PLAN.md](./SCHEDULE_PLANNING_AGENT_IMPLEMENTATION_PLAN.md) — Project report for the above: 10 numbered assumptions, a 6-phase breakdown, exact files to create/modify, test plan, risks, and the decisions that gate Phase 5
- [SCHEDULE_PLANNING_AGENT_SPEC.md](./SCHEDULE_PLANNING_AGENT_SPEC.md) — Normative specification for the above: requirement IDs, data contracts, 9 invariants, 8 validation rules, route state machine, error taxonomy, security requirements, and 19 testable acceptance criteria
- [NOTEPAD_LEGAL_RAG_PLAN.md](./NOTEPAD_LEGAL_RAG_PLAN.md) — Turning the Notepad into a legal-Q&A thread: the AI button routes through the legal RAG pipeline (`run_generation_pipeline`) instead of the generic LLM; append-only Answer turns, deterministic query rewriting for follow-ups, user-driven query fragmentation, statement editing with stale-answer markers
- [NOTEPAD_LEGAL_RAG_SPEC.md](./NOTEPAD_LEGAL_RAG_SPEC.md) — Normative specification for the above: requirement IDs (FR/NFR/INV/V/SEC), the `NoteAnswer` data contract, fragmentation and query-rewrite rules, pipeline-result mapping, route state machine, error taxonomy, 16 acceptance criteria, and traceability
- [PWA_IMPLEMENTATION.md](PWA_IMPLEMENTATION.md) — Progressive Web App rollout guide (config flag, assets, security, staging→production rollout)
- [OBSERVABILITY_SENTRY_PROMETHEUS_RESEARCH.md](OBSERVABILITY_SENTRY_PROMETHEUS_RESEARCH.md) — Where Sentry and Prometheus pay off in this codebase, and the phased plan to wire them in (README Level 7). **Read before starting the observability work.**
- [OBSERVABILITY_SENTRY_PROMETHEUS_SPEC.md](./OBSERVABILITY_SENTRY_PROMETHEUS_SPEC.md) — Normative specification for the above: decisions D1–D11, 50 functional requirements, the metric and label contracts (bounded allowlists), `/metrics` decision table, PII/security requirements, 24 acceptance criteria, and traceability

## Implemented

- `app/observability/` — **Observability**: error tracking (Sentry) with one guarded `init_sentry()` call site in `create_app()`, a `before_send` scrubber (request bodies and cookies dropped; firm names, licence numbers, addresses and query text redacted), and request correlation bound before the auth gates — a blank `SENTRY_DSN` is a true off switch. Plus the Prometheus surface: all metrics constructed in `metrics.py` under allowlisted labels, and `GET /metrics` on the FastAPI edge (bearer-guarded, fail-closed) with HTTP instrumentation on both surfaces counting each request exactly once. Glossary entry: `CONTEXT.md` §`Observability`. See [OBSERVABILITY_SENTRY_PROMETHEUS_SPEC.md](./OBSERVABILITY_SENTRY_PROMETHEUS_SPEC.md).
- `app/services/premises_resolver.py`, `app/services/diary_planner.py`, `app/schedule.py` — **Duty scheduling** ("Plan a duty"): free-text premises search against the FSSAI registry with road-alias expansion and confusable-road separation, then a confirm step that writes the Monthly Diary slot and one draft Inspection per selected premise. Glossary entries: `CONTEXT.md` §`PremisesResolver`, §`DiaryPlanner`, §Duty scheduling.
- `app/models/auth.py`, `migrations/versions/widen_record_audit_record_id.py`, `app/audit_hooks.py` — **Monthly Diary audit-record overflow fix**: `record_audit.record_id` widened `VARCHAR(50)`→`VARCHAR(255)` so `WorkDiaryEntry`'s composite-key audit identifier no longer aborts the diary save's transaction on Postgres, plus a logged length guard in the audit writer. See [RECORD_AUDIT_RECORD_ID_OVERFLOW_RESEARCH.md](./RECORD_AUDIT_RECORD_ID_OVERFLOW_RESEARCH.md).
- `app/static/icons/`, `app/static/src/sw.js`, `app/templates/offline.html` — **Progressive Web App** (PWA): installable PWA (manifest, root-scoped service worker, offline fallback) gated by `ENABLE_PWA`. See [PWA_IMPLEMENTATION.md](PWA_IMPLEMENTATION.md).
