# ENRICHMENT SCHEMA — Versioned, Additive, Provenance-Backed

> **Phase 2 deliverable.** Defines the enrichment record attached to each
> existing chunk. Follows the audit's constraints: the **original chunk text is
> immutable**, every field is **sparse** (only what the chunk supports),
> **explicit about provenance**, **versioned**, and **backward compatible**
> (never alters the §5.1 payload or chunk IDs).

---

## 1. Principles

1. **Immutability of evidence** — `original_text` (the payload `chunk_text`)
   is never modified, regenerated, or replaced. Enrichment lives **beside**
   it.
2. **Sparse by default** — a field is absent/`null` unless the chunk supports
   it. We optimise for *legally useful retrieval improvement per unit of
   complexity/memory/cost/hallucination risk*, not for maximum metadata.
3. **Every field has provenance** — each value records *how* it was obtained:
   `deterministic` (rule), `llm`, `existing_payload`, or `unknown`; plus
   `confidence` (0–1) and, where relevant, `evidence_span` (offsets into the
   original text).
4. **Explicit / Inferred / Unknown** — LLM-produced values must be tagged one
   of `explicit` (directly stated), `inferred` (reasonably implied),
   `unknown` (cannot be determined — and legal metadata prefers `unknown`
   over guessing).
5. **Versioned** — every enrichment record carries `enrichment_version`;
   schema changes bump it and old records remain interpretable.
6. **Authority hierarchy** — original text > existing verified payload
   metadata > LLM enrichment. Enrichment never becomes a source of law.

---

## 2. The enrichment record (v1.0)

```json
{
  "enrichment_version": "1.0",
  "chunk_id": "0000003d-7a84-4b0f-9f63-d8d6b4c7aa05",
  "original_text": "3.04 All processing tables...",          // mirror of payload chunk_text (never edited)
  "original_sha256": "…",                                     // integrity check against payload content_hash
  "status": "ENRICHED",                                       // PENDING|PROCESSING|ENRICHED|VALIDATED|FAILED|SKIPPED
  "legal_document": {
    "title": {"value": "…", "source": "existing_payload", "confidence": 0.99, "provenance": "payload.document_title"},
    "document_type": {"value": "regulation", "source": "existing_payload", "confidence": 0.95},
    "authority": {"value": "FSSAI", "source": "existing_payload"},
    "jurisdiction": {"value": "India", "source": "existing_payload"},
    "effective_date": {"value": "2011-08-05", "source": "existing_payload"},
    "status": {"value": null, "source": "unknown"}            // is_current / consolidated status
  },
  "legal_location": {                                         // deterministic-first attribution
    "act": {"value": "Food Safety and Standards Act, 2006", "source": "deterministic", "confidence": 0.8},
    "chapter": {"value": null, "source": "unknown"},
    "section": {"value": "32", "source": "deterministic", "evidence_span": [12, 28]},
    "subsection": {"value": "(1)", "source": "deterministic"},
    "regulation": {"value": null, "source": "unknown"},
    "rule": {"value": null, "source": "unknown"},
    "schedule": {"value": null, "source": "unknown"},
    "annexure": {"value": null, "source": "unknown"}
  },
  "entities": [
    {"name": "Food Safety and Standards Authority of India", "type": "authority",
     "source": "existing_payload", "confidence": 0.9}
  ],
  "legal_concepts": [
    {"concept": "improvement notice", "source": "llm", "kind": "explicit",
     "confidence": 0.85, "evidence_span": [120, 138]}
  ],
  "obligations": [
    {"actor": "Food Business Operator", "action": "comply with improvement notice",
     "source": "llm", "kind": "explicit", "confidence": 0.9, "evidence_span": [90, 140]}
  ],
  "prohibitions": [],
  "permissions": [],
  "powers": [],
  "duties": [],
  "conditions": [],
  "exceptions": [],
  "offences": [],
  "penalties": [],
  "procedures": [],
  "cross_references": [
    {"target": "Section 32", "target_chunk_id": "…", "resolved": true,
     "relation": "REFERS_TO", "source": "deterministic",
     "confidence": 0.95, "evidence_span": [200, 214]}
  ],
  "applicability": [],
  "temporal_information": [],
  "retrieval_keywords": ["improvement notice", "comply", "FBO"],
  "synonyms": [
    {"term": "FBO", "variants": ["Food Business Operator"], "source": "deterministic"}
  ],
  "question_types": ["procedural", "obligation"],
  "retrieval_summary": {
    "value": "Requires food business operators to comply with improvement notices.",
    "source": "llm", "kind": "explicit", "confidence": 0.8
  },
  "confidence": 0.85,                                        // overall enrichment confidence
  "evidence_spans": [[90, 140]],                             // aggregated evidence spans
  "provenance": {
    "deterministic_pass": "1.0",
    "llm_model": "poolside/laguna-s-2.1:free",
    "llm_prompt_version": "enrich-v1",
    "llm_used": true,
    "processed_at": "2026-08-10T12:00:00Z"
  }
}
```

### Field rules

* Every value object is `{value, source, confidence, evidence_span?, kind?,
  provenance?}` — never a bare value (auditability).
* `source ∈ {deterministic, llm, existing_payload, unknown}`.
* `kind ∈ {explicit, inferred, unknown}` for LLM-produced values.
* `evidence_span` = `[start, end]` character offsets **into `original_text`**
  (never invented when `source=unknown`).
* `cross_references[].target_chunk_id` is **only** set when Phase 6 resolution
  succeeded against the actual chunk index; unresolved references stay
  `resolved: false` with `target_chunk_id: null` — never guessed.
* `retrieval_summary` / `retrieval_keywords` / `synonyms` / `question_types`
  are retrieval aids and are **never** presented as legal evidence (see Phase 13).
