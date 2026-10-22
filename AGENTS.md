## Agent skills

### Issue tracker

GitHub issue tracker. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles mapped to this tracker's label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` at repo root with `docs/adr/` for architectural decisions. See `docs/agents/domain.md`.

### Docs index

Every other doc is listed in `docs/INDEX.md`. Check it before writing a new one.

### Evaluation artifacts

Schemas for the training/eval cache files: `evaluation/README.md`. Everything under `evaluation/out/` is gitignored and the file tools refuse it — read those paths via the terminal.

### Recent additions (2026-10)

- `app/observability/` — Sentry + Prometheus (issues #71–#72)
- `app/static/src/sw.js`, `app/static/icons/`, `app/templates/offline.html` — PWA (issue #70)
- `app/inspection/photo_scoring.py` — probabilistic photo-evidence assessment (ADR-0011)
- `app/shared/llm_client.py` — LLM kill switch + call budget, TLS enforcement
- `app/rag/model_runtime.py`, `app/rag/query_type_registry.py` — shared RAG model runtime and canonical query-type vocabulary (C1, C7)
- `evaluation/` — evaluator-v4 ruler, text-fallback gold resolution, decision-pathway scoring, top-lawyer rubric
