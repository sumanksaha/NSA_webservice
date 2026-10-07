
# This documents describes the workflow and how sampling and other part of inspection proceeds

## Seizure

When food safety officer seizes some item, he provides 
	- receipt in form II, regulation 2.3.1
	- order in form III, regulation 2.3.2(1)
	- bond in form IV, regulation 2.3.2(2)

## Sampling

Sampling procedure is described in regulation section 2.4. During sampling, FSO provides
	- notice to business operator in Form V, regulation 2.4.1.3
	- memorendum to analyst in Form VI, regulation 2.4.1.10(i)
	- analyst report should be in Form VIIA, regulation 2.4.2.5
	- FBO can appeal to designated officer  in form VIII, regulation 2.4.6
	- FBO can analyse the food , regulation 2.4.5
When FSO takes a sample,
	- he submit it in lab
	- lab performs the analysis and provides result
		- if result is oK, then no action
		- if result is not OK, then FBO will face penalty
			- after non satisfactory result, FBO can appeal to designated officer in form VIII

---

# Appendix: RAG Evaluation — Making the RAG Module Understand This Workflow

**Date:** 2026-10-07
**Target collection:** `fssai_legal_768` (Qdrant Cloud)

## 1. Evaluation findings (verified against live Qdrant: 12,842 chunks / 27 docs)

1. **This workflow document is NOT in Qdrant.** The existing corpus is only Acts /
   Regulations / Notifications — no workflow/SOP document exists. It must be ingested
   before the RAG can answer workflow questions from it.
2. **Forms II–VIII appear in zero chunks.** Questions like "which form does the FSO
   give the FBO?" can currently only be answered from this workflow doc — there is
   no other source in the corpus.
3. **The cited regulation clauses (2.3.1, 2.4.1.3, …) exist in Qdrant but in
   *different* regulations** (Food Additives / Contaminants / Labelling) — the
   seizure/sampling regulation itself is absent. Naive clause-number expansion
   would inject *wrong* chunks, so any expansion must be document-scoped.
4. **Pipeline gaps found in code:**
   - `.md` is a rejected ingestion extension (`app/document_loader/loader.py:32-36`,
     `app/rag/ingestion.py:31`), pinned by tests
     (`tests/test_document_loader.py:238-242`, `tests/test_ingestion_pipeline.py:415-421`).
   - Markdown headings (`## Seizure` / `## Sampling`) produce no section metadata in
     the chunker (`app/rag/chunker.py:229-286,382-387`).
   - Query understanding has no Form-NN or dotted-regulation-clause parsing
     (`app/rag/retrieval/identifier.py:114-142`); only a partial `procedure` intent
     exists (`retrieval/food_query_understanding.py:303-317`,
     `retrieval/query_classifier.py:43`).
   - No procedure-shaped answer prompt exists; `generation/food_answer.py` has
     intent-conditioned prompts but `procedure` falls through to generic.

## 2. Plan — full pipeline support

### Phase 1: Ingest the workflow document properly
- 1.1 Add `.md` → loader mapping (new `MDLoader` or reuse `TXTLoader`;
  the cleaner preserves `#`/bullets) — `app/document_loader/loader.py:32`.
- 1.2 Add `.md` to the corpus scan set — `app/rag/ingestion.py:31`.
- 1.3 Heading-aware chunk metadata: stamp `## Seizure` / `## Sampling` as
  `section_title` + `hierarchy_level`, propagate to continuation chunks (mirror
  `_propagate_sections`) — `app/rag/chunker.py:252-365`.
- 1.4 Add `workflow` to `VALID_DOCUMENT_TYPES` — `app/rag/metadata_adapter.py:29`.
- 1.5 Ingest with explicit metadata (`title="FSO Seizure & Sampling Workflow"`,
  `type=workflow`) into `fssai_legal_768` via `POST /api/rag/ingest` or
  `scripts/ingest_corpus.py --file`.
- 1.6 Update the two tests pinning `.md` as unsupported.

### Phase 2: Query understanding — recognize workflow questions
- 2.1 Form detector: regex `\bform\s+(ii|iii|iv|v|vi|vii|viiia|viii|ix|x…)\b` →
  parallel lexical identifier arm (mirrors the act+section arm) — new
  `retrieval/form_references.py`, wired at `retrieval/query_understanding.py:34`.
- 2.2 Dotted clause detector: `regulation 2.4.1.3` / bare `2.4.1.3` in query →
  lexical arm; *never* a bare `clause_number` filter (cross-document collision) —
  `retrieval/identifier.py:114`.
- 2.3 Strengthen `procedure` intent patterns ("which form", "what happens after",
  "steps", "seizure", "appeal after") so `QueryType.PROCEDURE` fires and the
  12k-char/10-chunk procedure budget applies —
  `retrieval/food_query_understanding.py:303-317`, `planning/query_planner.py:148`.

### Phase 3: Retrieval — "further research"
- 3.1 Enable **scoped** reference expansion: a retrieved workflow chunk's extracted
  `references` expand only to chunks with matching `clause_number` **within a
  compatible document** — `retrieval/stages.py:62-70`,
  `retrieval/reference_graph.py:330`.
- 3.2 Verify the procedure rerank profile + `legal_identity`/`sufficiency` do not
  deprioritize `document_type=workflow` chunks —
  `retrieval/legal_reranker.py:226,310`, `agent/sufficiency.py:89`.
- 3.3 Verify the planner decomposes compound workflow asks — `planning/query_planner.py`.

### Phase 4: Generation — procedure-shaped answers
- 4.1 Add a **procedure system-prompt branch**: answer as ordered steps; name the
  exact Form number + regulation clause per step; cite `[n]`; abstain on missing
  steps — `generation/prompt_template.py:31`, `generation/food_answer.py:111`.
- 4.2 Confirm context `<source>` labels render workflow title/section —
  `generation/context_builder.py:244`.

### Phase 5: Evaluation
- Add workflow QA pairs (gold answers: seizure → Forms II/III/IV; sampling notice
  → Form V reg 2.4.1.3; analyst memo → Form VI; report → Form VIIA; appeal →
  Form VIII) as `benchmark_workflow_v1.0.jsonl`.
- Run retrieval eval pre/post + `pytest app/rag/ -v`.
- Note: `.env` has `RAG_USE_STUB_LLM=true` — final answer-quality spot checks need
  a real LLM run.

## 3. Caveats
- The underlying seizure/sampling *regulation* is absent from the corpus, so this
  workflow doc is the sole authority; ingesting that regulation PDF later would let
  reference expansion actually resolve.
- Bare `clause_number` filters are unsafe due to cross-document collisions
  (e.g. clause 2.3.1 exists in many regulations). 