* `synonyms` = legally meaningful terminology variants (e.g. FBO ↔
  "Food Business Operator", licence ↔ license) — deterministic terms only;
  LLM synonyms must carry evidence spans.
* **Page numbers**: the §5.1 payload carries no page field (audit
  `missing_page_info` = 100 %). Pages are only obtainable from the PDF
  provenance layer and are out of scope for chunk enrichment — recorded
  here so the gap is explicit rather than silent.

---

## 3. Legal ontology for the graph layer (controlled)

Node labels and stable IDs (matching the task's controlled-ontology
requirement; used by both the JSON knowledge graph and any future Neo4j):

```
Act        -> ACT:FSSAI_2006
Regulation -> REG:FSSAI:<slug>:<year>
Rule       -> RULE:FSSAI:<slug>:<year>
Section    -> SECTION:FSSAI_2006:32
Subsection -> SUBSECTION:SECTION:FSSAI_2006:32:(1)
Provision  -> PROVISION:<chunk_id>
Authority  -> AUTHORITY:FSSAI
FBO        -> ENTITY:FBO:<name-normalized>
FSO        -> ENTITY:FSO:<name-normalized>
Offence / Penalty / Obligation / Condition / Exception / Procedure / LegalConcept
           -> CONCEPT:<type>:<normalized-label>
```

Relationship types (with `source_chunk_id`, `target_chunk_id`,
`relationship_type`, `confidence`, `evidence`, `provenance`):

```
REFERS_TO  · UNDER · IMPLEMENTS · PART_OF · IMPOSES · PROHIBITS · PERMITS
CREATES · SPECIFIES · HAS_EXCEPTION · APPLIES_TO · SUBJECT_TO
```

Relationships are only created when evidence exists (cross-reference
resolution or explicit LLM extraction with evidence span) — never from
superficial word overlap.

---

## 4. Storage design (enrichment store)

The DB registry is empty and Qdrant payloads must stay lean, so enrichment is
persisted in a **dedicated SQLite enrichment store** (no new Qdrant schema
until Phase 9 proves benefit; the existing vector index stays untouched).

```sql
-- instance/enrichment.db  (path configurable via ENRICHMENT_DB_PATH)
CREATE TABLE enrichment (
    chunk_id           TEXT PRIMARY KEY,          -- payload chunk_id
    enrichment_version TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'PENDING',  -- see statuses
    data               TEXT NOT NULL,             -- the v1.0 record (JSON)
    original_sha256    TEXT NOT NULL,             -- integrity check
    confidence         REAL,
    llm_used           INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    error              TEXT
);

CREATE TABLE checkpoint (
    batch_id        TEXT PRIMARY KEY,
    last_chunk_id   TEXT,
    status          TEXT NOT NULL,                -- RUNNING|COMPLETE|FAILED
    processed       INTEGER NOT NULL DEFAULT 0,
    enriched        INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    skipped         INTEGER NOT NULL DEFAULT 0,
    batch_size      INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE TABLE cross_reference (
    source_chunk_id    TEXT NOT NULL,
    target_chunk_id    TEXT NOT NULL,
    relation           TEXT NOT NULL,
    confidence         REAL NOT NULL,
    evidence           TEXT NOT NULL,             -- citation string in source
    provenance         TEXT NOT NULL,             -- deterministic|llm
    PRIMARY KEY (source_chunk_id, target_chunk_id, relation)
);

CREATE TABLE resource_usage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    batch_id     TEXT,
    peak_ram_mb  REAL,
    avg_ram_mb   REAL,
    batch_size   INTEGER,
    processed    INTEGER,
    failed       INTEGER,
    retries      INTEGER,
    duration_s   REAL,
    recorded_at  TEXT NOT NULL
);
```

* **Checkpointing (Phase 11)**: every batch writes a `checkpoint` row +
  per-chunk `enrichment` rows with `status ∈ {PENDING, PROCESSING, ENRICHED,
  VALIDATED, FAILED, SKIPPED}`. Restart continues from the last committed
  `last_chunk_id`; validated chunks are never reprocessed.
* **Memory safety (Phase 10)**: the pipeline streams chunks from Qdrant
  (`scroll`, batch 50–100), processes one batch at a time, writes to the
  store, and drops the batch objects — peak RAM stays in the low hundreds of
  MB. Reports `reports/resource_usage.json` is produced from
  `resource_usage` rows.
* **Backward compatibility**: the store is keyed by existing `chunk_id`;
  `enrichment_version` allows schema evolution; nothing in the payload or DB
  registry changes.

---

## 5. Deliverables touched by this schema

| Deliverable | Produced by |
| --- | --- |
| `reports/enrichment_progress.json` | checkpoint aggregation |
| `reports/resource_usage.json` | resource_usage aggregation |
| `reports/evaluation_baseline.json` / `reports/evaluation_enriched.json` | Phase 14 harness |
| `reports/ablation_results.json` | Phase 15 harness |
| `knowledge_graph.json` (regenerated) | enrichment + graph build |

---

## 6. Validation invariants (Phase 12 — automated)

1. `data` parses as JSON against the v1.0 schema (JSON Schema).
2. `chunk_id` exists in the source index; `original_sha256` matches the
   payload `content_hash`.
3. `original_text` equals the payload `chunk_text` (immutability).
4. No invented legal values: any `section`/`penalty`/`authority`/
   `obligation`/`exception`/date/applicability with `kind=explicit` must have
   a non-empty `evidence_span`; values with `source=llm` and no evidence are
   downgraded to `unknown`.
5. `confidence ∈ [0, 1]`; `evidence_span` offsets within `[0, len(text)]`.
6. `cross_references[].target_chunk_id` only when `resolved: true` and the
   target exists in the index.
7. No duplicate relationships/entities (store constraints + validation).
